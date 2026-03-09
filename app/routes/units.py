import io
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.unit import Unit
from app.models.enums import UnitType, UnitStatus, BusinessLine

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _parse_decimal(val: str) -> Optional[Decimal]:
    try:
        return Decimal(val) if val else None
    except InvalidOperation:
        return None


def _parse_int(val: str) -> Optional[int]:
    try:
        return int(val) if val else None
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse)
def list_units(
    request: Request,
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    status: Optional[str] = None,
    business_line: Optional[str] = None,
):
    query = db.query(Unit)
    if search:
        t = f"%{search}%"
        query = query.filter(
            or_(
                Unit.make.ilike(t),
                Unit.model.ilike(t),
                Unit.vin_serial.ilike(t),
                Unit.unit_id.ilike(t),
            )
        )
    if status:
        query = query.filter(Unit.status == status)
    if business_line:
        query = query.filter(Unit.business_line == business_line)
    units = query.order_by(Unit.created_at.desc()).all()
    return templates.TemplateResponse("units/list.html", {
        "request": request,
        "units": units,
        "search": search or "",
        "status_filter": status or "",
        "business_line_filter": business_line or "",
        "statuses": [s.value for s in UnitStatus],
        "business_lines": [b.value for b in BusinessLine],
    })


@router.get("/export")
def export_units(db: Session = Depends(get_db)):
    import openpyxl
    from openpyxl.styles import Font
    units = db.query(Unit).order_by(Unit.unit_id).all()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Units"
    headers = [
        "Unit ID", "Type", "Business Line", "Year", "Make", "Model",
        "VIN/Serial", "Purchase Date", "Purchase Source", "Acquisition Cost",
        "Status", "Notes",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for u in units:
        ws.append([
            u.unit_id,
            u.unit_type.value if u.unit_type else "",
            u.business_line.value if u.business_line else "",
            u.year or "",
            u.make or "",
            u.model or "",
            u.vin_serial or "",
            str(u.purchase_date) if u.purchase_date else "",
            u.purchase_source or "",
            float(u.acquisition_cost) if u.acquisition_cost else "",
            u.status.value if u.status else "",
            u.notes or "",
        ])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = 16
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=units.xlsx"},
    )


@router.get("/new", response_class=HTMLResponse)
def new_unit_form(request: Request):
    return templates.TemplateResponse("units/form.html", {
        "request": request,
        "unit": None,
        "statuses": [s.value for s in UnitStatus],
        "unit_types": [t.value for t in UnitType],
        "business_lines": [b.value for b in BusinessLine],
    })


@router.post("/new")
def create_unit(
    db: Session = Depends(get_db),
    unit_type: str = Form(...),
    business_line: str = Form(...),
    vin_serial: str = Form(""),
    year: str = Form(""),
    make: str = Form(""),
    model: str = Form(""),
    purchase_date: str = Form(""),
    purchase_source: str = Form(""),
    acquisition_cost: str = Form(""),
    status: str = Form("acquired"),
    notes: str = Form(""),
):
    from datetime import date
    unit = Unit(
        unit_type=unit_type,
        business_line=business_line,
        vin_serial=vin_serial or None,
        year=_parse_int(year),
        make=make or None,
        model=model or None,
        purchase_date=date.fromisoformat(purchase_date) if purchase_date else None,
        purchase_source=purchase_source or None,
        acquisition_cost=_parse_decimal(acquisition_cost),
        status=status,
        notes=notes or None,
    )
    db.add(unit)
    db.flush()
    unit.unit_id = f"U-{unit.id:04d}"
    db.commit()
    return RedirectResponse(url=f"/units/{unit.id}/edit?msg=Unit+saved", status_code=303)


@router.get("/{unit_id}/edit", response_class=HTMLResponse)
def edit_unit_form(unit_id: int, request: Request, db: Session = Depends(get_db)):
    unit = db.query(Unit).filter(Unit.id == unit_id).first()
    if not unit:
        return RedirectResponse(url="/units/")
    return templates.TemplateResponse("units/form.html", {
        "request": request,
        "unit": unit,
        "statuses": [s.value for s in UnitStatus],
        "unit_types": [t.value for t in UnitType],
        "business_lines": [b.value for b in BusinessLine],
    })


@router.post("/{unit_id}/edit")
def update_unit(
    unit_id: int,
    db: Session = Depends(get_db),
    unit_type: str = Form(...),
    business_line: str = Form(...),
    vin_serial: str = Form(""),
    year: str = Form(""),
    make: str = Form(""),
    model: str = Form(""),
    purchase_date: str = Form(""),
    purchase_source: str = Form(""),
    acquisition_cost: str = Form(""),
    status: str = Form("acquired"),
    notes: str = Form(""),
):
    from datetime import date
    unit = db.query(Unit).filter(Unit.id == unit_id).first()
    if unit:
        unit.unit_type = unit_type
        unit.business_line = business_line
        unit.vin_serial = vin_serial or None
        unit.year = _parse_int(year)
        unit.make = make or None
        unit.model = model or None
        unit.purchase_date = date.fromisoformat(purchase_date) if purchase_date else None
        unit.purchase_source = purchase_source or None
        unit.acquisition_cost = _parse_decimal(acquisition_cost)
        unit.status = status
        unit.notes = notes or None
        db.commit()
    return RedirectResponse(url=f"/units/{unit_id}/edit?msg=Unit+updated", status_code=303)
