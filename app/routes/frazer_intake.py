"""
Frazer source intake screens (admin only; the router is mounted with require_admin).

Upload a Frazer export, read what it contains, inspect exceptions, and preview what a later import
would do. There is NO edit or delete route, and nothing here writes a purchase, unit, payment,
transaction, customer, vendor, document or cash movement (docs/specs/cash-financial-control-spec.md, s19).
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models.frazer_intake import FrazerUpload
from app.models.user import User
from app.services import frazer_intake_service as svc
from app.services import frazer_parse as fp

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_CHICAGO, _UTC = ZoneInfo("America/Chicago"), ZoneInfo("UTC")
PAGE_SIZE = 100


def _money(v) -> str:
    if v in (None, ""):
        return ""
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        return str(v)
    return f"{'-' if d < 0 else ''}${abs(d):,.2f}"


def _local(dt: Optional[datetime]) -> str:
    return dt.replace(tzinfo=_UTC).astimezone(_CHICAGO).strftime("%b %-d, %Y %-I:%M %p %Z") if dt else ""


def _day(d) -> str:
    return d.strftime("%b %-d, %Y") if d else ""


def _label(name: str) -> str:
    return name.replace("floor_plan:", "Floor plan: ").replace("other:", "").replace("_", " ").capitalize()


templates.env.filters.update({"frmoney": _money, "frlocal": _local, "frday": _day, "frlabel": _label})
templates.env.globals.update(TYPE_LABEL=svc.TYPE_LABEL, CLASS_LABEL=fp.CLASS_LABEL)


def _page(request: Request, name: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(name, {"request": request, **ctx}, status_code=status_code)


def _get(db: Session, upload_pk: int) -> Optional[FrazerUpload]:
    return db.query(FrazerUpload).filter(FrazerUpload.id == upload_pk).first()


def _not_found(request: Request):
    return _page(request, "frazer_intake/error.html", 404, title="Not found", message="That upload doesn't exist.")


def _index(request, db, status_code=200, error=None, form=None):
    return _page(request, "frazer_intake/index.html", status_code, uploads=svc.list_uploads(db), error=error,
                 form=form or {}, types=[(t, svc.TYPE_LABEL[t]) for t in fp.EXPORT_TYPES])


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    return _index(request, db)


@router.post("/upload")
async def upload(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
                 file: Optional[UploadFile] = File(None), export_type: str = Form(""), export_date: str = Form(""),
                 coverage_start: str = Form(""), coverage_end: str = Form(""), actual_cutoff: str = Form("")):
    form = dict(export_type=export_type, export_date=export_date, coverage_start=coverage_start,
                coverage_end=coverage_end, actual_cutoff=actual_cutoff)
    try:
        up = svc.store_upload(db, user, file=file, requested_type=export_type, export_date=export_date,
                              coverage_start=coverage_start, coverage_end=coverage_end, actual_cutoff=actual_cutoff)
    except svc.DuplicateUpload as e:
        db.rollback()
        return _page(request, "frazer_intake/error.html", 409, title="Already uploaded", message=str(e),
                     link=(f"/finance/frazer/uploads/{e.existing.id}", f"Open {e.existing.upload_id}"))
    except svc.IntakeError as e:
        db.rollback()
        return _index(request, db, 400, str(e), form)
    try:
        db.commit()
    except Exception:
        db.rollback()
        svc.discard_file(up.stored_path)
        raise
    msg = "Upload+rejected%3A+see+why+below" if up.status != "parsed" else "Upload+read"
    return RedirectResponse(url=f"/finance/frazer/uploads/{up.id}?msg={msg}", status_code=303)


@router.get("/uploads/{upload_pk}", response_class=HTMLResponse)
def summary(upload_pk: int, request: Request, db: Session = Depends(get_db)):
    up = _get(db, upload_pk)
    if up is None:
        return _not_found(request)
    c = svc.classify(db, up)
    cross = svc.cross_checks(db, up)
    return _page(request, "frazer_intake/upload.html", up=up, s=up.summary_json or {}, c=c, cross=cross,
                 ready=svc.readiness(up, c, cross), groups=svc.grouped_exceptions(db, up),
                 cutoff=svc.current_cutoff(up), error=None)


@router.post("/uploads/{upload_pk}/notes")
def add_note(upload_pk: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
             note: str = Form(""), actual_cutoff: str = Form("")):
    up = _get(db, upload_pk)
    if up is None:
        return _not_found(request)
    try:
        svc.add_note(db, up, user, note=note, actual_cutoff=actual_cutoff)
    except svc.IntakeError as e:
        db.rollback()
        c = svc.classify(db, up)
        cross = svc.cross_checks(db, up)
        return _page(request, "frazer_intake/upload.html", 400, up=up, s=up.summary_json or {}, c=c, cross=cross,
                     ready=svc.readiness(up, c, cross), groups=svc.grouped_exceptions(db, up),
                     cutoff=svc.current_cutoff(up), error=str(e))
    db.commit()
    return RedirectResponse(url=f"/finance/frazer/uploads/{up.id}?msg=Note+added.+The+original+cutoff+is+unchanged", status_code=303)


@router.get("/uploads/{upload_pk}/rows", response_class=HTMLResponse)
def rows(upload_pk: int, request: Request, db: Session = Depends(get_db), status: str = "", q: str = "",
         entry_class: str = "", page: int = 1):
    up = _get(db, upload_pk)
    if up is None:
        return _not_found(request)
    c = svc.classify(db, up)
    items = c.rows
    if status in svc.STATUSES:
        items = [r for r in items if r["status"] == status]
    if entry_class:
        items = [r for r in items if r["entry_class"] == entry_class]
    needle = (q or "").strip().lower()
    if needle:
        def hit(r):
            vals = [r["identity"] or ""] + [str(v) for v in r["fields"].values()]
            return any(needle in v.lower() for v in vals)
        items = [r for r in items if hit(r)]
    total = len(items)
    page = max(page, 1)
    items = items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return _page(request, "frazer_intake/rows.html", up=up, items=items, total=total, page=page, pages=max(1, -(-total // PAGE_SIZE)),
                 status=status, q=q, entry_class=entry_class, counts=c.counts, statuses=svc.STATUSES)


@router.get("/uploads/{upload_pk}/exceptions", response_class=HTMLResponse)
def exceptions(upload_pk: int, request: Request, db: Session = Depends(get_db), code: str = ""):
    up = _get(db, upload_pk)
    if up is None:
        return _not_found(request)
    return _page(request, "frazer_intake/exceptions.html", up=up, groups=svc.grouped_exceptions(db, up, code or None),
                 code=code, all_groups=svc.grouped_exceptions(db, up))


@router.get("/uploads/{upload_pk}/download")
def download(upload_pk: int, request: Request, db: Session = Depends(get_db)):
    up = _get(db, upload_pk)
    path = svc.stored_file_path(up) if up else None
    if up is None or path is None:
        return _not_found(request)
    return FileResponse(path, filename=up.original_filename, media_type="text/csv",
                        content_disposition_type="attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})
