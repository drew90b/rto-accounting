import io
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.customer import Customer
from app.models.enums import CustomerStatus

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_customers(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    status: Optional[str] = None,
):
    query = db.query(Customer)
    if search:
        t = f"%{search}%"
        query = query.filter(
            or_(
                Customer.full_name.ilike(t),
                Customer.phone.ilike(t),
                Customer.email.ilike(t),
                Customer.customer_id.ilike(t),
            )
        )
    if status:
        query = query.filter(Customer.status == status)
    customers = query.order_by(Customer.full_name).all()
    return templates.TemplateResponse("customers/list.html", {
        "request": request,
        "customers": customers,
        "search": search or "",
        "status_filter": status or "",
        "statuses": [s.value for s in CustomerStatus],
    })


@router.get("/export")
def export_customers(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    customers = db.query(Customer).order_by(Customer.customer_id).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Customers"
    headers = ["Customer ID", "Name", "Phone", "Email", "Address", "Status", "Notes"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for c in customers:
        ws.append([
            c.customer_id, c.full_name, c.phone or "", c.email or "",
            c.address or "", c.status.value if c.status else "", c.notes or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 18
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=customers.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_customer_form(request: Request):
    return templates.TemplateResponse("customers/form.html", {
        "request": request,
        "customer": None,
        "statuses": [s.value for s in CustomerStatus],
    })


@router.post("/new")
def create_customer(
    db: Session = Depends(get_db),
    full_name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    status: str = Form("active"),
):
    c = Customer(
        full_name=full_name,
        phone=phone or None,
        email=email or None,
        address=address or None,
        notes=notes or None,
        status=status,
    )
    db.add(c)
    db.flush()
    c.customer_id = f"C-{c.id:04d}"
    db.commit()
    return RedirectResponse(url=f"/customers/{c.id}/edit?msg=Customer+saved", status_code=303)


@router.get("/{customer_id}/edit", response_class=HTMLResponse)
def edit_customer_form(customer_id: int, request: Request, db: Session = Depends(get_db)):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if not c:
        return RedirectResponse(url="/customers/")
    return templates.TemplateResponse("customers/form.html", {
        "request": request,
        "customer": c,
        "statuses": [s.value for s in CustomerStatus],
    })


@router.post("/{customer_id}/edit")
def update_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    full_name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    status: str = Form("active"),
):
    c = db.query(Customer).filter(Customer.id == customer_id).first()
    if c:
        c.full_name = full_name
        c.phone = phone or None
        c.email = email or None
        c.address = address or None
        c.notes = notes or None
        c.status = status
        db.commit()
    return RedirectResponse(url=f"/customers/{customer_id}/edit?msg=Customer+updated", status_code=303)
