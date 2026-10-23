"""
User accounts and password handling.

Accounts are managed only from the command line (app/scripts/users.py);
there is no signup or user-admin page. This module holds the rules both the
CLI and the sign-in code share:

  - Passwords are hashed with argon2 and are at least MIN_PASSWORD_LENGTH long.
  - Usernames are stored lowercase.
  - Resetting a password, disabling an account, or changing a role bumps
    session_version, which ends that user's other sessions.
  - The last active admin can never be disabled or demoted, so the app can
    not lock itself out.
  - Five wrong passwords lock the account for LOCKOUT_MINUTES. The sign-in
    error is the same for an unknown user and a wrong password.
"""

from datetime import datetime, timedelta
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError
from sqlalchemy.orm import Session

from app.models.enums import UserRole
from app.models.user import User

MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15

_hasher = PasswordHasher()
# Checked when the username is unknown so that case takes as long as a wrong password.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def get_user_by_username(db: Session, username: str) -> Optional[User]:
    return db.query(User).filter(User.username == username.strip().lower()).first()


def create_user(
    db: Session, *, username: str, display_name: str, role: UserRole,
    password: str, must_change_password: bool,
) -> User:
    username = username.strip().lower()
    if not username or not display_name.strip():
        raise ValueError("Username and display name are required.")
    if get_user_by_username(db, username):
        raise ValueError(f"User '{username}' already exists.")
    user = User(
        username=username,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        role=role,
        must_change_password=must_change_password,
    )
    db.add(user)
    db.flush()
    return user


def set_password(user: User, password: str, *, must_change: bool) -> None:
    user.password_hash = hash_password(password)
    user.must_change_password = must_change
    user.failed_login_count = 0
    user.locked_until = None
    user.session_version += 1


def _active_admin_count(db: Session) -> int:
    return db.query(User).filter(User.role == UserRole.admin, User.active.is_(True)).count()


def _would_remove_last_admin(db: Session, user: User) -> bool:
    return user.role == UserRole.admin and user.active and _active_admin_count(db) <= 1


def disable_user(db: Session, user: User) -> None:
    if _would_remove_last_admin(db, user):
        raise ValueError("Refusing to disable the last active admin.")
    user.active = False
    user.session_version += 1


def enable_user(user: User) -> None:
    user.active = True


def set_role(db: Session, user: User, role: UserRole) -> None:
    if role != UserRole.admin and _would_remove_last_admin(db, user):
        raise ValueError("Refusing to demote the last active admin.")
    user.role = role
    user.session_version += 1


def unlock_user(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None


def is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.utcnow())


def record_failure(user: User) -> None:
    """Count a wrong password; lock the account after MAX_FAILED_LOGINS in a row."""
    user.failed_login_count += 1
    if user.failed_login_count >= MAX_FAILED_LOGINS:
        user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        user.failed_login_count = 0


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    """Return the user if the credentials are good, else None. Records failures."""
    user = get_user_by_username(db, username)
    if user is None:
        verify_password(_DUMMY_HASH, password)
        return None
    now = datetime.utcnow()
    if user.locked_until and user.locked_until > now:
        verify_password(_DUMMY_HASH, password)
        return None
    if not user.active or not verify_password(user.password_hash, password):
        if user.active:
            record_failure(user)
        return None
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(password)
    return user
