"""
Tests for the record_rto_payment service.

Verifies that recording a payment:
    - creates both a Payment and a Transaction record
    - sets all required Transaction fields correctly (type, business_line, revenue_stream)
    - links both records back to the lease account
    - updates the computed remaining balance
"""

from decimal import Decimal
from datetime import date

import pytest

from app.models.customer import Customer
from app.models.unit import Unit
from app.models.lease_account import LeaseAccount
from app.models.payment import Payment
from app.models.transaction import Transaction
from app.models.enums import (
    UnitType, BusinessLine, UnitStatus, CustomerStatus,
    TransactionType, RevenueStream,
)
from app.services.lease_service import (
    calculate_remaining_balance,
    record_rto_payment,
)


# ---------------------------------------------------------------------------
# Helpers (reused from test_lease_balance where possible)
# ---------------------------------------------------------------------------

def make_customer(db, suffix="A"):
    c = Customer(
        customer_id=f"C-88{suffix}",
        full_name=f"RTO Test Customer {suffix}",
        status=CustomerStatus.active,
    )
    db.add(c)
    db.flush()
    return c


def make_unit(db, suffix="A"):
    u = Unit(
        unit_id=f"U-88{suffix}",
        unit_type=UnitType.car,
        business_line=BusinessLine.car,
        status=UnitStatus.leased_rto_active,
    )
    db.add(u)
    db.flush()
    return u


def make_lease(db, customer, unit, financed_balance=Decimal("8000.00")):
    la = LeaseAccount(
        lease_id=f"L-88{unit.id}",
        customer_id=customer.id,
        unit_id=unit.id,
        deal_date=date(2026, 1, 1),
        original_agreed_amount=financed_balance + Decimal("1000.00"),
        down_payment=Decimal("1000.00"),
        financed_balance=financed_balance,
        scheduled_payment_amount=Decimal("350.00"),
        status="active",
        delinquency_status="current",
    )
    db.add(la)
    db.flush()
    return la


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_record_rto_payment_creates_payment_record(db):
    c = make_customer(db, "1")
    u = make_unit(db, "1")
    la = make_lease(db, c, u)

    p, t = record_rto_payment(
        lease=la,
        payment_date=date(2026, 2, 1),
        amount=Decimal("350.00"),
        payment_method="cash",
        notes="First payment",
        entered_by="Admin",
        db=db,
    )

    assert p.id is not None
    assert p.payment_id.startswith("P-")
    assert p.customer_id == c.id
    assert p.lease_account_id == la.id
    assert p.amount == Decimal("350.00")
    assert p.payment_method == "cash"
    assert p.notes == "First payment"
    assert p.entered_by == "Admin"


def test_record_rto_payment_creates_transaction_record(db):
    c = make_customer(db, "2")
    u = make_unit(db, "2")
    la = make_lease(db, c, u)

    p, t = record_rto_payment(
        lease=la,
        payment_date=date(2026, 2, 1),
        amount=Decimal("350.00"),
        payment_method="check",
        notes="",
        entered_by="",
        db=db,
    )

    assert t.id is not None
    assert t.transaction_id.startswith("T-")
    assert t.transaction_type.value == "collection"
    assert t.business_line.value == "car"
    assert t.revenue_stream.value == "car_rto_lease"
    assert t.customer_id == c.id
    assert t.lease_account_id == la.id
    assert t.unit_id == u.id
    assert t.amount == Decimal("350.00")
    assert t.payment_method == "check"
    assert t.coding_complete is True
    assert "L-88" in t.description


def test_record_rto_payment_reduces_remaining_balance(db):
    c = make_customer(db, "3")
    u = make_unit(db, "3")
    la = make_lease(db, c, u, financed_balance=Decimal("8000.00"))

    assert calculate_remaining_balance(la, db) == Decimal("8000.00")

    record_rto_payment(
        lease=la,
        payment_date=date(2026, 2, 1),
        amount=Decimal("350.00"),
        payment_method="cash",
        notes="",
        entered_by="",
        db=db,
    )

    assert calculate_remaining_balance(la, db) == Decimal("7650.00")


def test_multiple_payments_accumulate_correctly(db):
    c = make_customer(db, "4")
    u = make_unit(db, "4")
    la = make_lease(db, c, u, financed_balance=Decimal("8000.00"))

    for i in range(3):
        record_rto_payment(
            lease=la,
            payment_date=date(2026, 2, i + 1),
            amount=Decimal("350.00"),
            payment_method="cash",
            notes="",
            entered_by="",
            db=db,
        )

    assert calculate_remaining_balance(la, db) == Decimal("6950.00")


def test_payment_linked_to_correct_customer(db):
    """Payment customer_id should come from the lease, not be independently specified."""
    c = make_customer(db, "5")
    u = make_unit(db, "5")
    la = make_lease(db, c, u)

    p, t = record_rto_payment(
        lease=la,
        payment_date=date(2026, 2, 1),
        amount=Decimal("200.00"),
        payment_method="card",
        notes="",
        entered_by="",
        db=db,
    )

    assert p.customer_id == la.customer_id
    assert t.customer_id == la.customer_id


def test_transaction_entry_date_is_today(db):
    from datetime import date as date_type
    c = make_customer(db, "6")
    u = make_unit(db, "6")
    la = make_lease(db, c, u)

    _, t = record_rto_payment(
        lease=la,
        payment_date=date(2026, 1, 15),
        amount=Decimal("350.00"),
        payment_method="cash",
        notes="",
        entered_by="",
        db=db,
    )

    assert t.entry_date == date_type.today()
    assert t.transaction_date == date(2026, 1, 15)


def test_both_records_share_same_amount(db):
    c = make_customer(db, "7")
    u = make_unit(db, "7")
    la = make_lease(db, c, u)

    p, t = record_rto_payment(
        lease=la,
        payment_date=date(2026, 2, 1),
        amount=Decimal("412.50"),
        payment_method="cash",
        notes="",
        entered_by="",
        db=db,
    )

    assert p.amount == Decimal("412.50")
    assert t.amount == Decimal("412.50")
