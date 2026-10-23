"""Sign in, sign out, and change your own password. The only public routes."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_user, safe_next, start_session
from app.database import get_db
from app.models.user import User
from app.services import user_service

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

LOGIN_FAILED = "Sign-in failed. Check your username and password."


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, next: str = "/", error: str = ""):
    return templates.TemplateResponse("login.html", {
        "request": request, "next": safe_next(next), "error": error,
    })


@router.post("/login")
def login(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
):
    user = user_service.authenticate(db, username, password)
    db.commit()  # keeps the failed-attempt counter and last_login_at
    if user is None:
        return templates.TemplateResponse("login.html", {
            "request": request, "next": safe_next(next), "error": LOGIN_FAILED,
        }, status_code=401)
    start_session(request, user)
    return RedirectResponse(url=safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


@router.get("/account/password", response_class=HTMLResponse)
def password_form(request: Request, user: User = Depends(require_user), error: str = ""):
    return templates.TemplateResponse("account/password.html", {
        "request": request, "error": error, "forced": user.must_change_password,
    })


@router.post("/account/password")
def change_password(
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    def again(message: str):
        return templates.TemplateResponse("account/password.html", {
            "request": request, "error": message, "forced": user.must_change_password,
        }, status_code=400)

    if not user_service.verify_password(user.password_hash, current_password):
        return again("Current password is wrong.")
    if new_password != confirm_password:
        return again("The new passwords do not match.")
    if new_password == current_password:
        return again("Choose a password different from the current one.")
    try:
        user_service.set_password(user, new_password, must_change=False)
    except ValueError as e:
        return again(str(e))
    db.commit()
    start_session(request, user)  # set_password ended the old session; keep this one
    return RedirectResponse(url="/?msg=Password+changed", status_code=303)
