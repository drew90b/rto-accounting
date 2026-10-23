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
                       Transaction after an edit, so corrections in the
                       Purchases ledger are reflected everywhere else. A
                       Unit's acquisition_cost is corrected only by the
                       purchase that created the unit (created_unit); any
                       other linked purchase leaves it alone.

Rules enforced:
  - A Vendor is always linked. If no Vendor with a matching name (case
    insensitive) exists, one is created — the employee never has to leave
    the purchase form to add a vendor.
  - business_line is derived from the expense category (never asked of the
    user) — RTO/Flip car categories map to 'car', golf cart categories map
    to 'golf_cart'.
  - General-expense categories (general_expense_*) record operating spending.
    They never create or link a Unit. Their business_line is car, golf_cart,
    shared (spans both lines), or unallocated ("Not sure" — held for review
    until someone decides).
  - payment_source (which card, account, cash, or person paid) is stored on
    the Transaction. The Purchases form requires it; the service does not, so
    the historical importer can leave it NULL rather than guess.
  - Vehicle purchases (auction_car_rto, auction_car_flip, auction_golf_cart_flip)
    always end up linked to a Unit: an existing one (matched by stock number
    or VIN) or a newly created one. No Unit is ever fabricated for any other
    category — those still only link on a best-effort stock-number match.
  - Acquisition cost = purchase amount + shipping_cost + other_fees. It is
    computed once when the Unit is created (and re-synced on edit), never a
    separately-entered total.
  - transaction_type is always 'purchase'. The Transaction is the accounting
    ledger entry; Purchase is the durable record of what was submitted.

Attribution and review:
  - entered_by_user_id / reviewed_by_user_id come from the signed-in user, never
    from a form field, and are separate from purchaser (who made the purchase).
  - Only a pending purchase can be edited. Marking it reviewed locks it for
    everyone; an admin must reopen it (with a reason) before it can change, so
    "reviewed" always describes the figures that were actually reviewed.
  - Every review and reopen is written to purchase_events.

Caller must db.commit() after each function returns.
"""
from datetime import date as date_type, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.purchase import Purchase
from app.models.transaction import Transaction
from app.models.vendor import Vendor
from app.models.unit import Unit
from app.models.enums import PurchaseCategory, PurchaseEventAction, ReviewStatus, UnitType
from app.models.purchase_event import PurchaseEvent
from app.models.user import User

_GOLF_CART_CATEGORIES = {
    PurchaseCategory.parts_flip_golf_cart_build,
    PurchaseCategory.parts_golf_cart_repair,
    PurchaseCategory.auction_golf_cart_flip,
    PurchaseCategory.general_expense_golf_cart,
}

PAYMENT_SOURCE_UNKNOWN = "Unknown - needs review"

_VEHICLE_CATEGORIES = {
    PurchaseCategory.auction_car_rto,
    PurchaseCategory.auction_car_flip,
    PurchaseCategory.auction_golf_cart_flip,
}


def business_line_for_category(category: PurchaseCategory) -> str:
    if category == PurchaseCategory.general_expense_shared:
        return "shared"
    if category == PurchaseCategory.general_expense_unsure:
        return "unallocated"
    return "golf_cart" if category in _GOLF_CART_CATEGORIES else "car"


def is_vehicle_purchase(category: PurchaseCategory) -> bool:
    return category in _VEHICLE_CATEGORIES


def is_payment_source_unknown(payment_source: Optional[str]) -> bool:
    return bool(payment_source) and payment_source.strip().lower().startswith("unknown")


def payment_source_suggestions(db: Session) -> list:
    """Payment sources already used, for the form's suggestion list. 'Unknown'
    is always offered so an employee who doesn't know can still submit."""
    used = [
        row[0] for row in
        db.query(Transaction.payment_source)
        .filter(Transaction.payment_source.isnot(None))
        .distinct()
        .order_by(Transaction.payment_source)
        .all()
    ]
    return [PAYMENT_SOURCE_UNKNOWN] + [s for s in used if s != PAYMENT_SOURCE_UNKNOWN]


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
    payment_source: str = "",
    entered_by_user_id: Optional[int] = None,
    db: Session,
) -> Purchase:
    vendor = find_or_create_vendor(vendor_name, db)
    business_line = business_line_for_category(category)

    created_unit = False
    if is_vehicle_purchase(category):
        unit = resolve_vehicle_unit(stock_number, vin, db)
        acquisition_cost = amount + shipping_cost + other_fees
        unit_is_new = unit is None
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
        created_unit = unit_is_new
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
        entered_by_user_id=entered_by_user_id,
        created_unit=created_unit,
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
        payment_source=payment_source.strip() or None,
        entered_by=entered_by or None,
        coding_complete=True,
        review_status="pending",
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"

    purchase.transaction_id = t.id
    return purchase


def purchase_total(purchase: Purchase) -> Decimal:
    return purchase.amount + (purchase.shipping_cost or Decimal("0")) + (purchase.other_fees or Decimal("0"))


def unit_cost_disagrees(purchase: Purchase) -> bool:
    """A vehicle purchase linked to a unit it did not create, whose total differs from the
    unit's acquisition cost. Nothing is synced in that case; someone has to decide."""
    return bool(
        is_vehicle_purchase(purchase.category) and purchase.unit and not purchase.created_unit
        and purchase.unit.acquisition_cost != purchase_total(purchase)
    )


def sync_purchase_transaction(purchase: Purchase, db: Session) -> None:
    """Push a purchase's current fields onto its linked Transaction after an edit."""
    total = purchase_total(purchase)

    t = purchase.transaction
    if t:
        t.transaction_date = purchase.purchase_date
        t.business_line = business_line_for_category(purchase.category)
        t.vendor_id = purchase.vendor_id
        t.unit_id = purchase.unit_id
        t.amount = total
        t.description = purchase.description or purchase.category.value
        t.category = purchase.category.name

    # A correction reaches the unit's acquisition cost only if this purchase created
    # the unit. A purchase that merely matched an existing unit (or whose origin is
    # unknown) must never overwrite a cost someone else established; the review
    # queue flags the mismatch instead.
    if purchase.created_unit and purchase.unit:
        purchase.unit.acquisition_cost = total


# ---------------------------------------------------------------------------
# Editing rules
# ---------------------------------------------------------------------------

def is_editable(purchase: Purchase) -> bool:
    """Only a pending purchase can change; reviewed and approved ones are locked."""
    return purchase.review_status == ReviewStatus.pending


def check_edit_allowed(purchase: Purchase, new_category: PurchaseCategory, matched_unit: Optional[Unit]) -> None:
    """
    Raise ValueError if an edit would damage a linked Unit.

    Saving an edit pushes a vehicle purchase's total onto its Unit's
    acquisition_cost, so a stock-number typo that matches a different unit, or
    a category change that turns a parts purchase into a vehicle purchase (or
    the reverse), would overwrite the wrong unit's cost.
    """
    was_vehicle = is_vehicle_purchase(purchase.category)
    will_be_vehicle = is_vehicle_purchase(new_category)
    if was_vehicle != will_be_vehicle:
        raise ValueError(
            "The category can't change between a vehicle purchase and a parts/expense "
            "purchase here, because that would change a vehicle's cost. Ask an administrator."
        )
    if was_vehicle and business_line_for_category(new_category) != business_line_for_category(purchase.category):
        raise ValueError("A vehicle purchase can't move between car and golf cart. Ask an administrator.")
    if was_vehicle and matched_unit and matched_unit.id != purchase.unit_id:
        raise ValueError(
            f"That stock number belongs to a different unit ({matched_unit.unit_id}). "
            "A vehicle purchase can't be re-linked here. Ask an administrator."
        )


# ---------------------------------------------------------------------------
# Review and history
# ---------------------------------------------------------------------------

def add_event(
    db: Session, purchase: Purchase, user: User, action: PurchaseEventAction,
    changes: Optional[dict] = None, note: Optional[str] = None,
) -> PurchaseEvent:
    event = PurchaseEvent(
        purchase_id=purchase.id, user_id=user.id, action=action,
        changes=changes or None, note=note or None,
    )
    db.add(event)
    return event


_MONEY_FIELDS = ("amount", "shipping_cost", "other_fees")


def purchase_snapshot(purchase: Purchase) -> dict:
    """The fields a reviewer cares about, as plain readable values, for the history log."""
    def money(value):
        return None if value is None else f"{value:.2f}"

    return {
        "purchaser": purchase.purchaser,
        "purchase_date": purchase.purchase_date.isoformat() if purchase.purchase_date else None,
        "vendor": purchase.vendor.name if purchase.vendor else None,
        "amount": money(purchase.amount),
        "shipping_cost": money(purchase.shipping_cost),
        "other_fees": money(purchase.other_fees),
        "category": purchase.category.value if purchase.category else None,
        "site_location": purchase.site_location or None,
        "stock_number": purchase.stock_number or None,
        "unit": purchase.unit.unit_id if purchase.unit else None,
        "mileage": purchase.mileage,
        "description": purchase.description or None,
        "payment_source": purchase.transaction.payment_source if purchase.transaction else None,
    }


def changes_between(before: dict, after: dict) -> dict:
    """{field: [old, new]} for the fields that differ. Empty when nothing changed."""
    return {k: [before[k], after[k]] for k in after if before.get(k) != after[k]}


def mark_reviewed(purchase: Purchase, user: User, db: Session) -> bool:
    """Mark a pending purchase reviewed by `user`. Returns False if it was not pending."""
    if purchase.review_status != ReviewStatus.pending:
        return False
    purchase.review_status = ReviewStatus.reviewed
    purchase.reviewed_by_user_id = user.id
    purchase.reviewed_at = datetime.utcnow()
    add_event(db, purchase, user, PurchaseEventAction.reviewed)
    return True


def reopen(purchase: Purchase, user: User, reason: str, db: Session) -> None:
    """
    Return a reviewed purchase to pending so it can be corrected. The reason is
    required. The previous reviewer and time are kept in the event, since the
    purchase itself no longer says who reviewed it.
    """
    reason = reason.strip()
    if not reason:
        raise ValueError("A reason is required to reopen a purchase.")
    if purchase.review_status == ReviewStatus.pending:
        raise ValueError("That purchase is already open for editing.")
    previous = purchase.reviewed_by_user.display_name if purchase.reviewed_by_user else None
    previous_at = purchase.reviewed_at.isoformat(timespec="minutes") if purchase.reviewed_at else None
    purchase.review_status = ReviewStatus.pending
    purchase.reviewed_by_user_id = None
    purchase.reviewed_at = None
    add_event(
        db, purchase, user, PurchaseEventAction.reopened,
        changes={"reviewed_by": [previous, None], "reviewed_at": [previous_at, None]},
        note=reason,
    )
