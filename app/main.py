from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db, Base, engine
import app.models  # noqa: registers all models with Base

from app.routes import units, customers, vendors, repair_jobs, sales, lease_accounts, payments, transactions, documents, exceptions, invoices

Base.metadata.create_all(bind=engine)

app = FastAPI(title="RTO Accounting System")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(units.router, prefix="/units", tags=["units"])
app.include_router(customers.router, prefix="/customers", tags=["customers"])
app.include_router(vendors.router, prefix="/vendors", tags=["vendors"])
app.include_router(repair_jobs.router, prefix="/repair-jobs", tags=["repair_jobs"])
app.include_router(sales.router, prefix="/sales", tags=["sales"])
app.include_router(lease_accounts.router, prefix="/lease-accounts", tags=["lease_accounts"])
app.include_router(payments.router, prefix="/payments", tags=["payments"])
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
app.include_router(documents.router, prefix="/documents", tags=["documents"])
app.include_router(exceptions.router, prefix="/exceptions", tags=["exceptions"])
app.include_router(invoices.router, prefix="/invoices", tags=["invoices"])

templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
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
