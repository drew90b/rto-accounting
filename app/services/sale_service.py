"""
Sale business logic and balance calculations.

finalize_new_sale() is called immediately after a new sale record is flushed.
It atomically creates the associated transaction and payment records, and
updates the linked unit's status.

Rules enforced here:
  - Golf cart sales are always paid in full at the time of transaction.
    Payment = total_contract_amount (or sale_amount + fees).
    Sale status is forced to 'complete'.
  - Car sales may carry a down payment. If down_payment > 0, a payment record
    is created for that amount. Sale status is whatever the user selected.
  - A sale transaction (transaction_type='sale') is always created.
  - Unit status is updated to 'sold' and linked_customer_id is set.

Caller must db.commit() after this returns.
"""
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.payment import Payment
from app.models.transaction import Transaction
from app.models.unit import Unit


def finalize_new_sale(sale, payment_method: str, db: Session) -> Transaction:
    """
    Create the transaction (and payment if applicable) for a newly saved sale.

    Parameters
    ----------
    sale          : Sale ORM object, already flushed (sale.id and sale.sale_id exist).
    payment_method: Payment method string ('cash', 'check', 'card', etc.)
    db            : Active SQLAlchemy session.

    Returns the created Transaction.
    """
    business_line = sale.business_line.value if hasattr(sale.business_line, "value") else sale.business_line
    sale_amount = Decimal(str(sale.sale_amount)) if sale.sale_amount else Decimal("0")
    fees = Decimal(str(sale.fees)) if sale.fees else Decimal("0")
    down_payment = Decimal(str(sale.down_payment)) if sale.down_payment else Decimal("0")
    total = Decimal(str(sale.total_contract_amount)) if sale.total_contract_amount else (sale_amount + fees)

    today = date_type.today()
    revenue_stream = "golf_cart_sale" if business_line == "golf_cart" else "car_sale"

    # Sale transaction — always created
    t = Transaction(
        transaction_date=sale.sale_date,
        entry_date=today,
        transaction_type="sale",
        business_line=business_line,
        revenue_stream=revenue_stream,
        customer_id=sale.customer_id,
        unit_id=sale.unit_id,
        sale_id=sale.id,
        amount=sale_amount,
        payment_method=payment_method or None,
        description=f"Sale — {sale.sale_id}",
        category="vehicle_sale",
        coding_complete=True,
        review_status="pending",
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"

    if business_line == "golf_cart":
        # Golf cart: always paid in full at counter
        _create_payment(sale, total, payment_method, today, db)
        sale.status = "complete"
    elif down_payment > Decimal("0"):
        # Car sale with a down payment
        _create_payment(sale, down_payment, payment_method, today, db)

    # Update the unit
    unit = db.query(Unit).filter(Unit.id == sale.unit_id).first()
    if unit:
        unit.status = "sold"
        unit.linked_customer_id = sale.customer_id

    # Auto-create invoice
    from app.services.invoice_service import create_invoice_from_sale
    create_invoice_from_sale(sale, db)

    return t


def _create_payment(sale, amount: Decimal, payment_method: str, payment_date, db: Session) -> Payment:
    p = Payment(
        customer_id=sale.customer_id,
        payment_date=payment_date,
        amount=amount,
        payment_method=payment_method,
        sale_id=sale.id,
    )
    db.add(p)
    db.flush()
    p.payment_id = f"P-{p.id:04d}"
    return p


def calculate_sale_balance(sale, db: Session) -> Decimal:
    """
    Return the remaining balance owed on a sale.

    Formula:
        remaining = total_contract_amount - SUM(payments WHERE sale_id = sale.id)

    If total_contract_amount is not set, falls back to sale_amount + fees.
    Mirrors the inline calculation used in the sale edit route.
    """
    total_contract = (
        Decimal(str(sale.total_contract_amount))
        if sale.total_contract_amount
        else (
            (Decimal(str(sale.sale_amount)) if sale.sale_amount else Decimal("0"))
            + (Decimal(str(sale.fees)) if sale.fees else Decimal("0"))
        )
    )
    total_paid: Decimal = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.sale_id == sale.id)
        .scalar()
    ) or Decimal("0.00")

    return total_contract - Decimal(str(total_paid))


def build_sale_balance_map(sales: list, db: Session) -> dict:
    """
    Return a dict of {sale.id: remaining_balance} for a list of sales.
    Convenient for passing to templates. Mirrors build_balance_map in lease_service.
    """
    return {s.id: calculate_sale_balance(s, db) for s in sales}
