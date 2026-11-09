"""
Employee-facing vehicle questions: only the questions ASSIGNED to the signed-in user, of an employee-eligible kind, with a
restricted evidence view (no cost, purchase amount, customer, balance or payroll). Anything else answers 404 so its existence is
not revealed. An employee's answer to an identity question is a PROPOSAL until an administrator accepts it (REQ-VR-12, -16).

Integration: vehicle_recon_service.assigned_items() lists these for the Monthly Review "My actions" view once both are merged.
"""
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_user
from app.database import get_db
from app.models.user import User
from app.services import vehicle_recon_service as svc

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/mine", response_class=HTMLResponse)
def mine(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return templates.TemplateResponse("vehicles/mine.html", {"request": request, "items": svc.my_questions(db, user)})


def _page(request, db, user, ref, error=None, saved=False, status=200):
    q = svc.get_question(db, user, ref)
    if q is None or q.owner_user_id != user.id and not user.is_admin:
        raise HTTPException(404, "Not found.")
    names = svc.user_names(db)
    return templates.TemplateResponse("vehicles/mine_question.html", {
        "request": request, "v": svc.employee_view(q), "q": q, "error": error, "saved": saved,
        "history": [h for h in svc.question_history(db, q, names) if h["type"] in ("raised", "answered", "deferred", "accepted", "rejected", "assigned")],
        "today": date.today().isoformat(), "waiting": q.status == "proposed",
    }, status_code=status)


@router.get("/mine/{ref}", response_class=HTMLResponse)
def mine_question(ref: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user), saved: int = 0):
    return _page(request, db, user, ref, saved=bool(saved))


@router.post("/mine/{ref}/answer")
def mine_answer(ref: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user),
                choice: str = Form(...), expected_version: int = Form(...), expected_fp: str = Form(...),
                as_of: str = Form(""), note: str = Form("")):
    if svc.get_question(db, user, ref) is None:
        raise HTTPException(404, "Not found.")
    try:
        svc.answer_question(db, user, ref, choice, expected_version, expected_fp, {"as_of": as_of}, note)
        db.commit()
    except svc.NotAllowed:
        db.rollback()
        raise HTTPException(404, "Not found.")
    except svc.RecError as e:
        db.rollback()
        return _page(request, db, user, ref, error=str(e), status=409 if isinstance(e, svc.StaleAnswer) else 400)
    return RedirectResponse(f"/vehicle-questions/mine/{ref}?saved=1", status_code=303)
