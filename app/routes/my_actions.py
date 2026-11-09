"""
My actions: the restricted view of review issues assigned to the signed-in user (any signed-in user, not only administrators).

The router is mounted with require_user, and EVERY handler narrows further: an issue is reachable only if it is assigned to the
caller and not restricted payroll (administrators see their own assigned items in full). Nothing here links to the Monthly
Review page, the Frazer pages or any financial record. Spec section 20, REQ-MR-23..25.
"""
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models.monthly_review import ReviewIssueFile
from app.models.user import User
from app.routes.monthly_review import _day, _local
from app.services import monthly_review_service as svc
from app.services import my_actions_service as ma
from app.services import vehicle_recon_service as vrsvc      # vehicle reconciliation: assigned_items() is its integration point

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.filters.update({"mrlocal": _local, "mrday": _day})
BASE = "/my-actions"


def _gone(request: Request, user: User):
    return templates.TemplateResponse("my_actions/error.html", {"request": request, "message": "That question isn't on your list."}, status_code=404)


def _detail(request, db, user, issue, *, status_code=200, error=None, form=None):
    return templates.TemplateResponse("my_actions/detail.html", {
        "request": request, "i": issue, "events": ma.visible_events(issue), "files": ma.files_for(db, issue), "error": error, "form": form or {},
        "can_note_source": bool((issue.auto_key or "").startswith("source:") and issue.auto_key.split(":", 1)[1] in svc.SOURCES
                                and issue.auto_key != "source:payroll_weekly" and not issue.restricted and issue.status != "resolved"),
        "source_label": svc.SOURCES.get((issue.auto_key or "").split(":", 1)[-1], ""), "STATUS_LABEL": svc.STATUS_LABEL, "base": BASE,
        "msg": (request.query_params.get("msg") or "").replace("+", " "), "is_admin": user.is_admin,
    }, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    from datetime import date
    from app.services import business_calendar as bc
    issues = ma.my_issues(db, user)
    return templates.TemplateResponse("my_actions/index.html", {
        "request": request, "issues": issues, "STATUS_LABEL": svc.STATUS_LABEL, "base": BASE, "today": bc.chicago_today(), "is_admin": user.is_admin,
        "open_count": sum(1 for i in issues if i.status != "resolved"),
        # Vehicle reconciliation questions assigned to this user (restricted evidence; answered on their own page)
        "vehicle_items": vrsvc.assigned_items(db, user),
    })


@router.get("/files/{file_id}")
def download(file_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    f = db.query(ReviewIssueFile).filter(ReviewIssueFile.id == file_id).first()
    if f is None or not ma.can_download(user, f):
        return _gone(request, user)
    p = ma.file_path(f)
    if p is None:
        return _gone(request, user)
    return FileResponse(p, media_type="application/octet-stream", filename=f.original_filename,
                        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store",
                                 "Content-Disposition": f'attachment; filename="{f.original_filename}"'})


@router.get("/{ref}", response_class=HTMLResponse)
def detail(ref: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    issue = ma.get_issue(db, user, ref)
    return _detail(request, db, user, issue) if issue else _gone(request, user)


def _run(request, db, user, ref, fn, ok, form=None):
    issue = ma.get_issue(db, user, ref)
    if issue is None:
        return _gone(request, user)
    try:
        fn(issue)
        db.commit()
    except (ma.ActionError, svc.ReviewError) as e:
        db.rollback()
        issue = ma.get_issue(db, user, ref)
        return _detail(request, db, user, issue, status_code=400, error=str(e), form=form)
    return RedirectResponse(f"{BASE}/{issue.issue_id}?msg={ok.replace(' ', '+')}", status_code=303)


@router.post("/{ref}/reply")
def reply(ref: str, request: Request, body: str = Form(""), status: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _run(request, db, user, ref, lambda i: ma.reply(db, user, i, body=body, status=status), "Sent", dict(body=body))


@router.post("/{ref}/files")
async def attach(ref: str, request: Request, file: UploadFile = File(None), note: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(require_user)):
    issue = ma.get_issue(db, user, ref)
    if issue is None:
        return _gone(request, user)
    try:
        ma.attach_file(db, user, issue, file=file, note=note)
        db.commit()
    except (ma.ActionError, svc.ReviewError) as e:
        db.rollback()
        return _detail(request, db, user, ma.get_issue(db, user, ref), status_code=400, error=str(e), form=dict(note=note))
    return RedirectResponse(f"{BASE}/{issue.issue_id}?msg=File+added", status_code=303)


@router.post("/{ref}/record")
def record(ref: str, request: Request, coverage_start: str = Form(""), coverage_end: str = Form(""), where_kept: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _run(request, db, user, ref, lambda i: ma.record_source(db, user, i, coverage_start=coverage_start, coverage_end=coverage_end,
                                                                      where_kept=where_kept), "Recorded",
                dict(coverage_start=coverage_start, coverage_end=coverage_end, where_kept=where_kept))
