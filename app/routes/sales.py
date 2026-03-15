import io
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.sale import Sale
from app.models.unit import Unit
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.lease_account import LeaseAccount
from app.models.enums import BusinessLine, SaleStatus, PaymentMethod
from app.services.sale_service import finalize_new_sale

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Statuses that mean a sale is finished and the unit is available again.
_TERMINAL_SALE_STATUSES = {SaleStatus.cancelled}


def _d(val: str) -> Optional[Decimal]:
    try:
        return Decimal(val) if val else None
    except InvalidOperation:
        return None


def _active_sale_for_unit(unit_id: int, db: Session) -> Optional[Sale]:
    """Return the first non-terminal sale for a unit, or None if the unit is free."""
    return (
        db.query(Sale)
        .filter(Sale.unit_id == unit_id)
        .filter(Sale.status.notin_(_TERMINAL_SALE_STATUSES))
        .first()
    )


@router.get("/", response_class=HTMLResponse)
def list_sales(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    business_line: Optional[str] = None,
):
    query = db.query(Sale)
    if status:
        query = query.filter(Sale.status == status)
    if business_line:
        query = query.filter(Sale.business_line == business_line)
    sales = query.order_by(Sale.sale_date.desc()).all()
    return templates.TemplateResponse("sales/list.html", {
        "request": request,
        "sales": sales,
        "status_filter": status or "",
        "business_line_filter": business_line or "",
        "statuses": [s.value for s in SaleStatus],
        "business_lines": [b.value for b in BusinessLine],
    })


@router.get("/export")
def export_sales(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    sales = db.query(Sale).order_by(Sale.sale_id).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    headers = [
        "Sale ID", "Business Line", "Customer", "Unit", "Sale Date",
        "Sale Amount", "Down Payment", "Fees", "Total Contract", "Status", "Notes",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for s in sales:
        ws.append([
            s.sale_id,
            s.business_line.value if s.business_line else "",
            s.customer.full_name if s.customer else "",
            s.unit.unit_id if s.unit else "",
            str(s.sale_date) if s.sale_date else "",
            float(s.sale_amount) if s.sale_amount else 0,
            float(s.down_payment) if s.down_payment else 0,
            float(s.fees) if s.fees else 0,
            float(s.total_contract_amount) if s.total_contract_amount else 0,
            s.status.value if s.status else "",
            s.notes or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=sales.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_sale_form(request: Request, db: Session = Depends(get_db)):
    units = db.query(Unit).order_by(Unit.unit_id).all()
    customers = db.query(Customer).filter(Customer.status == "active").order_by(Customer.full_name).all()
    return templates.TemplateResponse("sales/form.html", {
        "request": request,
        "sale": None,
        "units": units,
        "customers": customers,
        "statuses": [s.value for s in SaleStatus],
        "business_lines": [b.value for b in BusinessLine],
        "payment_methods": [m.value for m in PaymentMethod],
    })


@router.post("/new")
def create_sale(
    db: Session = Depends(get_db),
    customer_id: str = Form(...),
    unit_id: str = Form(...),
    sale_date: str = Form(...),
    business_line: str = Form(...),
    sale_amount: str = Form(...),
    down_payment: str = Form("0"),
    fees: str = Form("0"),
    total_contract_amount: str = Form(""),
    status: str = Form("pending"),
    payment_method: str = Form("cash"),
    notes: str = Form(""),
):
    from datetime import date

    conflict = _active_sale_for_unit(int(unit_id), db)
    if conflict:
        return RedirectResponse(
            url=f"/sales/new?error=Unit+already+has+an+active+sale+({conflict.sale_id}).+Cancel+that+sale+before+creating+a+new+one.",
            status_code=303,
        )

    sale = Sale(
        customer_id=int(customer_id),
        unit_id=int(unit_id),
        sale_date=date.fromisoformat(sale_date),
        business_line=business_line,
        sale_amount=_d(sale_amount),
        down_payment=_d(down_payment) or Decimal("0"),
        fees=_d(fees) or Decimal("0"),
        total_contract_amount=_d(total_contract_amount),
        status=status,
        notes=notes or None,
    )
    db.add(sale)
    db.flush()
    sale.sale_id = f"S-{sale.id:04d}"

    finalize_new_sale(sale, payment_method, db)
    db.commit()

    # Golf cart sales go straight to receipt; car sales go to edit
    if business_line == "golf_cart":
        return RedirectResponse(url=f"/sales/{sale.id}/receipt", status_code=303)
    return RedirectResponse(url=f"/sales/{sale.id}/edit?msg=Sale+saved", status_code=303)


@router.get("/{sale_id}/receipt", response_class=HTMLResponse)
def sale_receipt(sale_id: int, request: Request, db: Session = Depends(get_db)):
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        return RedirectResponse(url="/sales/")
    payments = db.query(Payment).filter(Payment.sale_id == sale_id).all()
    total_paid = sum((p.amount or Decimal("0")) for p in payments)
    total_contract = (
        Decimal(str(sale.total_contract_amount))
        if sale.total_contract_amount
        else (Decimal(str(sale.sale_amount or "0")) + Decimal(str(sale.fees or "0")))
    )
    remaining = total_contract - total_paid
    return templates.TemplateResponse("sales/receipt.html", {
        "request": request,
        "sale": sale,
        "payments": payments,
        "total_paid": total_paid,
        "total_contract": total_contract,
        "remaining_balance": remaining,
    })


@router.post("/{sale_id}/complete")
def complete_sale(sale_id: int, db: Session = Depends(get_db)):
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        return RedirectResponse(url="/sales/", status_code=303)

    has_payment = db.query(Payment).filter(Payment.sale_id == sale_id).first()
    has_lease = (
        db.query(LeaseAccount).filter(LeaseAccount.unit_id == sale.unit_id).first()
        if sale.unit_id else None
    )
    if not has_payment and not has_lease:
        return RedirectResponse(
            url=f"/sales/{sale_id}/edit?msg=Cannot+complete+sale+—+no+payment+or+lease+on+file",
            status_code=303,
        )

    sale.status = "complete"
    unit = db.query(Unit).filter(Unit.id == sale.unit_id).first()
    if unit:
        unit.status = "sold"
        unit.linked_customer_id = sale.customer_id
    db.commit()
    return RedirectResponse(url=f"/sales/{sale_id}/edit?msg=Sale+marked+complete", status_code=303)


@router.get("/{sale_id}/edit", response_class=HTMLResponse)
def edit_sale_form(sale_id: int, request: Request, db: Session = Depends(get_db)):
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if not sale:
        return RedirectResponse(url="/sales/")
    units = db.query(Unit).order_by(Unit.unit_id).all()
    customers = db.query(Customer).order_by(Customer.full_name).all()
    payments = db.query(Payment).filter(Payment.sale_id == sale_id).all()
    total_paid = sum((p.amount or Decimal("0")) for p in payments)
    total_contract = (
        Decimal(str(sale.total_contract_amount))
        if sale.total_contract_amount
        else (Decimal(str(sale.sale_amount or "0")) + Decimal(str(sale.fees or "0")))
    )
    remaining = total_contract - total_paid
    return templates.TemplateResponse("sales/form.html", {
        "request": request,
        "sale": sale,
        "units": units,
        "customers": customers,
        "statuses": [s.value for s in SaleStatus],
        "business_lines": [b.value for b in BusinessLine],
        "payment_methods": [m.value for m in PaymentMethod],
        "payments": payments,
        "total_paid": total_paid,
        "total_contract": total_contract,
        "remaining_balance": remaining,
    })


@router.post("/{sale_id}/edit")
def update_sale(
    sale_id: int,
    db: Session = Depends(get_db),
    customer_id: str = Form(...),
    unit_id: str = Form(...),
    sale_date: str = Form(...),
    business_line: str = Form(...),
    sale_amount: str = Form(...),
    down_payment: str = Form("0"),
    fees: str = Form("0"),
    total_contract_amount: str = Form(""),
    status: str = Form("pending"),
    notes: str = Form(""),
):
    from datetime import date
    sale = db.query(Sale).filter(Sale.id == sale_id).first()
    if sale:
        new_unit_id = int(unit_id)
        if new_unit_id != sale.unit_id:
            conflict = _active_sale_for_unit(new_unit_id, db)
            if conflict:
                return RedirectResponse(
                    url=f"/sales/{sale_id}/edit?error=Unit+already+has+an+active+sale+({conflict.sale_id}).+Cancel+that+sale+before+reassigning+the+unit.",
                    status_code=303,
                )
        sale.customer_id = int(customer_id)
        sale.unit_id = new_unit_id
        sale.sale_date = date.fromisoformat(sale_date)
        sale.business_line = business_line
        sale.sale_amount = _d(sale_amount)
        sale.down_payment = _d(down_payment) or Decimal("0")
        sale.fees = _d(fees) or Decimal("0")
        sale.total_contract_amount = _d(total_contract_amount)
        sale.status = status
        sale.notes = notes or None
        db.commit()
    return RedirectResponse(url=f"/sales/{sale_id}/edit?msg=Sale+updated", status_code=303)
