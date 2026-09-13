"""
Review Queue — a mobile-first inbox for the owner to verify newly entered
data. Reuses Purchase.review_status and Transaction.review_status; no new
schema. See app/services/purchase_service.py for why a Unit created through
the purchase workflow never needs its own review item — its Purchase card
already carries the Unit's data (purchase.unit).

Two record types appear:
  - Purchases with review_status='pending'
  - Transactions with review_status='pending' that are linked to a Unit and
    are NOT a purchase's own acquisition transaction (excluded via
    Purchase.transaction_id, so the same dollar amount never shows twice)
"""
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.purchase import Purchase
from app.models.transaction import Transaction
from app.models.enums import ReviewStatus, TransactionType
from app.services.purchase_service import is_vehicle_purchase

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# What counts as an "expense" for review purposes — cost transactions only.
# Excludes sale/charge/collection/repair_revenue/parts_revenue: those are
# revenue events, not purchasing/spend data quality concerns.
_EXPENSE_TYPES = [
    TransactionType.purchase,
    TransactionType.materials_cost,
    TransactionType.labor_cost,
    TransactionType.overhead,
]


def _purchase_txn_ids(db: Session):
    return db.query(Purchase.transaction_id).filter(Purchase.transaction_id.isnot(None))


def _unit_linked_transactions(db: Session, status: ReviewStatus):
    return (
        db.query(Transaction)
        .filter(
            Transaction.review_status == status,
            Transaction.unit_id.isnot(None),
            Transaction.transaction_type.in_(_EXPENSE_TYPES),
            Transaction.id.notin_(_purchase_txn_ids(db)),
        )
        .all()
    )


def _pending_count(db: Session) -> int:
    return (
        db.query(Purchase).filter(Purchase.review_status == ReviewStatus.pending).count()
        + len(_unit_linked_transactions(db, ReviewStatus.pending))
    )


def _purchase_flags(purchase: Purchase) -> list:
    flags = []
    if not purchase.vendor:
        flags.append("Missing vendor")
    if not purchase.amount or purchase.amount <= Decimal("0"):
        flags.append("Zero or negative amount")
    if is_vehicle_purchase(purchase.category):
        if not purchase.unit_id:
            flags.append("Vehicle purchase has no linked unit")
        elif not purchase.unit.vin_serial:
            flags.append("Missing VIN")
        if not purchase.stock_number:
            flags.append("Missing stock number")
    return flags


def _transaction_flags(t: Transaction) -> list:
    flags = []
    if not t.vendor:
        flags.append("Missing vendor")
    if not t.amount or t.amount <= Decimal("0"):
        flags.append("Zero or negative amount")
    return flags


@router.get("/", response_class=HTMLResponse)
def review_queue(request: Request, db: Session = Depends(get_db), show: str = "all"):
    status = ReviewStatus.reviewed if show == "reviewed" else ReviewStatus.pending

    items = []

    if show in ("all", "purchases", "reviewed"):
        for p in db.query(Purchase).filter(Purchase.review_status == status).all():
            items.append({"kind": "purchase", "record": p, "date": p.purchase_date, "flags": _purchase_flags(p)})

    if show in ("all", "expenses", "reviewed"):
        for t in _unit_linked_transactions(db, status):
            items.append({"kind": "transaction", "record": t, "date": t.transaction_date, "flags": _transaction_flags(t)})

    # Flagged items first, then newest first.
    items.sort(key=lambda i: (0 if i["flags"] else 1, -(i["date"].toordinal() if i["date"] else 0)))

    return templates.TemplateResponse("review/queue.html", {
        "request": request,
        "items": items,
        "show": show,
        "pending_count": _pending_count(db),
    })


@router.get("/count")
def review_count(db: Session = Depends(get_db)):
    return JSONResponse({"count": _pending_count(db)})


@router.post("/purchases/{purchase_id}/reviewed")
def mark_purchase_reviewed(purchase_id: int, db: Session = Depends(get_db)):
    purchase = db.query(Purchase).filter(Purchase.id == purchase_id).first()
    if purchase:
        purchase.review_status = ReviewStatus.reviewed
        db.commit()
    return RedirectResponse(url="/review/?msg=Marked+reviewed", status_code=303)


@router.post("/transactions/{transaction_id}/reviewed")
def mark_transaction_reviewed(transaction_id: int, db: Session = Depends(get_db)):
    t = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if t:
        t.review_status = ReviewStatus.reviewed
        db.commit()
    return RedirectResponse(url="/review/?msg=Marked+reviewed", status_code=303)
