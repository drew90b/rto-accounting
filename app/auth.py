"""
Sign-in enforcement.

Every request re-reads the user from the database, so disabling an account or
changing a role takes effect on the very next click. The session cookie is
signed (not encrypted) and holds only uid, ver (the user's session_version)
and login_at; nothing sensitive.

  require_user   - signed in; sends a user who must change their password to
                   /account/password first.
  require_admin  - require_user, then 403 for anyone who is not an admin.

app.main applies these to whole routers (default-deny). Menu items hidden in
base.html are cosmetic; these dependencies are the actual enforcement.
"""

import time
from typing import Optional
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import config
from app.database import get_db
from app.models.user import User

# Reachable while a password change is pending.
_PASSWORD_CHANGE_PATHS = {"/account/password", "/logout"}


def safe_next(path: Optional[str]) -> str:
    """Only same-site relative paths; never an absolute or protocol-relative URL."""
    if path and path.startswith("/") and not path.startswith("//") and "\\" not in path:
        return path
    return "/"


def start_session(request: Request, user: User) -> None:
    request.session.clear()
    request.session["uid"] = user.id
    request.session["ver"] = user.session_version
    request.session["login_at"] = int(time.time())


def _user_from_session(request: Request, db: Session) -> Optional[User]:
    session = request.session
    uid, ver, login_at = session.get("uid"), session.get("ver"), session.get("login_at")
    if not isinstance(uid, int) or not isinstance(login_at, int):
        return None
    if time.time() - login_at > config.SESSION_MAX_HOURS * 3600:
        return None
    user = db.query(User).filter(User.id == uid).first()
    if user is None or not user.active or user.session_version != ver:
        return None
    return user


def _redirect(location: str) -> HTTPException:
    return HTTPException(status_code=303, headers={"Location": location})


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = _user_from_session(request, db)
    if user is None:
        request.session.clear()
        if request.method == "GET":
            target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            raise _redirect(f"/login?next={quote(target, safe='/')}" if target != "/" else "/login")
        raise _redirect("/login")
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_PATHS:
        raise _redirect("/account/password")
    request.state.user = user
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user
