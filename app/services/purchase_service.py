"""
Purchase business logic — the Purchases module's single write path.

record_purchase()   — atomically creates the Purchase and its linked
                       Transaction. Finds-or-creates the Vendor by name and
                       best-effort matches an existing Unit by stock number.
sync_purchase_transaction() — re-applies a purchase's fields onto its linked
                       Transaction after an edit, so corrections in the
                       Purchases ledger are reflected in Transactions/exports.

Rules enforced:
  - A Vendor is always linked. If no Vendor with a matching name (case
    insensitive) exists, one is created — the employee never has to leave
    the purchase form to add a vendor.
  - A Unit is linked only on a clean match against Unit.unit_id or
    Unit.vin_serial. No Unit is ever fabricated; an unmatched stock number
    is still preserved as free text on the purchase.
  - business_line is derived from the expense category (never asked of the
    user) — RTO/Flip car categories map to 'car', golf cart categories map
    to 'golf_cart'.
  - transaction_type is always 'purchase'. The Transaction is the accounting
    ledger entry; Purchase is the durable record of what was submitted.

Caller must db.commit() after each function returns.
"""
from datetime import date as date_type
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.purchase import Purchase
from app.models.transaction import Transaction
from app.models.vendor import Vendor
from app.models.unit import Unit
from app.models.enums import PurchaseCategory

_GOLF_CART_CATEGORIES = {
    PurchaseCategory.parts_flip_golf_cart_build,
    PurchaseCategory.parts_golf_cart_repair,
    PurchaseCategory.auction_golf_cart_flip,
}


def business_line_for_category(category: PurchaseCategory) -> str:
    return "golf_cart" if category in _GOLF_CART_CATEGORIES else "car"


def find_or_create_vendor(name: str, db: Session) -> Vendor:
    name = name.strip()
    vendor = db.query(Vendor).filter(func.lower(Vendor.name) == name.lower()).first()
    if vendor:
        return vendor
    vendor = Vendor(name=name)
    db.add(vendor)
    db.flush()
    vendor.vendor_id = f"V-{vendor.id:04d}"
    return vendor


def match_unit_by_stock_number(stock_number: str, db: Session) -> Optional[Unit]:
    s = stock_number.strip()
    if not s:
        return None
    return db.query(Unit).filter(
        or_(func.lower(Unit.unit_id) == s.lower(), func.lower(Unit.vin_serial) == s.lower())
    ).first()


def record_purchase(
    *,
    purchase_date,
    purchaser: str,
    vendor_name: str,
    amount: Decimal,
    category: PurchaseCategory,
    site_location: str = "",
    stock_number: str = "",
    mileage: Optional[int] = None,
    description: str = "",
    entered_by: str = "",
    db: Session,
) -> Purchase:
    vendor = find_or_create_vendor(vendor_name, db)
    unit = match_unit_by_stock_number(stock_number, db) if stock_number else None
    business_line = business_line_for_category(category)

    purchase = Purchase(
        purchase_date=purchase_date,
        purchaser=purchaser,
        vendor_id=vendor.id,
        amount=amount,
        category=category,
        site_location=site_location or None,
        stock_number=stock_number or None,
        unit_id=unit.id if unit else None,
        mileage=mileage,
        description=description or None,
        entered_by=entered_by or None,
    )
    db.add(purchase)
    db.flush()
    purchase.purchase_id = f"PO-{purchase.id:04d}"

    t = Transaction(
        transaction_date=purchase_date,
        entry_date=date_type.today(),
        transaction_type="purchase",
        business_line=business_line,
        vendor_id=vendor.id,
        unit_id=unit.id if unit else None,
        amount=amount,
        description=description or category.value,
        category=category.name,
        entered_by=entered_by or None,
        coding_complete=True,
        review_status="pending",
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"

    purchase.transaction_id = t.id
    return purchase


def sync_purchase_transaction(purchase: Purchase, db: Session) -> None:
    """Push a purchase's current fields onto its linked Transaction after an edit."""
    t = purchase.transaction
    if not t:
        return
    t.transaction_date = purchase.purchase_date
    t.business_line = business_line_for_category(purchase.category)
    t.vendor_id = purchase.vendor_id
    t.unit_id = purchase.unit_id
    t.amount = purchase.amount
    t.description = purchase.description or purchase.category.value
    t.category = purchase.category.name
    t.entered_by = purchase.entered_by
