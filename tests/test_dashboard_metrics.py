"""
Unit tests for dashboard_service aggregation functions.

Each test calls service functions directly through the `db` fixture.
The transactional rollback architecture in conftest.py ensures no state
leaks between tests.
"""

from datetime import date, timedelta
from decimal import Decimal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_customer(db):
    from app.models.customer import Customer
    c = Customer(full_name="Test Customer", status="active")
    db.add(c)
    db.flush()
    c.customer_id = f"C-{c.id:04d}"
    return c


def make_unit(db, status="frontline_ready", acquisition_cost=None):
    from app.models.unit import Unit
    from app.models.enums import UnitType, BusinessLine
    u = Unit(
        unit_type=UnitType.car,
        business_line=BusinessLine.car,
        status=status,
        acquisition_cost=acquisition_cost,
    )
    db.add(u)
    db.flush()
    u.unit_id = f"U-{u.id:04d}"
    return u


def make_lease(db, customer, unit, financed_balance, delinquency_status="late", status="active"):
    from app.models.lease_account import LeaseAccount
    from app.models.enums import LeaseStatus, DelinquencyStatus
    la = LeaseAccount(
        customer_id=customer.id,
        unit_id=unit.id,
        deal_date=date.today(),
        original_agreed_amount=financed_balance,
        down_payment=Decimal("0.00"),
        financed_balance=financed_balance,
        status=status,
        delinquency_status=delinquency_status,
    )
    db.add(la)
    db.flush()
    la.lease_id = f"L-{la.id:04d}"
    return la


def make_payment(db, customer, amount, payment_date, lease=None):
    from app.models.payment import Payment
    from app.models.enums import PaymentMethod
    p = Payment(
        customer_id=customer.id,
        payment_date=payment_date,
        amount=amount,
        payment_method=PaymentMethod.cash,
        lease_account_id=lease.id if lease else None,
    )
    db.add(p)
    db.flush()
    p.payment_id = f"P-{p.id:04d}"
    return p


# ---------------------------------------------------------------------------
# cash_collected_this_month
# ---------------------------------------------------------------------------

def test_cash_this_month_no_payments(db):
    from app.services.dashboard_service import cash_collected_this_month
    result = cash_collected_this_month(db)
    assert result == Decimal("0.00")


def test_cash_this_month_counts_payments_in_current_month(db):
    from app.services.dashboard_service import cash_collected_this_month
    customer = make_customer(db)
    today = date.today()
    make_payment(db, customer, Decimal("200.00"), today)
    make_payment(db, customer, Decimal("350.00"), today)
    db.flush()
    result = cash_collected_this_month(db)
    assert result == Decimal("550.00")


def test_cash_this_month_excludes_prior_month(db):
    from app.services.dashboard_service import cash_collected_this_month
    customer = make_customer(db)
    today = date.today()
    # First day of this month minus 1 = last day of last month
    last_month = today.replace(day=1) - timedelta(days=1)
    make_payment(db, customer, Decimal("500.00"), last_month)
    make_payment(db, customer, Decimal("150.00"), today)
    db.flush()
    result = cash_collected_this_month(db)
    assert result == Decimal("150.00")


def test_cash_this_month_excludes_next_month(db):
    from app.services.dashboard_service import cash_collected_this_month
    customer = make_customer(db)
    today = date.today()
    # First day of next month
    if today.month == 12:
        next_month = today.replace(year=today.year + 1, month=1, day=1)
    else:
        next_month = today.replace(month=today.month + 1, day=1)
    make_payment(db, customer, Decimal("999.00"), next_month)
    make_payment(db, customer, Decimal("100.00"), today)
    db.flush()
    result = cash_collected_this_month(db)
    assert result == Decimal("100.00")


# ---------------------------------------------------------------------------
# inventory_investment
# ---------------------------------------------------------------------------

def test_inventory_investment_no_units(db):
    from app.services.dashboard_service import inventory_investment
    result = inventory_investment(db)
    assert result == Decimal("0.00")


def test_inventory_investment_sums_active_units(db):
    from app.services.dashboard_service import inventory_investment
    make_unit(db, status="frontline_ready", acquisition_cost=Decimal("5000.00"))
    make_unit(db, status="in_repair", acquisition_cost=Decimal("3200.00"))
    make_unit(db, status="leased_rto_active", acquisition_cost=Decimal("7800.00"))
    db.flush()
    result = inventory_investment(db)
    assert result == Decimal("16000.00")


def test_inventory_investment_excludes_sold_units(db):
    from app.services.dashboard_service import inventory_investment
    make_unit(db, status="frontline_ready", acquisition_cost=Decimal("4000.00"))
    make_unit(db, status="sold", acquisition_cost=Decimal("9000.00"))
    make_unit(db, status="closed", acquisition_cost=Decimal("8000.00"))
    make_unit(db, status="returned_special_review", acquisition_cost=Decimal("6000.00"))
    db.flush()
    result = inventory_investment(db)
    assert result == Decimal("4000.00")


def test_inventory_investment_ignores_null_acquisition_cost(db):
    from app.services.dashboard_service import inventory_investment
    make_unit(db, status="frontline_ready", acquisition_cost=Decimal("5000.00"))
    make_unit(db, status="in_repair", acquisition_cost=None)  # no cost recorded
    db.flush()
    result = inventory_investment(db)
    # NULL unit contributes $0; only the $5000 unit is summed
    assert result == Decimal("5000.00")


# ---------------------------------------------------------------------------
# delinquent_balance_total
# ---------------------------------------------------------------------------

def test_delinquent_balance_total_no_leases(db):
    from app.services.dashboard_service import delinquent_balance_total
    result = delinquent_balance_total(db)
    assert result == Decimal("0.00")


def test_delinquent_balance_total_sums_remaining_balances(db):
    from app.services.dashboard_service import delinquent_balance_total
    customer = make_customer(db)
    unit1 = make_unit(db, status="leased_rto_active", acquisition_cost=Decimal("5000.00"))
    unit2 = make_unit(db, status="leased_rto_active", acquisition_cost=Decimal("6000.00"))
    lease1 = make_lease(db, customer, unit1, Decimal("8000.00"), delinquency_status="late")
    lease2 = make_lease(db, customer, unit2, Decimal("5000.00"), delinquency_status="delinquent")
    # $200 paid on lease1 → remaining $7800
    make_payment(db, customer, Decimal("200.00"), date.today(), lease=lease1)
    db.flush()
    result = delinquent_balance_total(db)
    assert result == Decimal("12800.00")  # 7800 + 5000


def test_delinquent_balance_total_excludes_current_leases(db):
    from app.services.dashboard_service import delinquent_balance_total
    customer = make_customer(db)
    unit = make_unit(db, status="leased_rto_active", acquisition_cost=Decimal("5000.00"))
    # current delinquency status — should NOT be counted
    make_lease(db, customer, unit, Decimal("10000.00"), delinquency_status="current")
    db.flush()
    result = delinquent_balance_total(db)
    assert result == Decimal("0.00")


def test_delinquent_balance_total_excludes_inactive_leases(db):
    from app.services.dashboard_service import delinquent_balance_total
    customer = make_customer(db)
    unit = make_unit(db, status="closed", acquisition_cost=Decimal("5000.00"))
    # paid_off lease with stale delinquency_status — should NOT be counted
    make_lease(db, customer, unit, Decimal("10000.00"), delinquency_status="late", status="paid_off")
    db.flush()
    result = delinquent_balance_total(db)
    assert result == Decimal("0.00")


# ---------------------------------------------------------------------------
# Dashboard route smoke test
# ---------------------------------------------------------------------------

def test_dashboard_returns_200(client):
    """GET / should render without error after metrics changes."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Cash Collected" in response.content
    assert b"Delinquent Balance" in response.content
    assert b"Inventory Investment" in response.content
    assert b"Active Exceptions" in response.content
