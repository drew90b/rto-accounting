from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import config
from app.auth import require_user, require_admin
from app.csrf import OriginCheckMiddleware
from app.database import get_db
from app.models.user import User
import app.models  # noqa: registers all models with Base

from app.routes import units, customers, vendors, repair_jobs, sales, lease_accounts, payments, transactions, documents, exceptions, invoices, purchases, review, auth

if len(config.SESSION_SECRET) < 32:
    raise RuntimeError(
        "SESSION_SECRET must be set (at least 32 characters). "
        "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'"
    )

# The API docs would show the whole route map to anyone, signed in or not.
app = FastAPI(title="RTO Accounting System", docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(OriginCheckMiddleware, allowed_origins=config.ALLOWED_ORIGINS)
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="__Host-rto_session" if config.SESSION_COOKIE_SECURE else "rto_session",
    max_age=config.SESSION_MAX_HOURS * 3600,
    same_site="lax",
    https_only=config.SESSION_COOKIE_SECURE,
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Default-deny: signing in is required everywhere except /login, and every
# module other than Purchases and Documents is administrator-only. Purchases
# and Documents are open to employees, with admin-only routes inside them.
_USER = [Depends(require_user)]
_ADMIN = [Depends(require_admin)]

app.include_router(auth.router, tags=["auth"])
app.include_router(review.router, prefix="/review", tags=["review"], dependencies=_ADMIN)
app.include_router(purchases.router, prefix="/purchases", tags=["purchases"], dependencies=_USER)
app.include_router(units.router, prefix="/units", tags=["units"], dependencies=_ADMIN)
app.include_router(customers.router, prefix="/customers", tags=["customers"], dependencies=_ADMIN)
app.include_router(vendors.router, prefix="/vendors", tags=["vendors"], dependencies=_ADMIN)
app.include_router(repair_jobs.router, prefix="/repair-jobs", tags=["repair_jobs"], dependencies=_ADMIN)
app.include_router(sales.router, prefix="/sales", tags=["sales"], dependencies=_ADMIN)
app.include_router(lease_accounts.router, prefix="/lease-accounts", tags=["lease_accounts"], dependencies=_ADMIN)
app.include_router(payments.router, prefix="/payments", tags=["payments"], dependencies=_ADMIN)
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"], dependencies=_ADMIN)
app.include_router(documents.router, prefix="/documents", tags=["documents"], dependencies=_USER)
app.include_router(exceptions.router, prefix="/exceptions", tags=["exceptions"], dependencies=_ADMIN)
app.include_router(invoices.router, prefix="/invoices", tags=["invoices"], dependencies=_ADMIN)

templates = Jinja2Templates(directory="app/templates")


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 403 and "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse("forbidden.html", {"request": request}, status_code=403)
    from fastapi.exception_handlers import http_exception_handler
    return await http_exception_handler(request, exc)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    if not user.is_admin:
        return RedirectResponse(url="/purchases/new", status_code=303)
    from app.models.exception_record import ExceptionRecord
    from app.models.lease_account import LeaseAccount
    from app.models.enums import ExceptionStatus
    from app.services.lease_service import build_balance_map
    from app.services.dashboard_service import (
        cash_collected_this_month,
        delinquent_balance_total,
        inventory_investment,
    )

    # Active exceptions = open + in_review (both require attention)
    active_exception_count = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.status.in_([ExceptionStatus.open, ExceptionStatus.in_review]))
        .count()
    )

    metrics = {
        "cash_this_month": cash_collected_this_month(db),
        "delinquent_total": delinquent_balance_total(db),
        "inventory_investment": inventory_investment(db),
        "active_exceptions": active_exception_count,
    }

    open_exceptions = (
        db.query(ExceptionRecord)
        .filter(ExceptionRecord.status.in_([ExceptionStatus.open, ExceptionStatus.in_review]))
        .order_by(ExceptionRecord.opened_date.desc())
        .limit(10)
        .all()
    )

    delinquent_leases = (
        db.query(LeaseAccount)
        .filter(LeaseAccount.delinquency_status.in_(["late", "delinquent", "default"]))
        .order_by(LeaseAccount.delinquency_status.desc())
        .limit(10)
        .all()
    )

    return templates.TemplateResponse("index.html", {
        "request": request,
        "metrics": metrics,
        "open_exceptions": open_exceptions,
        "delinquent_leases": delinquent_leases,
        "delinquent_balances": build_balance_map(delinquent_leases, db),
    })
