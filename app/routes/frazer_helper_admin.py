"""
Administrator page for the Frazer export helper: last successful update, run history, upload tokens, approved column layouts.
Mounted with require_admin. It starts no run and reads no customer data; it never resolves a question or edits a record.
"""
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models.frazer_helper import FrazerHelperRun, FrazerHelperToken
from app.models.frazer_intake import FrazerUpload
from app.models.user import User
from app.services import frazer_helper_service as svc
from app.services import frazer_intake_service as intake

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_CHICAGO, _UTC = ZoneInfo("America/Chicago"), ZoneInfo("UTC")


def _local(dt: Optional[datetime]) -> str:
    return dt.replace(tzinfo=_UTC).astimezone(_CHICAGO).strftime("%b %-d, %Y %-I:%M %p %Z") if dt else "never"


def _age(hours) -> str:
    if hours is None:
        return "never"
    return f"{hours:.0f} hours ago" if hours < 48 else f"{hours / 24:.0f} days ago"


templates.env.filters.update({"fhlocal": _local, "fhage": _age})
templates.env.globals.update(TYPE_LABEL=intake.TYPE_LABEL, STALE_HOURS=svc.STALE_AFTER_HOURS)


def _page(request, db, status_code=200, **extra):
    users = db.query(User).filter(User.active.is_(True)).order_by(User.username).all()
    uploads = (db.query(FrazerUpload).filter(FrazerUpload.status == "parsed", FrazerUpload.export_type.in_(svc.EXPORT_TYPES))
               .order_by(FrazerUpload.id.desc()).limit(30).all())
    ctx = dict(request=request, fresh=svc.freshness(db), runs=db.query(FrazerHelperRun).order_by(FrazerHelperRun.id.desc()).limit(15).all(),
               tokens=db.query(FrazerHelperToken).order_by(FrazerHelperToken.id.desc()).all(), layouts=svc.layouts(db), users=users,
               uploads=uploads, types=svc.EXPORT_TYPES, error=None, new_secret=None, msg=request.query_params.get("msg"))
    ctx.update(extra)
    return templates.TemplateResponse("frazer_helper/index.html", ctx, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    return _page(request, db)


@router.post("/tokens", response_class=HTMLResponse)
def create_token(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin), label: str = Form(""),
                 user_id: int = Form(...), days_valid: int = Form(90)):
    person = db.get(User, user_id)
    try:
        if person is None or not person.active:
            raise svc.HelperError("Choose an active person to attribute the uploads to.")
        _, secret = svc.create_token(db, admin, label=label, user=person, days_valid=days_valid or None)
        db.commit()
    except svc.HelperError as e:
        db.rollback()
        return _page(request, db, 400, error=str(e))
    # the secret is shown on this response only; it is not stored anywhere readable and is not in the URL
    return _page(request, db, new_secret=secret)


@router.post("/tokens/{token_id}/revoke")
def revoke(token_id: int, db: Session = Depends(get_db)):
    try:
        svc.revoke_token(db, token_id)
        db.commit()
    except svc.HelperError:
        db.rollback()
    return RedirectResponse("/finance/frazer/helper/?msg=Token+revoked", status_code=303)


@router.post("/layouts", response_class=HTMLResponse)
def approve_layout(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin), upload_pk: int = Form(...)):
    try:
        svc.set_layout(db, admin, upload_pk)
        db.commit()
    except svc.HelperError as e:
        db.rollback()
        return _page(request, db, 400, error=str(e))
    return RedirectResponse("/finance/frazer/helper/?msg=Expected+layout+approved", status_code=303)
