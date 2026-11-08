"""
Vehicle reconciliation: the reconciled list, vehicle detail and the question queue. ADMINISTRATOR ONLY, enforced for the whole
router in app.main (default-deny). Employees use app/routes/vehicle_questions.py, which exposes only their own assigned
questions with restricted evidence. Spec: docs/specs/vehicle-reconciliation-spec.md (REQ-VR-16).
"""
from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models.user import User
from app.models.vehicle_recon import VrQuestion
from app.services import vehicle_recon_service as svc
from app.services import vr_engine as eng

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

PAGE_SIZE = 50


def cost_label(rec) -> str:
    """Reported zero is not 'free' and a missing cost is not zero (REQ-VR-10)."""
    if rec is None or rec.reported_cost is None:
        return "Not reported"
    c = Decimal(rec.reported_cost)
    if c == 0:
        return "$0.00 reported (zero as reported; not confirmed free)"
    return f"${c:,.2f}"


templates.env.globals["vr_cost_label"] = cost_label
templates.env.globals["vr_state_label"] = eng.STATE_LABEL


def _users(db):
    return db.query(User).filter(User.active.is_(True)).order_by(User.display_name).all()


@router.get("/", response_class=HTMLResponse)
def vehicle_list(request: Request, db: Session = Depends(get_db), search: str = "", state: str = "", owner: str = "",
                 has_question: str = "", kind: str = "", page: int = 1):
    rows, w, names = svc.reconciled(db)
    total_all = len(rows)
    rows = svc.filter_rows(rows, search, state, owner, has_question, kind)
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 1), pages)
    counts = {s: sum(1 for r in rows if r.state == s) for s in (eng.STATE_AGREE, eng.STATE_REVIEW, eng.STATE_LACK, eng.STATE_RESOLVED)}
    return templates.TemplateResponse("vehicles/list.html", {
        "request": request, "rows": rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], "total": len(rows), "total_all": total_all,
        "counts": counts, "page": page, "pages": pages, "search": search, "state": state, "owner": owner,
        "has_question": has_question, "kind": kind, "users": _users(db), "loaded": w.loaded, "latest": w.latest,
        "open_questions": db.query(VrQuestion).filter(VrQuestion.status.in_(eng.LIVE_QUESTION_STATUSES)).count(),
    })


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db)):
    """Re-reads the app's units and purchases (read only) and regenerates questions. Repeating it changes nothing."""
    svc.refresh(db)
    db.commit()
    return RedirectResponse("/vehicles/?refreshed=1", status_code=303)


@router.get("/queue", response_class=HTMLResponse)
def queue(request: Request, db: Session = Depends(get_db), status: str = "live", kind: str = "", owner: str = ""):
    names = svc.user_names(db)
    q = db.query(VrQuestion)
    if status == "live":
        q = q.filter(VrQuestion.status.in_(eng.LIVE_QUESTION_STATUSES))
    elif status:
        q = q.filter(VrQuestion.status == status)
    if kind:
        q = q.filter(VrQuestion.kind == kind)
    if owner == "unassigned":
        q = q.filter(VrQuestion.owner_user_id.is_(None))
    elif owner.isdigit():
        q = q.filter(VrQuestion.owner_user_id == int(owner))
    rank = {"review_needed": 0, "proposed": 1, "open": 2}
    items = sorted(q.all(), key=lambda x: (rank.get(x.status, 9), x.id))
    return templates.TemplateResponse("vehicles/queue.html", {
        "request": request, "items": items, "names": names, "status": status, "kind": kind, "owner": owner,
        "users": _users(db), "kind_class": svc.KIND_CLASS,
    })


@router.get("/v/{key:path}", response_class=HTMLResponse)
def vehicle_detail(key: str, request: Request, db: Session = Depends(get_db)):
    rows, w, names = svc.reconciled(db)
    row = svc.find_row(rows, key)
    if row is None:
        raise HTTPException(404, "No such vehicle.")
    histories = {q.ref: svc.question_history(db, q, names) for q in row.questions}
    return templates.TemplateResponse("vehicles/detail.html", {
        "request": request, "row": row, "names": names, "histories": histories, "w": w, "snap_dates": w.snap_dates,
        "kind_class": svc.KIND_CLASS,
    })


def _question_context(request, db, q, names, error=None, saved=False):
    decisions = svc.decisions_for(db, q)
    return {
        "request": request, "q": q, "names": names, "decisions": decisions,
        "live": next((d for d in decisions if d.id == q.current_decision_id), None),
        "history": svc.question_history(db, q, names), "users": _users(db), "error": error, "saved": saved,
        "kind_class": svc.KIND_CLASS, "today": date.today().isoformat(), "assignable_to_employee": q.kind in svc.EMPLOYEE_KINDS,
    }


def _get_q(db, ref) -> VrQuestion:
    q = db.query(VrQuestion).filter(VrQuestion.ref == ref).first()
    if q is None:
        raise HTTPException(404, "No such question.")
    return q


@router.get("/q/{ref}", response_class=HTMLResponse)
def question_page(ref: str, request: Request, db: Session = Depends(get_db), saved: int = 0):
    q = _get_q(db, ref)
    return templates.TemplateResponse("vehicles/question.html", _question_context(request, db, q, svc.user_names(db), saved=bool(saved)))


def _fail(request, db, q, e: Exception):
    status = 409 if isinstance(e, svc.StaleAnswer) else 400
    return templates.TemplateResponse("vehicles/question.html", _question_context(request, db, q, svc.user_names(db), error=str(e)),
                                      status_code=status)


@router.post("/q/{ref}/answer")
def answer(ref: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
           choice: str = Form(...), expected_version: int = Form(...), expected_fp: str = Form(...), as_of: str = Form(""),
           other_stock: str = Form(""), note: str = Form(""), reason: str = Form("")):
    q = _get_q(db, ref)
    try:
        svc.answer_question(db, user, ref, choice, expected_version, expected_fp, {"as_of": as_of, "other_stock": other_stock},
                            note, reason)
        db.commit()
    except svc.RecError as e:
        db.rollback()
        return _fail(request, db, q, e)
    return RedirectResponse(f"/vehicles/q/{ref}?saved=1", status_code=303)


@router.post("/q/{ref}/accept")
def accept(ref: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
           expected_version: int = Form(...), expected_fp: str = Form(...)):
    q = _get_q(db, ref)
    try:
        svc.accept_decision(db, user, ref, expected_version, expected_fp)
        db.commit()
    except svc.RecError as e:
        db.rollback()
        return _fail(request, db, q, e)
    return RedirectResponse(f"/vehicles/q/{ref}?saved=1", status_code=303)


@router.post("/q/{ref}/reject")
def reject(ref: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
           expected_version: int = Form(...), expected_fp: str = Form(...), reason: str = Form("")):
    q = _get_q(db, ref)
    try:
        svc.reject_decision(db, user, ref, reason, expected_version, expected_fp)
        db.commit()
    except svc.RecError as e:
        db.rollback()
        return _fail(request, db, q, e)
    return RedirectResponse(f"/vehicles/q/{ref}?saved=1", status_code=303)


@router.post("/q/{ref}/assign")
def assign(ref: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin),
           owner_user_id: Optional[str] = Form("")):
    q = _get_q(db, ref)
    try:
        svc.assign(db, user, ref, int(owner_user_id) if owner_user_id and owner_user_id.isdigit() else None)
        db.commit()
    except svc.RecError as e:
        db.rollback()
        return _fail(request, db, q, e)
    return RedirectResponse(f"/vehicles/q/{ref}?saved=1", status_code=303)
