import io
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.payment import Payment
from app.models.customer import Customer
from app.models.sale import Sale
from app.models.lease_account import LeaseAccount
from app.models.repair_job import RepairJob
from app.models.enums import PaymentMethod

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _d(val: str) -> Optional[Decimal]:
    try:
        return Decimal(val) if val else None
    except InvalidOperation:
        return None


@router.get("/", response_class=HTMLResponse)
def list_payments(
    request: Request,
    db: Session = Depends(get_db),
    customer_id: Optional[str] = None,
    method: Optional[str] = None,
):
    query = db.query(Payment)
    if customer_id:
        query = query.filter(Payment.customer_id == int(customer_id))
    if method:
        query = query.filter(Payment.payment_method == method)
    payments = query.order_by(Payment.payment_date.desc()).all()
    customers = db.query(Customer).order_by(Customer.full_name).all()
    return templates.TemplateResponse("payments/list.html", {
        "request": request,
        "payments": payments,
        "customers": customers,
        "customer_filter": customer_id or "",
        "method_filter": method or "",
        "methods": [m.value for m in PaymentMethod],
    })


@router.get("/export")
def export_payments(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    payments = db.query(Payment).order_by(Payment.payment_id).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Payments"
    headers = [
        "Payment ID", "Customer", "Date", "Amount", "Method",
        "Applied To Sale", "Applied To Lease", "Applied To Repair Job",
        "Entered By", "Notes",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for p in payments:
        ws.append([
            p.payment_id,
            p.customer.full_name if p.customer else "",
            str(p.payment_date) if p.payment_date else "",
            float(p.amount) if p.amount else 0,
            p.payment_method.value if p.payment_method else "",
            p.sale.sale_id if p.sale else "",
            p.lease_account.lease_id if p.lease_account else "",
            p.repair_job.job_id if p.repair_job else "",
            p.entered_by or "",
            p.notes or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=payments.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_payment_form(request: Request, db: Session = Depends(get_db)):
    customers = db.query(Customer).filter(Customer.status == "active").order_by(Customer.full_name).all()
    sales = db.query(Sale).order_by(Sale.sale_id).all()
    leases = db.query(LeaseAccount).order_by(LeaseAccount.lease_id).all()
    repair_jobs = db.query(RepairJob).order_by(RepairJob.job_id).all()
    return templates.TemplateResponse("payments/form.html", {
        "request": request,
        "payment": None,
        "customers": customers,
        "sales": sales,
        "leases": leases,
        "repair_jobs": repair_jobs,
        "methods": [m.value for m in PaymentMethod],
    })


@router.post("/new")
def create_payment(
    db: Session = Depends(get_db),
    customer_id: str = Form(...),
    payment_date: str = Form(...),
    amount: str = Form(...),
    payment_method: str = Form(...),
    sale_id: str = Form(""),
    lease_account_id: str = Form(""),
    repair_job_id: str = Form(""),
    entered_by: str = Form(""),
    notes: str = Form(""),
):
    from datetime import date
    p = Payment(
        customer_id=int(customer_id),
        payment_date=date.fromisoformat(payment_date),
        amount=_d(amount),
        payment_method=payment_method,
        sale_id=int(sale_id) if sale_id else None,
        lease_account_id=int(lease_account_id) if lease_account_id else None,
        repair_job_id=int(repair_job_id) if repair_job_id else None,
        entered_by=entered_by or None,
        notes=notes or None,
    )
    db.add(p)
    db.flush()
    p.payment_id = f"P-{p.id:04d}"
    db.commit()
    return RedirectResponse(url=f"/payments/{p.id}/edit?msg=Payment+saved", status_code=303)


@router.get("/{payment_id}/edit", response_class=HTMLResponse)
def edit_payment_form(payment_id: int, request: Request, db: Session = Depends(get_db)):
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if not p:
        return RedirectResponse(url="/payments/")
    customers = db.query(Customer).order_by(Customer.full_name).all()
    sales = db.query(Sale).order_by(Sale.sale_id).all()
    leases = db.query(LeaseAccount).order_by(LeaseAccount.lease_id).all()
    repair_jobs = db.query(RepairJob).order_by(RepairJob.job_id).all()
    return templates.TemplateResponse("payments/form.html", {
        "request": request,
        "payment": p,
        "customers": customers,
        "sales": sales,
        "leases": leases,
        "repair_jobs": repair_jobs,
        "methods": [m.value for m in PaymentMethod],
    })


@router.post("/{payment_id}/edit")
def update_payment(
    payment_id: int,
    db: Session = Depends(get_db),
    customer_id: str = Form(...),
    payment_date: str = Form(...),
    amount: str = Form(...),
    payment_method: str = Form(...),
    sale_id: str = Form(""),
    lease_account_id: str = Form(""),
    repair_job_id: str = Form(""),
    entered_by: str = Form(""),
    notes: str = Form(""),
):
    from datetime import date
    p = db.query(Payment).filter(Payment.id == payment_id).first()
    if p:
        p.customer_id = int(customer_id)
        p.payment_date = date.fromisoformat(payment_date)
        p.amount = _d(amount)
        p.payment_method = payment_method
        p.sale_id = int(sale_id) if sale_id else None
        p.lease_account_id = int(lease_account_id) if lease_account_id else None
        p.repair_job_id = int(repair_job_id) if repair_job_id else None
        p.entered_by = entered_by or None
        p.notes = notes or None
        db.commit()
    return RedirectResponse(url=f"/payments/{payment_id}/edit?msg=Payment+updated", status_code=303)
