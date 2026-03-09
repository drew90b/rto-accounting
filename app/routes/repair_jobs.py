import io
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.repair_job import RepairJob
from app.models.payment import Payment
from app.models.unit import Unit
from app.models.customer import Customer
from app.models.enums import BusinessLine, RepairJobType, RepairJobStatus, PaymentMethod
from app.services.repair_service import close_repair_job, record_repair_payment

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _d(val: str) -> Optional[Decimal]:
    try:
        return Decimal(val) if val else None
    except InvalidOperation:
        return None


@router.get("/", response_class=HTMLResponse)
def list_repair_jobs(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    business_line: Optional[str] = None,
    job_type: Optional[str] = None,
):
    query = db.query(RepairJob)
    if status:
        query = query.filter(RepairJob.status == status)
    if business_line:
        query = query.filter(RepairJob.business_line == business_line)
    if job_type:
        query = query.filter(RepairJob.job_type == job_type)
    jobs = query.order_by(RepairJob.open_date.desc()).all()
    return templates.TemplateResponse("repair_jobs/list.html", {
        "request": request,
        "jobs": jobs,
        "status_filter": status or "",
        "business_line_filter": business_line or "",
        "job_type_filter": job_type or "",
        "statuses": [s.value for s in RepairJobStatus],
        "business_lines": [b.value for b in BusinessLine],
        "job_types": [t.value for t in RepairJobType],
    })


@router.get("/export")
def export_repair_jobs(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    jobs = db.query(RepairJob).order_by(RepairJob.job_id).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Repair Jobs"
    headers = [
        "Job ID", "Business Line", "Job Type", "Unit", "Customer",
        "Open Date", "Close Date", "Status", "Labor", "Materials", "Total Billed", "Notes",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for j in jobs:
        ws.append([
            j.job_id,
            j.business_line.value if j.business_line else "",
            j.job_type.value if j.job_type else "",
            j.unit.unit_id if j.unit else "",
            j.customer.full_name if j.customer else "",
            str(j.open_date) if j.open_date else "",
            str(j.close_date) if j.close_date else "",
            j.status.value if j.status else "",
            float(j.labor_amount) if j.labor_amount else 0,
            float(j.materials_amount) if j.materials_amount else 0,
            float(j.total_billed_amount) if j.total_billed_amount else 0,
            j.notes or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=repair_jobs.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_job_form(request: Request, db: Session = Depends(get_db)):
    units = db.query(Unit).order_by(Unit.unit_id).all()
    customers = db.query(Customer).filter(Customer.status == "active").order_by(Customer.full_name).all()
    return templates.TemplateResponse("repair_jobs/form.html", {
        "request": request,
        "job": None,
        "units": units,
        "customers": customers,
        "statuses": [s.value for s in RepairJobStatus],
        "business_lines": [b.value for b in BusinessLine],
        "job_types": [t.value for t in RepairJobType],
    })


@router.post("/new")
def create_job(
    db: Session = Depends(get_db),
    business_line: str = Form(...),
    job_type: str = Form(...),
    open_date: str = Form(...),
    unit_id: str = Form(""),
    customer_id: str = Form(""),
    close_date: str = Form(""),
    status: str = Form("open"),
    labor_amount: str = Form("0"),
    materials_amount: str = Form("0"),
    total_billed_amount: str = Form("0"),
    notes: str = Form(""),
):
    from datetime import date
    job = RepairJob(
        business_line=business_line,
        job_type=job_type,
        open_date=date.fromisoformat(open_date),
        unit_id=int(unit_id) if unit_id else None,
        customer_id=int(customer_id) if customer_id else None,
        close_date=date.fromisoformat(close_date) if close_date else None,
        status=status,
        labor_amount=_d(labor_amount) or Decimal("0"),
        materials_amount=_d(materials_amount) or Decimal("0"),
        total_billed_amount=_d(total_billed_amount) or Decimal("0"),
        notes=notes or None,
    )
    db.add(job)
    db.flush()
    job.job_id = f"J-{job.id:04d}"
    db.commit()
    return RedirectResponse(url=f"/repair-jobs/{job.id}/edit?msg=Job+saved", status_code=303)


@router.get("/{job_id}/close", response_class=HTMLResponse)
def close_job_form(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.query(RepairJob).filter(RepairJob.id == job_id).first()
    if not job:
        return RedirectResponse(url="/repair-jobs/")
    if job.status.value == "complete":
        return RedirectResponse(url=f"/repair-jobs/{job_id}/invoice")
    job_type = job.job_type.value if hasattr(job.job_type, "value") else job.job_type
    return templates.TemplateResponse("repair_jobs/close_job.html", {
        "request": request,
        "job": job,
        "job_type": job_type,
    })


@router.post("/{job_id}/close")
def do_close_job(
    job_id: int,
    db: Session = Depends(get_db),
    labor_amount: str = Form("0"),
    materials_amount: str = Form("0"),
    total_billed_amount: str = Form("0"),
):
    job = db.query(RepairJob).filter(RepairJob.id == job_id).first()
    if not job:
        return RedirectResponse(url="/repair-jobs/", status_code=303)

    job.labor_amount = _d(labor_amount) or Decimal("0")
    job.materials_amount = _d(materials_amount) or Decimal("0")
    job.total_billed_amount = _d(total_billed_amount) or Decimal("0")

    close_repair_job(job, db)
    db.commit()
    return RedirectResponse(url=f"/repair-jobs/{job_id}/invoice?msg=Job+closed", status_code=303)


@router.get("/{job_id}/record-payment", response_class=HTMLResponse)
def record_payment_form(job_id: int, request: Request, db: Session = Depends(get_db)):
    from datetime import date
    job = db.query(RepairJob).filter(RepairJob.id == job_id).first()
    if not job:
        return RedirectResponse(url="/repair-jobs/")
    payments = db.query(Payment).filter(Payment.repair_job_id == job_id).all()
    total_paid = sum((p.amount or Decimal("0")) for p in payments)
    total_billed = Decimal(str(job.total_billed_amount)) if job.total_billed_amount else Decimal("0")
    remaining = total_billed - total_paid
    return templates.TemplateResponse("repair_jobs/record_payment.html", {
        "request": request,
        "job": job,
        "remaining_balance": remaining,
        "methods": [m.value for m in PaymentMethod],
        "today": date.today().isoformat(),
    })


@router.post("/{job_id}/record-payment")
def do_record_payment(
    job_id: int,
    db: Session = Depends(get_db),
    payment_date: str = Form(...),
    amount: str = Form(...),
    payment_method: str = Form(...),
    entered_by: str = Form(""),
    notes: str = Form(""),
):
    from datetime import date
    job = db.query(RepairJob).filter(RepairJob.id == job_id).first()
    if not job:
        return RedirectResponse(url="/repair-jobs/", status_code=303)
    record_repair_payment(
        job=job,
        payment_date=date.fromisoformat(payment_date),
        amount=Decimal(amount),
        payment_method=payment_method,
        entered_by=entered_by,
        notes=notes,
        db=db,
    )
    db.commit()
    return RedirectResponse(
        url=f"/repair-jobs/{job_id}/invoice?msg=Payment+recorded",
        status_code=303,
    )


@router.get("/{job_id}/invoice", response_class=HTMLResponse)
def repair_invoice(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.query(RepairJob).filter(RepairJob.id == job_id).first()
    if not job:
        return RedirectResponse(url="/repair-jobs/")
    payments = db.query(Payment).filter(Payment.repair_job_id == job_id).all()
    total_paid = sum((p.amount or Decimal("0")) for p in payments)
    total_billed = Decimal(str(job.total_billed_amount)) if job.total_billed_amount else Decimal("0")
    remaining = total_billed - total_paid
    return templates.TemplateResponse("repair_jobs/invoice.html", {
        "request": request,
        "job": job,
        "payments": payments,
        "total_paid": total_paid,
        "total_billed": total_billed,
        "remaining_balance": remaining,
    })


@router.get("/{job_id}/edit", response_class=HTMLResponse)
def edit_job_form(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = db.query(RepairJob).filter(RepairJob.id == job_id).first()
    if not job:
        return RedirectResponse(url="/repair-jobs/")
    units = db.query(Unit).order_by(Unit.unit_id).all()
    customers = db.query(Customer).order_by(Customer.full_name).all()
    payments = db.query(Payment).filter(Payment.repair_job_id == job_id).all()
    total_paid = sum((p.amount or Decimal("0")) for p in payments)
    total_billed = Decimal(str(job.total_billed_amount)) if job.total_billed_amount else Decimal("0")
    remaining = total_billed - total_paid
    return templates.TemplateResponse("repair_jobs/form.html", {
        "request": request,
        "job": job,
        "units": units,
        "customers": customers,
        "statuses": [s.value for s in RepairJobStatus],
        "business_lines": [b.value for b in BusinessLine],
        "job_types": [t.value for t in RepairJobType],
        "payments": payments,
        "total_paid": total_paid,
        "remaining_balance": remaining,
    })


@router.post("/{job_id}/edit")
def update_job(
    job_id: int,
    db: Session = Depends(get_db),
    business_line: str = Form(...),
    job_type: str = Form(...),
    open_date: str = Form(...),
    unit_id: str = Form(""),
    customer_id: str = Form(""),
    close_date: str = Form(""),
    status: str = Form("open"),
    labor_amount: str = Form("0"),
    materials_amount: str = Form("0"),
    total_billed_amount: str = Form("0"),
    notes: str = Form(""),
):
    from datetime import date
    job = db.query(RepairJob).filter(RepairJob.id == job_id).first()
    if job:
        job.business_line = business_line
        job.job_type = job_type
        job.open_date = date.fromisoformat(open_date)
        job.unit_id = int(unit_id) if unit_id else None
        job.customer_id = int(customer_id) if customer_id else None
        job.close_date = date.fromisoformat(close_date) if close_date else None
        job.status = status
        job.labor_amount = _d(labor_amount) or Decimal("0")
        job.materials_amount = _d(materials_amount) or Decimal("0")
        job.total_billed_amount = _d(total_billed_amount) or Decimal("0")
        job.notes = notes or None
        db.commit()
    return RedirectResponse(url=f"/repair-jobs/{job_id}/edit?msg=Job+updated", status_code=303)
