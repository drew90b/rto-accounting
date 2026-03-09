from typing import Optional

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.exception_record import ExceptionRecord
from app.models.enums import ExceptionType, ExceptionStatus, LinkedRecordType

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def list_exceptions(
    request: Request,
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    exception_type: Optional[str] = None,
):
    query = db.query(ExceptionRecord)
    if status:
        query = query.filter(ExceptionRecord.status == status)
    if exception_type:
        query = query.filter(ExceptionRecord.exception_type == exception_type)
    exceptions = query.order_by(ExceptionRecord.opened_date.desc()).all()
    return templates.TemplateResponse("exceptions/list.html", {
        "request": request,
        "exceptions": exceptions,
        "status_filter": status or "",
        "type_filter": exception_type or "",
        "statuses": [s.value for s in ExceptionStatus],
        "exception_types": [t.value for t in ExceptionType],
    })


@router.get("/new", response_class=HTMLResponse)
def new_exception_form(request: Request):
    return templates.TemplateResponse("exceptions/form.html", {
        "request": request,
        "exc": None,
        "statuses": [s.value for s in ExceptionStatus],
        "exception_types": [t.value for t in ExceptionType],
        "record_types": [r.value for r in LinkedRecordType],
    })


@router.post("/new")
def create_exception(
    db: Session = Depends(get_db),
    exception_type: str = Form(...),
    linked_record_type: str = Form(""),
    linked_record_id: str = Form(""),
    opened_date: str = Form(...),
    owner: str = Form(""),
    notes: str = Form(""),
    target_resolution_date: str = Form(""),
):
    from datetime import date
    exc = ExceptionRecord(
        exception_type=exception_type,
        linked_record_type=linked_record_type or None,
        linked_record_id=int(linked_record_id) if linked_record_id else None,
        opened_date=date.fromisoformat(opened_date),
        owner=owner or None,
        notes=notes or None,
        target_resolution_date=date.fromisoformat(target_resolution_date) if target_resolution_date else None,
        status=ExceptionStatus.open,
    )
    db.add(exc)
    db.flush()
    exc.exception_id = f"E-{exc.id:04d}"
    db.commit()
    return RedirectResponse(url=f"/exceptions/{exc.id}/edit?msg=Exception+logged", status_code=303)


@router.get("/{exc_id}/edit", response_class=HTMLResponse)
def edit_exception_form(exc_id: int, request: Request, db: Session = Depends(get_db)):
    exc = db.query(ExceptionRecord).filter(ExceptionRecord.id == exc_id).first()
    if not exc:
        return RedirectResponse(url="/exceptions/")
    return templates.TemplateResponse("exceptions/form.html", {
        "request": request,
        "exc": exc,
        "statuses": [s.value for s in ExceptionStatus],
        "exception_types": [t.value for t in ExceptionType],
        "record_types": [r.value for r in LinkedRecordType],
    })


@router.post("/{exc_id}/edit")
def update_exception(
    exc_id: int,
    db: Session = Depends(get_db),
    exception_type: str = Form(...),
    linked_record_type: str = Form(""),
    linked_record_id: str = Form(""),
    opened_date: str = Form(...),
    owner: str = Form(""),
    status: str = Form("open"),
    notes: str = Form(""),
    target_resolution_date: str = Form(""),
    resolution_action: str = Form(""),
):
    from datetime import date, datetime
    exc = db.query(ExceptionRecord).filter(ExceptionRecord.id == exc_id).first()
    if exc:
        old_status = exc.status.value if exc.status else ""
        exc.exception_type = exception_type
        exc.linked_record_type = linked_record_type or None
        exc.linked_record_id = int(linked_record_id) if linked_record_id else None
        exc.opened_date = date.fromisoformat(opened_date)
        exc.owner = owner or None
        exc.status = status
        exc.notes = notes or None
        exc.target_resolution_date = date.fromisoformat(target_resolution_date) if target_resolution_date else None
        exc.resolution_action = resolution_action or None
        if old_status != status:
            entry = f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')}] Status changed: {old_status} → {status}"
            exc.audit_history = (exc.audit_history or "") + "\n" + entry
        db.commit()
    return RedirectResponse(url=f"/exceptions/{exc_id}/edit?msg=Exception+updated", status_code=303)
