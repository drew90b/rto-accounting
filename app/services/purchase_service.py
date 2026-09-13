"""
Purchase business logic — the Purchases module's single write path.

record_purchase()   — atomically creates the Purchase and its linked
                       Transaction. Finds-or-creates the Vendor by name.
                       For a vehicle purchase (an auction_* category), also
                       finds-or-creates the Unit it represents — this is the
                       single entry point for adding a vehicle to inventory.
                       For every other category, Unit is linked only on a
                       best-effort stock-number match, same as before.
sync_purchase_transaction() — re-applies a purchase's fields onto its linked
                       Transaction (and, for a vehicle purchase, its Unit's
                       acquisition_cost) after an edit, so corrections in the
                       Purchases ledger are reflected everywhere else.

Rules enforced:
  - A Vendor is always linked. If no Vendor with a matching name (case
    insensitive) exists, one is created — the employee never has to leave
    the purchase form to add a vendor.
  - business_line is derived from the expense category (never asked of the
    user) — RTO/Flip car categories map to 'car', golf cart categories map
    to 'golf_cart'.
  - Vehicle purchases (auction_car_rto, auction_car_flip, auction_golf_cart_flip)
    always end up linked to a Unit: an existing one (matched by stock number
    or VIN) or a newly created one. No Unit is ever fabricated for any other
    category — those still only link on a best-effort stock-number match.
  - Acquisition cost = purchase amount + shipping_cost + other_fees. It is
    computed once when the Unit is created (and re-synced on edit), never a
    separately-entered total.
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
from app.models.enums import PurchaseCategory, UnitType

_GOLF_CART_CATEGORIES = {
    PurchaseCategory.parts_flip_golf_cart_build,
    PurchaseCategory.parts_golf_cart_repair,
    PurchaseCategory.auction_golf_cart_flip,
}

_VEHICLE_CATEGORIES = {
    PurchaseCategory.auction_car_rto,
    PurchaseCategory.auction_car_flip,
    PurchaseCategory.auction_golf_cart_flip,
}


def business_line_for_category(category: PurchaseCategory) -> str:
    return "golf_cart" if category in _GOLF_CART_CATEGORIES else "car"


def is_vehicle_purchase(category: PurchaseCategory) -> bool:
    return category in _VEHICLE_CATEGORIES


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


def resolve_vehicle_unit(stock_number: str, vin: str, db: Session) -> Optional[Unit]:
    """
    Find the existing Unit a vehicle purchase belongs to, checking both the
    submitted stock number (against Unit.unit_id) and VIN (against
    Unit.vin_serial). Returns None when neither matches anything — the
    caller should create a new Unit in that case.

    Raises ValueError when stock number and VIN point to two different
    existing units — an unresolvable conflict the employee needs to fix by
    hand rather than something safe to guess at.
    """
    by_stock = match_unit_by_stock_number(stock_number, db) if stock_number else None
    vin = vin.strip()
    by_vin = (
        db.query(Unit).filter(func.lower(Unit.vin_serial) == vin.lower()).first()
        if vin else None
    )
    if by_stock and by_vin and by_stock.id != by_vin.id:
        raise ValueError(
            f"Stock number matches unit {by_stock.unit_id} but VIN matches unit "
            f"{by_vin.unit_id} — those are two different units. Fix the stock "
            f"number or VIN before saving."
        )
    return by_vin or by_stock


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
    shipping_cost: Decimal = Decimal("0"),
    other_fees: Decimal = Decimal("0"),
    vin: str = "",
    year: Optional[int] = None,
    make: str = "",
    model: str = "",
    db: Session,
) -> Purchase:
    vendor = find_or_create_vendor(vendor_name, db)
    business_line = business_line_for_category(category)

    if is_vehicle_purchase(category):
        unit = resolve_vehicle_unit(stock_number, vin, db)
        acquisition_cost = amount + shipping_cost + other_fees
        if unit is None:
            unit = Unit(
                unit_type=UnitType.car if business_line == "car" else UnitType.golf_cart,
                business_line=business_line,
                vin_serial=vin.strip() or None,
                year=year,
                make=make or None,
                model=model or None,
                purchase_date=purchase_date,
                purchase_source=vendor.name,
                acquisition_cost=acquisition_cost,
                notes=description or None,
            )
            db.add(unit)
            db.flush()
            unit.unit_id = f"U-{unit.id:04d}"
        transaction_amount = acquisition_cost
    else:
        unit = match_unit_by_stock_number(stock_number, db) if stock_number else None
        transaction_amount = amount

    purchase = Purchase(
        purchase_date=purchase_date,
        purchaser=purchaser,
        vendor_id=vendor.id,
        amount=amount,
        shipping_cost=shipping_cost or None,
        other_fees=other_fees or None,
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
        amount=transaction_amount,
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
    total = purchase.amount + (purchase.shipping_cost or Decimal("0")) + (purchase.other_fees or Decimal("0"))

    t = purchase.transaction
    if t:
        t.transaction_date = purchase.purchase_date
        t.business_line = business_line_for_category(purchase.category)
        t.vendor_id = purchase.vendor_id
        t.unit_id = purchase.unit_id
        t.amount = total
        t.description = purchase.description or purchase.category.value
        t.category = purchase.category.name
        t.entered_by = purchase.entered_by

    if is_vehicle_purchase(purchase.category) and purchase.unit:
        purchase.unit.acquisition_cost = total
