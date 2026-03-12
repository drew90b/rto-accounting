from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.invoice import Invoice
from app.models.enums import InvoiceType, InvoiceStatus
from app.services.invoice_service import load_invoice_for_display

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_invoices(
    request: Request,
    db: Session = Depends(get_db),
    invoice_type: str = "",
    status: str = "",
):
    query = db.query(Invoice)
    if invoice_type:
        query = query.filter(Invoice.invoice_type == invoice_type)
    if status:
        query = query.filter(Invoice.status == status)
    invoices = query.order_by(Invoice.invoice_date.desc(), Invoice.id.desc()).all()
    return templates.TemplateResponse("invoices/list.html", {
        "request": request,
        "invoices": invoices,
        "type_filter": invoice_type,
        "status_filter": status,
        "invoice_types": [t.value for t in InvoiceType],
        "statuses": [s.value for s in InvoiceStatus],
    })


@router.get("/{invoice_id}/print", response_class=HTMLResponse)
def print_invoice(invoice_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        invoice, items, customer, payments, total_paid, remaining_balance, source = (
            load_invoice_for_display(invoice_id, db)
        )
    except ValueError:
        return RedirectResponse("/invoices/", status_code=302)

    return templates.TemplateResponse("invoices/invoice.html", {
        "request": request,
        "invoice": invoice,
        "items": items,
        "customer": customer,
        "payments": payments,
        "total_paid": total_paid,
        "remaining_balance": remaining_balance,
        "source": source,
        "msg": None,
    })
