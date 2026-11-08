"""
Seed the Opening Snapshot DEMO data into a THROWAWAY database. Demo only.

Usage (spec section 18.7):
    python -m app.scripts.opening_snapshot_demo --credentials-file PATH

Refuses to run unless DATABASE_URL is a unix-socket connection (no network host)
to a database named rto_demo, so it can never touch the live database. Every
figure is labelled DEMO and uses made-up numbers; the real Workday 1 figures are
never seeded from code. Creates demo-admin and demo-employee with random
passwords written to --credentials-file (mode 600), never printed.
"""
import argparse
import io
import os
import secrets
import sys
from datetime import date
from urllib.parse import parse_qs, urlsplit

from app import config


def _refuse(message: str):
    print(f"Refusing to run: {message}", file=sys.stderr)
    raise SystemExit(2)


def check_database_url(url: str) -> None:
    parts = urlsplit(url)
    host_param = (parse_qs(parts.query).get("host") or [""])[0]
    if parts.hostname:
        _refuse("DATABASE_URL has a network host; the demo needs a unix-socket throwaway database.")
    if not host_param.startswith("/"):
        _refuse("DATABASE_URL must connect through a unix socket directory (?host=/path).")
    if parts.path.lstrip("/") != "rto_demo":
        _refuse("the database must be named rto_demo.")


class _Upload:
    def __init__(self, filename: str, data: bytes):
        self.filename, self.file = filename, io.BytesIO(data)


def _password() -> str:
    return "demo-" + "-".join(secrets.token_hex(2) for _ in range(3))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--credentials-file", required=True)
    args = ap.parse_args(argv)
    check_database_url(config.DATABASE_URL)

    from app.database import SessionLocal
    import app.models  # noqa: registers all models
    from app.models.enums import UserRole
    from app.services import opening_snapshot_service as svc, user_service

    db = SessionLocal()
    try:
        if svc.get_snapshot(db) is not None:
            _refuse("the demo database already has a snapshot.")
        creds = {}
        for username, display, role in (("demo-admin", "DEMO Admin", UserRole.admin),
                                        ("demo-employee", "DEMO Employee", UserRole.employee)):
            pw = _password()
            user_service.create_user(db, username=username, display_name=display, role=role,
                                     password=pw, must_change_password=False)
            creds[username] = pw
        admin = user_service.get_user_by_username(db, "demo-admin")

        snap = svc.create_snapshot(db, admin, name="DEMO Starting position", as_of_date=date(2026, 11, 2),
                                   label="Workday 1", cutoff_note="Unknown")

        def fig(**kw):
            base = dict(reported_by="DEMO Owner", reported_via="text message", reported_on=date(2026, 11, 2),
                        cutoff="Unknown", status="reported")
            base.update(kw)
            return svc.create_figure(db, snap, admin, **base)

        bank = fig(section="cash", label="DEMO Bank balances", amount="10000",
                   statement="DEMO: 10,000 in the bank, 500 in cash, 10,500 all together")
        fig(section="cash", label="DEMO Cash on hand", amount="500")
        fig(section="other_asset", label="DEMO Cash-owned cars", amount="100000", count="20",
            statement="DEMO value, basis not stated")
        fig(section="other_asset", label="DEMO Golf carts", amount="30000", count="6")
        fig(section="other_asset", label="DEMO Repair receivables", amount="12000")
        fig(section="debt", label="DEMO Floor-plan balance", amount="40000", count="10")
        fig(section="debt", label="DEMO Loan balance", amount="20000")
        fig(section="commitment", label="DEMO Weekly payroll (provisional)", amount="1000",
            due_note="Friday, date to confirm", statement="DEMO provisional payroll total, per the owner")
        for label, need in (("DEMO Bank account list", "Which accounts make up the bank balance"),
                            ("DEMO Split of the cash", "Drawer vs. owner cash"),
                            ("DEMO Other obligations", "Supplier bills, taxes, deposits, other loans")):
            fig(section="not_reported", label=label, status="pending", pending_need=need)

        svc.add_clarification(db, bank, admin, body="DEMO: read from the bank app, about 8 AM",
                              cutoff_update="Nov 2, about 8:00 AM, bank app", basis_update="statement balance",
                              refers_to_date=date(2026, 11, 2))
        svc.add_evidence(db, bank, admin, file=_Upload("demo-owner-text.txt", b"DEMO text message: 10,000 in the bank"),
                         kind="message_from_steve", description="DEMO copy of the owner's text",
                         no_pay_detail=True)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    fd = os.open(args.credentials_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        for username, pw in creds.items():
            f.write(f"{username} {pw}\n")
    print("Seeded the DEMO snapshot, users demo-admin and demo-employee, and one evidence file.")
    print(f"Credentials written to {args.credentials_file} (mode 600).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
