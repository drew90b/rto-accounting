"""
Lease account business logic.

outstanding_balance is NOT stored as mutable state.
Remaining balance is always calculated at runtime from deal terms and payment history.
"""
import math
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.lease_account import LeaseAccount
from app.models.payment import Payment
from app.models.enums import TransactionType, BusinessLine, RevenueStream


def calculate_remaining_balance(lease: LeaseAccount, db: Session) -> Decimal:
    """
    Return the remaining balance owed on a lease account.

    Formula:
        remaining = financed_balance - sum(payments.amount WHERE lease_account_id = lease.id)

    Source of truth:
        - lease_accounts.financed_balance  (immutable deal term)
        - payments table                   (recorded collection events)

    Notes:
        - Adjustments are not yet a first-class concept in the payment model.
          If a balance correction is needed, it should be entered as a Payment
          with a negative amount and a note explaining the adjustment.
        - Do not read or write outstanding_balance_deprecated on the model.
    """
    if lease.financed_balance is None:
        return Decimal("0.00")

    total_paid: Decimal = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.lease_account_id == lease.id)
        .scalar()
    ) or Decimal("0.00")

    return Decimal(str(lease.financed_balance)) - Decimal(str(total_paid))


def build_balance_map(leases: list[LeaseAccount], db: Session) -> dict[int, Decimal]:
    """
    Return a dict of {lease.id: remaining_balance} for a list of lease accounts.
    Convenient for passing to templates.
    """
    return {la.id: calculate_remaining_balance(la, db) for la in leases}


def calculate_remaining_months(
    lease: LeaseAccount, remaining_balance: Decimal
) -> Optional[int]:
    """
    Estimate the number of months of scheduled payments remaining on a lease.

    Approach (option 3 — closest truthful derivation):
        payments_remaining = ceil(remaining_balance / scheduled_payment_amount)
        months = payments_remaining adjusted for payment_frequency

    Frequency conversions:
        monthly    → 1 payment  = 1 month
        bi_weekly  → 2 payments ≈ 1 month  (ceil(n / 2))
        weekly     → ~4.3 payments per month  (ceil(n * 7 / 30))

    This is an approximation. It assumes on-schedule payments and does not
    account for irregular payment history or missed periods. It answers:
    "at the current scheduled pace, how many months are left?"

    Returns:
        0    if remaining_balance <= 0  (fully paid or overpaid)
        None if calculation cannot be made (missing or zero scheduled_payment_amount)
        int  estimated months remaining (>= 1)
    """
    if remaining_balance <= Decimal("0"):
        return 0

    if lease.scheduled_payment_amount is None:
        return None
    payment_amount = Decimal(str(lease.scheduled_payment_amount))
    if payment_amount <= Decimal("0"):
        return None

    payments_remaining = math.ceil(float(remaining_balance) / float(payment_amount))

    freq = lease.payment_frequency.value if lease.payment_frequency else "monthly"
    if freq == "bi_weekly":
        return max(1, math.ceil(payments_remaining / 2))
    elif freq == "weekly":
        return max(1, math.ceil(payments_remaining * 7 / 30))
    else:
        # monthly (and any unknown frequency — treat as monthly)
        return payments_remaining


def build_months_map(
    leases: list[LeaseAccount], balance_map: dict[int, Decimal]
) -> dict[int, Optional[int]]:
    """
    Return {lease.id: remaining_months_or_None} for a list of lease accounts.
    Reuses a pre-computed balance_map to avoid redundant DB queries.
    Pass the output of build_balance_map() as balance_map.
    """
    return {
        la.id: calculate_remaining_months(la, balance_map[la.id])
        for la in leases
    }


def record_rto_payment(
    lease: LeaseAccount,
    payment_date: date,
    amount: Decimal,
    payment_method: str,
    notes: str,
    entered_by: str,
    db: Session,
):
    """
    Atomically create a Payment and matching Transaction for an RTO collection.

    Creates:
        - payments row  (lease_account_id set)
        - transactions row  (type=collection, business_line=car, revenue_stream=car_rto_lease)

    Both records are flushed so IDs are assigned before the caller commits.
    Returns (payment, transaction).
    """
    from app.models.transaction import Transaction

    p = Payment(
        customer_id=lease.customer_id,
        payment_date=payment_date,
        amount=amount,
        payment_method=payment_method,
        lease_account_id=lease.id,
        entered_by=entered_by or None,
        notes=notes or None,
    )
    db.add(p)
    db.flush()
    p.payment_id = f"P-{p.id:04d}"

    customer_name = lease.customer.full_name if lease.customer else ""
    t = Transaction(
        transaction_date=payment_date,
        entry_date=date.today(),
        transaction_type=TransactionType.collection,
        business_line=BusinessLine.car,
        revenue_stream=RevenueStream.car_rto_lease,
        customer_id=lease.customer_id,
        lease_account_id=lease.id,
        unit_id=lease.unit_id,
        amount=amount,
        payment_method=payment_method,
        description=f"RTO payment — {customer_name} ({lease.lease_id})",
        category="rto_collection",
        coding_complete=True,
        review_status="pending",
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"

    # Auto-create RTO payment receipt
    from app.services.invoice_service import create_invoice_from_rto_payment
    create_invoice_from_rto_payment(lease, p, db)

    return p, t
