"""
Manage sign-in accounts. This is the only way to create or change users.

Usage:
    python -m app.scripts.users create --username belinda --display-name "Belinda" --role employee [--temporary]
    python -m app.scripts.users reset-password USER [--temporary]
    python -m app.scripts.users disable USER
    python -m app.scripts.users enable USER
    python -m app.scripts.users set-role USER admin|employee
    python -m app.scripts.users unlock USER
    python -m app.scripts.users list

Passwords are never taken as arguments (they would land in shell history).
They are typed twice at a prompt, or --temporary generates a random one,
prints it once, and forces a change at first sign-in. Hashes are never shown.
"""

import argparse
import getpass
import secrets
import sys

from app.database import SessionLocal
from app.models.enums import UserRole
import app.models  # noqa: registers all models
from app.services import user_service


def _new_password(temporary: bool):
    """Return (password, must_change)."""
    if temporary:
        return secrets.token_urlsafe(12), True
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat password: "):
        raise ValueError("Passwords did not match.")
    return first, False


def _find(db, username):
    user = user_service.get_user_by_username(db, username)
    if user is None:
        raise ValueError(f"No such user: {username}")
    return user


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage sign-in accounts.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create")
    p.add_argument("--username", required=True)
    p.add_argument("--display-name", required=True)
    p.add_argument("--role", required=True, choices=[r.value for r in UserRole])
    p.add_argument("--temporary", action="store_true")

    p = sub.add_parser("reset-password")
    p.add_argument("username")
    p.add_argument("--temporary", action="store_true")

    for name in ("disable", "enable", "unlock"):
        sub.add_parser(name).add_argument("username")

    p = sub.add_parser("set-role")
    p.add_argument("username")
    p.add_argument("role", choices=[r.value for r in UserRole])

    sub.add_parser("list")

    args = parser.parse_args(argv)
    db = SessionLocal()
    try:
        if args.command == "list":
            from app.models.user import User
            for u in db.query(User).order_by(User.username).all():
                locked = u.locked_until is not None
                print(f"{u.username}\t{u.role.value}\t{'active' if u.active else 'disabled'}\t{'locked' if locked else ''}")
            return 0

        if args.command == "create":
            password, must_change = _new_password(args.temporary)
            user_service.create_user(
                db, username=args.username, display_name=args.display_name,
                role=UserRole(args.role), password=password, must_change_password=must_change,
            )
            message = f"Created {args.username.strip().lower()} ({args.role})."
        else:
            user = _find(db, args.username)
            if args.command == "reset-password":
                password, must_change = _new_password(args.temporary)
                user_service.set_password(user, password, must_change=must_change)
                message = f"Password reset for {user.username}."
            elif args.command == "disable":
                user_service.disable_user(db, user)
                message = f"Disabled {user.username}."
            elif args.command == "enable":
                user_service.enable_user(user)
                message = f"Enabled {user.username}."
            elif args.command == "set-role":
                user_service.set_role(db, user, UserRole(args.role))
                message = f"{user.username} is now {args.role}."
            else:
                user_service.unlock_user(user)
                message = f"Unlocked {user.username}."
        db.commit()
    except ValueError as e:
        db.rollback()
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(message)
    if getattr(args, "temporary", False) and args.command in ("create", "reset-password"):
        print(f"Temporary password (shown once): {password}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
