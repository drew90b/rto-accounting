"""
Monthly Review workspace (admin only; the router is mounted with require_admin).

One page per month: close workday and progress, records received and missing, issues with owners, supported figures,
reconciliation requirements, and owner review notes. It REVIEWS a month; it never reconciles, verifies or closes one, and it
writes only the review_* tables (docs/specs/cash-financial-control-spec.md, section 20).
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models.monthly_review import ReviewIssue
from app.models.user import User
from app.services import business_calendar as bc
from app.services import close_progress as cp
from app.services import monthly_review_service as svc
from app.services import review_period as rp

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
BASE = "/finance/review"


def _local(dt) -> str:
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc).astimezone(bc.TIMEZONE).strftime("%b %-d, %Y %-I:%M %p") if dt else ""


def _day(d) -> str:
    return d.strftime("%a %b %-d, %Y") if d else ""


def _href(h: str, month_key: str) -> str:
    """Anchors point into the review page for the month; absolute paths pass through unchanged."""
    return f"{BASE}/?month={month_key}{h}" if h.startswith("#") else h


templates.env.filters.update({"mrlocal": _local, "mrday": _day, "mrhref": _href})
templates.env.globals.update(STATUS_LABEL=svc.STATUS_LABEL, CATEGORIES=svc.CATEGORIES, CHECKLIST=svc.CHECKLIST, SOURCES=svc.SOURCES,
                             STEP_LABEL=cp.STATUS_LABEL)


def _month(raw: Optional[str], today: date) -> date:
    return rp.parse_month(raw, today)


def _render(request: Request, db: Session, month: date, *, status_code: int = 200, error: Optional[str] = None,
            form: Optional[dict] = None, duplicate: Optional[ReviewIssue] = None, template: str = "monthly_review/index.html"):
    today = bc.chicago_today()
    data = svc.build_review(db, month, today, sync=(template == "monthly_review/index.html" and request.method == "GET"))
    db.commit()
    p = data["period"]
    return templates.TemplateResponse(template, {
        "request": request, "d": data, "p": p, "month_key": p.key, "prev_month": rp.add_months(p.month, -1).strftime("%Y-%m"),
        "next_month": rp.add_months(p.month, 1).strftime("%Y-%m"), "error": error, "form": form or {}, "duplicate": duplicate,
        "attest": request.query_params.get("attest", ""), "base": BASE, "today": today,
        "msg": (request.query_params.get("msg") or "").replace("+", " "),
        "holidays": sorted((d_, n_) for y in (p.month.year, p.month.year + 1) for d_, n_ in bc.ACTIVE.holidays_for(y).items()
                           if p.month <= d_ <= max([a["date"] for a in data["schedule"]] + [p.end])),
    }, status_code=status_code)


def _back(month: date, msg: str, anchor: str = "") -> RedirectResponse:
    from urllib.parse import quote_plus
    return RedirectResponse(f"{BASE}/?month={month.strftime('%Y-%m')}&msg={quote_plus(msg)}{anchor}", status_code=303)


def _bad_month(request: Request, today: date, e: Exception):
    return templates.TemplateResponse("monthly_review/error.html", {"request": request, "message": str(e), "base": BASE}, status_code=400)


@router.get("/", response_class=HTMLResponse)
def index(request: Request, month: Optional[str] = None, db: Session = Depends(get_db)):
    today = bc.chicago_today()
    try:
        m = _month(month, today)
    except rp.BadMonth as e:
        return _bad_month(request, today, e)
    return _render(request, db, m)


@router.get("/schedule", response_class=HTMLResponse)
def schedule(request: Request, month: Optional[str] = None, db: Session = Depends(get_db)):
    today = bc.chicago_today()
    try:
        m = _month(month, today)
    except rp.BadMonth as e:
        return _bad_month(request, today, e)
    return _render(request, db, m, template="monthly_review/schedule.html")


def _post(request, db, month_raw, fn, ok_msg, anchor="", form=None):
    """Run a write; on a refusal re-render the page with the reason and the user's input."""
    today = bc.chicago_today()
    try:
        m = _month(month_raw, today)
    except rp.BadMonth as e:
        return _bad_month(request, today, e)
    try:
        fn(m, today)
        db.commit()
    except svc.DuplicateIssue as e:
        db.rollback()
        return _render(request, db, m, status_code=409, error=str(e), form=form, duplicate=e.existing)
    except svc.ReviewError as e:
        db.rollback()
        return _render(request, db, m, status_code=400, error=str(e), form=form)
    return _back(m, ok_msg, anchor)


@router.post("/issues")
def create_issue(request: Request, month: str = Form(""), title: str = Form(""), description: str = Form(""), category: str = Form("other"),
                 checklist_ref: str = Form(""), owner_id: str = Form(""), due_date: str = Form(""), link: str = Form(""),
                 message_to_owner: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    form = dict(title=title, description=description, category=category, checklist_ref=checklist_ref, owner_id=owner_id,
                due_date=due_date, link=link, message_to_owner=message_to_owner)
    return _post(request, db, month,
                 lambda m, t: svc.create_manual_issue(db, user, m, title=title, description=description, category=category,
                                                      checklist_ref=checklist_ref, owner_id=owner_id, due_date=due_date, link=link,
                                                      message_to_owner=message_to_owner),
                 "Issue added", "#attention", form)


@router.post("/issues/{issue_pk}/update")
def update_issue(request: Request, issue_pk: int, month: str = Form(""), owner_id: str = Form("__keep__"), status: str = Form(""),
                 due_date: str = Form("__keep__"), note: str = Form(""), message_to_owner: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    issue = db.query(ReviewIssue).filter(ReviewIssue.id == issue_pk).first()
    if issue is None:
        return templates.TemplateResponse("monthly_review/error.html", {"request": request, "message": "That issue doesn't exist.", "base": BASE}, status_code=404)
    kw = {}
    if owner_id != "__keep__":
        kw["owner_id"] = owner_id
    if due_date != "__keep__":
        kw["due_date"] = due_date
    # the issue's own month decides which page we return to
    return _post(request, db, issue.month.strftime("%Y-%m"),
                 lambda m, t: svc.update_issue(db, issue, user, status=status or None, note=note, message_to_owner=message_to_owner, **kw), "Issue updated", f"#issue-{issue.issue_id}")


@router.post("/notes")
def add_note(request: Request, month: str = Form(""), note_type: str = Form("review"), body: str = Form(""), review_date: str = Form(""),
             corrects_note_id: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _post(request, db, month,
                 lambda m, t: svc.add_note(db, user, m, note_type=note_type, body=body, review_date=review_date,
                                           corrects_note_id=corrects_note_id, today=t),
                 "Note saved. This records a review, not a verification or a close.", "#owner-review",
                 dict(note_type=note_type, body=body, review_date=review_date))


@router.post("/records")
def add_record(request: Request, month: str = Form(""), source_key: str = Form(""), status: str = Form("received"),
               coverage_start: str = Form(""), coverage_end: str = Form(""), where_kept: str = Form(""), note: str = Form(""),
               pay_date: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _post(request, db, month,
                 lambda m, t: svc.add_attestation(db, user, m, source_key=source_key, status=status, coverage_start=coverage_start,
                                                  coverage_end=coverage_end, where_kept=where_kept, note=note, pay_date=pay_date),
                 "Recorded. The app notes that it exists; it does not hold or check the file.", "#records",
                 dict(source_key=source_key, status=status, coverage_start=coverage_start, coverage_end=coverage_end,
                      where_kept=where_kept, note=note, pay_date=pay_date))


@router.post("/records/{record_id}/confirm")
def confirm_record(request: Request, record_id: int, month: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _post(request, db, month, lambda m, t: svc.confirm_not_required(db, user, m, record_id),
                 "Confirmed. Two administrators have now agreed it is not required this month.", "#records")


@router.post("/steps/{step_key}")
def step_mark(request: Request, step_key: str, month: str = Form(""), kind: str = Form(""), action: str = Form("set"), note: str = Form(""),
              evidence: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return _post(request, db, month,
                 lambda m, t: svc.set_step_mark(db, user, m, t, step_key=step_key, kind=kind, action=action, note=note, evidence=evidence),
                 "Recorded. The step is complete only when every one of its milestones is satisfied.", f"#step-{step_key}")
