import io
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.vendor import Vendor

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_vendors(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = None,
):
    query = db.query(Vendor)
    if search:
        t = f"%{search}%"
        query = query.filter(
            or_(
                Vendor.name.ilike(t),
                Vendor.phone.ilike(t),
                Vendor.email.ilike(t),
                Vendor.vendor_id.ilike(t),
            )
        )
    vendors = query.order_by(Vendor.name).all()
    return templates.TemplateResponse("vendors/list.html", {
        "request": request,
        "vendors": vendors,
        "search": search or "",
    })


@router.get("/export")
def export_vendors(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    vendors = db.query(Vendor).order_by(Vendor.vendor_id).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Vendors"
    headers = ["Vendor ID", "Name", "Phone", "Email", "Address", "Notes"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for v in vendors:
        ws.append([
            v.vendor_id or "",
            v.name,
            v.phone or "",
            v.email or "",
            v.address or "",
            v.notes or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 20
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=vendors.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_vendor_form(request: Request):
    return templates.TemplateResponse("vendors/form.html", {
        "request": request,
        "vendor": None,
    })


@router.post("/new")
def create_vendor(
    db: Session = Depends(get_db),
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
):
    v = Vendor(
        name=name,
        phone=phone or None,
        email=email or None,
        address=address or None,
        notes=notes or None,
    )
    db.add(v)
    db.flush()
    v.vendor_id = f"V-{v.id:04d}"
    db.commit()
    return RedirectResponse(url=f"/vendors/{v.id}/edit?msg=Vendor+saved", status_code=303)


@router.get("/{vendor_id}/edit", response_class=HTMLResponse)
def edit_vendor_form(vendor_id: int, request: Request, db: Session = Depends(get_db)):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if not v:
        return RedirectResponse(url="/vendors/")
    return templates.TemplateResponse("vendors/form.html", {
        "request": request,
        "vendor": v,
    })


@router.post("/{vendor_id}/edit")
def update_vendor(
    vendor_id: int,
    db: Session = Depends(get_db),
    name: str = Form(...),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
):
    v = db.query(Vendor).filter(Vendor.id == vendor_id).first()
    if v:
        v.name = name
        v.phone = phone or None
        v.email = email or None
        v.address = address or None
        v.notes = notes or None
        db.commit()
    return RedirectResponse(url=f"/vendors/{vendor_id}/edit?msg=Vendor+updated", status_code=303)
