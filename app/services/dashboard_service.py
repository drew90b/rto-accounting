"""
Dashboard aggregation functions.

All functions take a db session and return a single Decimal value.
Each is intentionally narrow in scope — one metric, one query path.

Notes:
- cash_collected_this_month uses date.today() on the server (UTC on Render).
  For a Florida-based business this is an acceptable approximation.
- inventory_investment excludes units where acquisition_cost IS NULL.
  SQL SUM ignores NULLs, so those units contribute $0 to the total.
- delinquent_balance_total filters to status=active leases only,
  preventing paid_off or cancelled accounts with stale delinquency_status
  from inflating the figure.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session


def cash_collected_this_month(db: Session) -> Decimal:
    """
    Sum of all payment amounts where payment_date falls in the current calendar month.
    Returns Decimal("0.00") when no payments exist for the period.
    """
    from app.models.payment import Payment

    today = date.today()
    month_start = today.replace(day=1)
    if today.month == 12:
        month_end = today.replace(year=today.year + 1, month=1, day=1)
    else:
        month_end = today.replace(month=today.month + 1, day=1)

    total = (
        db.query(func.sum(Payment.amount))
        .filter(Payment.payment_date >= month_start)
        .filter(Payment.payment_date < month_end)
        .scalar()
    )
    return Decimal(str(total)) if total is not None else Decimal("0.00")


def delinquent_balance_total(db: Session) -> Decimal:
    """
    Sum of remaining balances for all active lease accounts with a
    delinquency_status of late, delinquent, or default.

    Reuses lease_service.build_balance_map() so the calculation method
    stays consistent with the rest of the application. Returns the
    greater of the computed total and zero (negative totals indicate
    data entry errors and should not propagate to the dashboard).
    """
    from app.models.lease_account import LeaseAccount
    from app.models.enums import LeaseStatus
    from app.services.lease_service import build_balance_map

    delinquent_leases = (
        db.query(LeaseAccount)
        .filter(LeaseAccount.status == LeaseStatus.active)
        .filter(LeaseAccount.delinquency_status.in_(["late", "delinquent", "default"]))
        .all()
    )

    if not delinquent_leases:
        return Decimal("0.00")

    balance_map = build_balance_map(delinquent_leases, db)
    total = sum(balance_map.values(), Decimal("0.00"))
    return max(total, Decimal("0.00"))


def inventory_investment(db: Session) -> Decimal:
    """
    Sum of acquisition_cost for units that are still in the active portfolio
    (i.e., not sold, closed, or returned for special review).

    leased_rto_active units are included — they remain portfolio assets
    until the account is paid off.

    Units with acquisition_cost = NULL are excluded from the sum (SQL
    SUM ignores NULLs). The returned value may understate true investment
    if units were entered without an acquisition cost.
    """
    from app.models.unit import Unit
    from app.models.enums import UnitStatus

    excluded_statuses = [
        UnitStatus.sold,
        UnitStatus.closed,
        UnitStatus.returned_special_review,
    ]

    total = (
        db.query(func.sum(Unit.acquisition_cost))
        .filter(Unit.status.notin_(excluded_statuses))
        .scalar()
    )
    return Decimal(str(total)) if total is not None else Decimal("0.00")
