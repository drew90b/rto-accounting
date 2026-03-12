"""
Tests for lease_service.calculate_remaining_balance.

Scenarios:
    1. No payments — balance equals financed_balance
    2. Single payment — balance reduced by that amount
    3. Multiple payments — balance reduced by the sum
    4. Partial payments — balance is positive remainder
    5. Adjustment payment (negative amount) — balance increases
    6. Financed balance is None — returns 0.00
    7. build_balance_map returns correct values for multiple leases
"""

from decimal import Decimal
from datetime import date

import pytest

from app.models.customer import Customer
from app.models.unit import Unit
from app.models.lease_account import LeaseAccount
from app.models.payment import Payment
from app.models.enums import UnitType, BusinessLine, UnitStatus, CustomerStatus
from app.services.lease_service import calculate_remaining_balance, build_balance_map


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_customer(db, suffix="A"):
    c = Customer(
        customer_id=f"C-999{suffix}",
        full_name=f"Test Customer {suffix}",
        status=CustomerStatus.active,
    )
    db.add(c)
    db.flush()
    return c


def make_unit(db, suffix="A"):
    u = Unit(
        unit_id=f"U-999{suffix}",
        unit_type=UnitType.car,
        business_line=BusinessLine.car,
        status=UnitStatus.frontline_ready,
    )
    db.add(u)
    db.flush()
    return u


def make_lease(db, customer, unit, financed_balance):
    la = LeaseAccount(
        lease_id=f"L-999{unit.id}",
        customer_id=customer.id,
        unit_id=unit.id,
        deal_date=date(2026, 1, 1),
        financed_balance=financed_balance,
        status="active",
    )
    db.add(la)
    db.flush()
    return la


def make_payment(db, lease, amount):
    p = Payment(
        customer_id=lease.customer_id,
        payment_date=date(2026, 2, 1),
        amount=amount,
        payment_method="cash",
        lease_account_id=lease.id,
    )
    db.add(p)
    db.flush()
    p.payment_id = f"P-{p.id:04d}"
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_payments_returns_financed_balance(db):
    c = make_customer(db, "1")
    u = make_unit(db, "1")
    la = make_lease(db, c, u, Decimal("8000.00"))

    balance = calculate_remaining_balance(la, db)

    assert balance == Decimal("8000.00")


def test_single_payment_reduces_balance(db):
    c = make_customer(db, "2")
    u = make_unit(db, "2")
    la = make_lease(db, c, u, Decimal("8000.00"))
    make_payment(db, la, Decimal("350.00"))

    balance = calculate_remaining_balance(la, db)

    assert balance == Decimal("7650.00")


def test_multiple_payments_sum_correctly(db):
    c = make_customer(db, "3")
    u = make_unit(db, "3")
    la = make_lease(db, c, u, Decimal("8000.00"))
    make_payment(db, la, Decimal("350.00"))
    make_payment(db, la, Decimal("350.00"))
    make_payment(db, la, Decimal("350.00"))

    balance = calculate_remaining_balance(la, db)

    assert balance == Decimal("6950.00")


def test_partial_payments_leave_positive_remainder(db):
    c = make_customer(db, "4")
    u = make_unit(db, "4")
    la = make_lease(db, c, u, Decimal("5000.00"))
    make_payment(db, la, Decimal("1000.00"))
    make_payment(db, la, Decimal("500.00"))

    balance = calculate_remaining_balance(la, db)

    assert balance == Decimal("3500.00")


def test_adjustment_payment_negative_increases_balance(db):
    """A negative payment amount represents a balance correction (credit reversal, etc.)."""
    c = make_customer(db, "5")
    u = make_unit(db, "5")
    la = make_lease(db, c, u, Decimal("8000.00"))
    make_payment(db, la, Decimal("350.00"))
    make_payment(db, la, Decimal("-100.00"))  # adjustment / reversal

    balance = calculate_remaining_balance(la, db)

    assert balance == Decimal("7750.00")


def test_financed_balance_none_returns_zero(db):
    c = make_customer(db, "6")
    u = make_unit(db, "6")
    la = make_lease(db, c, u, None)

    balance = calculate_remaining_balance(la, db)

    assert balance == Decimal("0.00")


def test_build_balance_map_multiple_leases(db):
    c = make_customer(db, "7")
    u1 = make_unit(db, "7")
    u2 = make_unit(db, "8")
    la1 = make_lease(db, c, u1, Decimal("5000.00"))
    la2 = make_lease(db, c, u2, Decimal("3000.00"))
    make_payment(db, la1, Decimal("500.00"))
    make_payment(db, la2, Decimal("200.00"))

    result = build_balance_map([la1, la2], db)

    assert result[la1.id] == Decimal("4500.00")
    assert result[la2.id] == Decimal("2800.00")


def test_payments_on_other_leases_do_not_affect_balance(db):
    """Payments linked to a different lease must not bleed into another lease's balance."""
    c = make_customer(db, "9")
    u1 = make_unit(db, "9")
    u2 = make_unit(db, "X")
    la1 = make_lease(db, c, u1, Decimal("8000.00"))
    la2 = make_lease(db, c, u2, Decimal("8000.00"))
    make_payment(db, la2, Decimal("5000.00"))  # payment on la2 only

    balance = calculate_remaining_balance(la1, db)

    assert balance == Decimal("8000.00")
