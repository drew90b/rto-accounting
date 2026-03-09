import io
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.lease_account import LeaseAccount
from app.models.unit import Unit
from app.models.customer import Customer
from app.models.enums import LeaseStatus, DelinquencyStatus, PaymentFrequency
from app.models.enums import PaymentMethod as PaymentMethodEnum
from app.services.lease_service import build_balance_map, calculate_remaining_balance, record_rto_payment

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _d(val: str) -> Optional[Decimal]:
    try:
        return Decimal(val) if val else None
    except InvalidOperation:
        return None


@router.get("/", response_class=HTMLResponse)
def list_leases(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    delinquency: Optional[str] = None,
):
    query = db.query(LeaseAccount)
    if status:
        query = query.filter(LeaseAccount.status == status)
    if delinquency:
        query = query.filter(LeaseAccount.delinquency_status == delinquency)
    leases = query.order_by(LeaseAccount.deal_date.desc()).all()
    return templates.TemplateResponse("lease_accounts/list.html", {
        "request": request,
        "leases": leases,
        "balances": build_balance_map(leases, db),
        "status_filter": status or "",
        "delinquency_filter": delinquency or "",
        "statuses": [s.value for s in LeaseStatus],
        "delinquency_statuses": [s.value for s in DelinquencyStatus],
    })


@router.get("/export")
def export_leases(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    leases = db.query(LeaseAccount).order_by(LeaseAccount.lease_id).all()
    balance_map = build_balance_map(leases, db)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lease Accounts"
    headers = [
        "Lease ID", "Customer", "Unit", "Deal Date", "Original Amount",
        "Down Payment", "Financed Balance", "Payment Amount", "Frequency",
        "Outstanding Balance", "Status", "Delinquency", "Notes",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for la in leases:
        ws.append([
            la.lease_id,
            la.customer.full_name if la.customer else "",
            la.unit.unit_id if la.unit else "",
            str(la.deal_date) if la.deal_date else "",
            float(la.original_agreed_amount) if la.original_agreed_amount else 0,
            float(la.down_payment) if la.down_payment else 0,
            float(la.financed_balance) if la.financed_balance else 0,
            float(la.scheduled_payment_amount) if la.scheduled_payment_amount else 0,
            la.payment_frequency.value if la.payment_frequency else "",
            float(balance_map[la.id]) if la.id in balance_map else 0,
            la.status.value if la.status else "",
            la.delinquency_status.value if la.delinquency_status else "",
            la.notes or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=lease_accounts.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_lease_form(request: Request, db: Session = Depends(get_db)):
    units = db.query(Unit).order_by(Unit.unit_id).all()
    customers = db.query(Customer).filter(Customer.status == "active").order_by(Customer.full_name).all()
    return templates.TemplateResponse("lease_accounts/form.html", {
        "request": request,
        "lease": None,
        "units": units,
        "customers": customers,
        "statuses": [s.value for s in LeaseStatus],
        "delinquency_statuses": [s.value for s in DelinquencyStatus],
        "frequencies": [f.value for f in PaymentFrequency],
    })


@router.post("/new")
def create_lease(
    db: Session = Depends(get_db),
    customer_id: str = Form(...),
    unit_id: str = Form(...),
    deal_date: str = Form(...),
    original_agreed_amount: str = Form(""),
    down_payment: str = Form("0"),
    financed_balance: str = Form(""),
    scheduled_payment_amount: str = Form(""),
    payment_frequency: str = Form("monthly"),
    status: str = Form("active"),
    delinquency_status: str = Form("current"),
    notes: str = Form(""),
):
    from datetime import date
    la = LeaseAccount(
        customer_id=int(customer_id),
        unit_id=int(unit_id),
        deal_date=date.fromisoformat(deal_date),
        original_agreed_amount=_d(original_agreed_amount),
        down_payment=_d(down_payment) or Decimal("0"),
        financed_balance=_d(financed_balance),
        scheduled_payment_amount=_d(scheduled_payment_amount),
        payment_frequency=payment_frequency,
        status=status,
        delinquency_status=delinquency_status,
        notes=notes or None,
    )
    db.add(la)
    db.flush()
    la.lease_id = f"L-{la.id:04d}"
    db.commit()
    return RedirectResponse(url=f"/lease-accounts/{la.id}/edit?msg=Lease+saved", status_code=303)


@router.get("/{lease_id}/edit", response_class=HTMLResponse)
def edit_lease_form(lease_id: int, request: Request, db: Session = Depends(get_db)):
    la = db.query(LeaseAccount).filter(LeaseAccount.id == lease_id).first()
    if not la:
        return RedirectResponse(url="/lease-accounts/")
    units = db.query(Unit).order_by(Unit.unit_id).all()
    customers = db.query(Customer).order_by(Customer.full_name).all()
    remaining_balance = calculate_remaining_balance(la, db)
    financed = Decimal(str(la.financed_balance)) if la.financed_balance else Decimal("0.00")
    total_paid = financed - remaining_balance
    return templates.TemplateResponse("lease_accounts/form.html", {
        "request": request,
        "lease": la,
        "remaining_balance": remaining_balance,
        "total_paid": total_paid,
        "units": units,
        "customers": customers,
        "statuses": [s.value for s in LeaseStatus],
        "delinquency_statuses": [s.value for s in DelinquencyStatus],
        "frequencies": [f.value for f in PaymentFrequency],
    })


@router.get("/{lease_id}/record-payment", response_class=HTMLResponse)
def record_payment_form(lease_id: int, request: Request, db: Session = Depends(get_db)):
    from datetime import date
    la = db.query(LeaseAccount).filter(LeaseAccount.id == lease_id).first()
    if not la:
        return RedirectResponse(url="/lease-accounts/")
    remaining = calculate_remaining_balance(la, db)
    return templates.TemplateResponse("lease_accounts/record_payment.html", {
        "request": request,
        "lease": la,
        "remaining_balance": remaining,
        "methods": [m.value for m in PaymentMethodEnum],
        "today": date.today().isoformat(),
    })


@router.post("/{lease_id}/record-payment")
def record_payment(
    lease_id: int,
    db: Session = Depends(get_db),
    payment_date: str = Form(...),
    amount: str = Form(...),
    payment_method: str = Form(...),
    notes: str = Form(""),
    entered_by: str = Form(""),
):
    from datetime import date
    la = db.query(LeaseAccount).filter(LeaseAccount.id == lease_id).first()
    if not la:
        return RedirectResponse(url="/lease-accounts/")
    record_rto_payment(
        lease=la,
        payment_date=date.fromisoformat(payment_date),
        amount=Decimal(amount),
        payment_method=payment_method,
        notes=notes,
        entered_by=entered_by,
        db=db,
    )
    db.commit()
    return RedirectResponse(
        url=f"/lease-accounts/{lease_id}/edit?msg=Payment+recorded+successfully",
        status_code=303,
    )


@router.post("/{lease_id}/edit")
def update_lease(
    lease_id: int,
    db: Session = Depends(get_db),
    customer_id: str = Form(...),
    unit_id: str = Form(...),
    deal_date: str = Form(...),
    original_agreed_amount: str = Form(""),
    down_payment: str = Form("0"),
    financed_balance: str = Form(""),
    scheduled_payment_amount: str = Form(""),
    payment_frequency: str = Form("monthly"),
    status: str = Form("active"),
    delinquency_status: str = Form("current"),
    notes: str = Form(""),
):
    from datetime import date
    la = db.query(LeaseAccount).filter(LeaseAccount.id == lease_id).first()
    if la:
        la.customer_id = int(customer_id)
        la.unit_id = int(unit_id)
        la.deal_date = date.fromisoformat(deal_date)
        la.original_agreed_amount = _d(original_agreed_amount)
        la.down_payment = _d(down_payment) or Decimal("0")
        la.financed_balance = _d(financed_balance)
        la.scheduled_payment_amount = _d(scheduled_payment_amount)
        la.payment_frequency = payment_frequency
        la.status = status
        la.delinquency_status = delinquency_status
        la.notes = notes or None
        db.commit()
    return RedirectResponse(url=f"/lease-accounts/{lease_id}/edit?msg=Lease+updated", status_code=303)
