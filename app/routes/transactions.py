import io
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.transaction import Transaction
from app.models.unit import Unit
from app.models.customer import Customer
from app.models.vendor import Vendor
from app.models.repair_job import RepairJob
from app.models.sale import Sale
from app.models.lease_account import LeaseAccount
from app.models.enums import (
    TransactionType, BusinessLine, RevenueStream,
    PaymentMethod, ReviewStatus, ExceptionTxnStatus
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _d(val: str) -> Optional[Decimal]:
    try:
        return Decimal(val) if val else None
    except InvalidOperation:
        return None


@router.get("/", response_class=HTMLResponse)
def list_transactions(
    request: Request,
    db: Session = Depends(get_db),
    transaction_type: Optional[str] = None,
    business_line: Optional[str] = None,
    revenue_stream: Optional[str] = None,
    review_status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    from datetime import date
    query = db.query(Transaction)
    if transaction_type:
        query = query.filter(Transaction.transaction_type == transaction_type)
    if business_line:
        query = query.filter(Transaction.business_line == business_line)
    if revenue_stream:
        query = query.filter(Transaction.revenue_stream == revenue_stream)
    if review_status:
        query = query.filter(Transaction.review_status == review_status)
    if date_from:
        query = query.filter(Transaction.transaction_date >= date.fromisoformat(date_from))
    if date_to:
        query = query.filter(Transaction.transaction_date <= date.fromisoformat(date_to))
    txns = query.order_by(Transaction.transaction_date.desc()).all()
    return templates.TemplateResponse("transactions/list.html", {
        "request": request,
        "transactions": txns,
        "type_filter": transaction_type or "",
        "business_line_filter": business_line or "",
        "revenue_stream_filter": revenue_stream or "",
        "review_status_filter": review_status or "",
        "date_from": date_from or "",
        "date_to": date_to or "",
        "transaction_types": [t.value for t in TransactionType],
        "business_lines": [b.value for b in BusinessLine],
        "revenue_streams": [r.value for r in RevenueStream],
        "review_statuses": [r.value for r in ReviewStatus],
    })


@router.get("/export")
def export_transactions(
    db: Session = Depends(get_db),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
):
    import openpyxl
    from openpyxl.styles import Font
    from datetime import date
    query = db.query(Transaction)
    if date_from:
        query = query.filter(Transaction.transaction_date >= date.fromisoformat(date_from))
    if date_to:
        query = query.filter(Transaction.transaction_date <= date.fromisoformat(date_to))
    txns = query.order_by(Transaction.transaction_date).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Transactions"
    headers = [
        "Transaction ID", "Date", "Entry Date", "Type", "Business Line", "Revenue Stream",
        "Amount", "Description", "Category", "Payment Method",
        "Unit", "Repair Job", "Sale", "Lease", "Vendor", "Customer",
        "Receipt", "Coded", "Review Status", "Exception Status", "Entered By",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for t in txns:
        ws.append([
            t.transaction_id,
            str(t.transaction_date) if t.transaction_date else "",
            str(t.entry_date) if t.entry_date else "",
            t.transaction_type.value if t.transaction_type else "",
            t.business_line.value if t.business_line else "",
            t.revenue_stream.value if t.revenue_stream else "",
            float(t.amount) if t.amount else 0,
            t.description or "",
            t.category or "",
            t.payment_method.value if t.payment_method else "",
            t.unit.unit_id if t.unit else "",
            t.repair_job.job_id if t.repair_job else "",
            t.sale.sale_id if t.sale else "",
            t.lease_account.lease_id if t.lease_account else "",
            t.vendor.name if t.vendor else "",
            t.customer.full_name if t.customer else "",
            "Yes" if t.receipt_attached else "No",
            "Yes" if t.coding_complete else "No",
            t.review_status.value if t.review_status else "",
            t.exception_status.value if t.exception_status else "",
            t.entered_by or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=transactions.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_transaction_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("transactions/form.html", {
        "request": request,
        "txn": None,
        **_form_context(db),
    })


def _form_context(db):
    return {
        "units": db.query(Unit).order_by(Unit.unit_id).all(),
        "customers": db.query(Customer).order_by(Customer.full_name).all(),
        "vendors": db.query(Vendor).order_by(Vendor.name).all(),
        "repair_jobs": db.query(RepairJob).order_by(RepairJob.job_id).all(),
        "sales": db.query(Sale).order_by(Sale.sale_id).all(),
        "leases": db.query(LeaseAccount).order_by(LeaseAccount.lease_id).all(),
        "transaction_types": [t.value for t in TransactionType],
        "business_lines": [b.value for b in BusinessLine],
        "revenue_streams": [r.value for r in RevenueStream],
        "payment_methods": [m.value for m in PaymentMethod],
        "review_statuses": [r.value for r in ReviewStatus],
    }


@router.post("/new")
def create_transaction(
    db: Session = Depends(get_db),
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    business_line: str = Form(...),
    amount: str = Form(...),
    description: str = Form(""),
    revenue_stream: str = Form(""),
    category: str = Form(""),
    payment_method: str = Form(""),
    unit_id: str = Form(""),
    repair_job_id: str = Form(""),
    sale_id: str = Form(""),
    lease_account_id: str = Form(""),
    vendor_id: str = Form(""),
    customer_id: str = Form(""),
    receipt_attached: str = Form(""),
    coding_complete: str = Form(""),
    review_status: str = Form("pending"),
    entered_by: str = Form(""),
):
    from datetime import date
    t = Transaction(
        transaction_date=date.fromisoformat(transaction_date),
        entry_date=date.today(),
        transaction_type=transaction_type,
        business_line=business_line,
        amount=_d(amount),
        description=description or None,
        revenue_stream=revenue_stream or None,
        category=category or None,
        payment_method=payment_method or None,
        unit_id=int(unit_id) if unit_id else None,
        repair_job_id=int(repair_job_id) if repair_job_id else None,
        sale_id=int(sale_id) if sale_id else None,
        lease_account_id=int(lease_account_id) if lease_account_id else None,
        vendor_id=int(vendor_id) if vendor_id else None,
        customer_id=int(customer_id) if customer_id else None,
        receipt_attached=bool(receipt_attached),
        coding_complete=bool(coding_complete),
        review_status=review_status,
        entered_by=entered_by or None,
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"
    db.commit()
    return RedirectResponse(url=f"/transactions/{t.id}/edit?msg=Transaction+saved", status_code=303)


@router.get("/{txn_id}/edit", response_class=HTMLResponse)
def edit_transaction_form(txn_id: int, request: Request, db: Session = Depends(get_db)):
    t = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if not t:
        return RedirectResponse(url="/transactions/")
    return templates.TemplateResponse("transactions/form.html", {
        "request": request,
        "txn": t,
        **_form_context(db),
    })


@router.post("/{txn_id}/edit")
def update_transaction(
    txn_id: int,
    db: Session = Depends(get_db),
    transaction_date: str = Form(...),
    transaction_type: str = Form(...),
    business_line: str = Form(...),
    amount: str = Form(...),
    description: str = Form(""),
    revenue_stream: str = Form(""),
    category: str = Form(""),
    payment_method: str = Form(""),
    unit_id: str = Form(""),
    repair_job_id: str = Form(""),
    sale_id: str = Form(""),
    lease_account_id: str = Form(""),
    vendor_id: str = Form(""),
    customer_id: str = Form(""),
    receipt_attached: str = Form(""),
    coding_complete: str = Form(""),
    review_status: str = Form("pending"),
    entered_by: str = Form(""),
):
    from datetime import date
    t = db.query(Transaction).filter(Transaction.id == txn_id).first()
    if t:
        t.transaction_date = date.fromisoformat(transaction_date)
        t.transaction_type = transaction_type
        t.business_line = business_line
        t.amount = _d(amount)
        t.description = description or None
        t.revenue_stream = revenue_stream or None
        t.category = category or None
        t.payment_method = payment_method or None
        t.unit_id = int(unit_id) if unit_id else None
        t.repair_job_id = int(repair_job_id) if repair_job_id else None
        t.sale_id = int(sale_id) if sale_id else None
        t.lease_account_id = int(lease_account_id) if lease_account_id else None
        t.vendor_id = int(vendor_id) if vendor_id else None
        t.customer_id = int(customer_id) if customer_id else None
        t.receipt_attached = bool(receipt_attached)
        t.coding_complete = bool(coding_complete)
        t.review_status = review_status
        t.entered_by = entered_by or None
        db.commit()
    return RedirectResponse(url=f"/transactions/{txn_id}/edit?msg=Transaction+updated", status_code=303)
