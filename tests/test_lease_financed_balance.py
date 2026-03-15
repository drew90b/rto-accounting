"""
Tests for auto-calculated financed_balance on lease create.

Route: POST /lease-accounts/new
- financed_balance is derived server-side as original_agreed_amount - down_payment
- Any financed_balance submitted in the form body is ignored
- Validation: original_agreed_amount must be > 0
- Validation: down_payment must not exceed original_agreed_amount
"""

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


def make_unit(db):
    from app.models.unit import Unit
    from app.models.enums import UnitType, BusinessLine
    u = Unit(unit_type=UnitType.car, business_line=BusinessLine.car, status="frontline_ready")
    db.add(u)
    db.flush()
    u.unit_id = f"U-{u.id:04d}"
    return u


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_financed_balance_derived_from_agreed_minus_down(client, db):
    """financed_balance is stored as agreed_amount - down_payment."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    resp = client.post("/lease-accounts/new", data={
        "customer_id": str(c.id),
        "unit_id": str(u.id),
        "deal_date": "2026-03-14",
        "original_agreed_amount": "5000.00",
        "down_payment": "500.00",
        "scheduled_payment_amount": "250.00",
        "payment_frequency": "monthly",
        "status": "active",
        "delinquency_status": "current",
        "notes": "",
    }, follow_redirects=False)

    assert resp.status_code == 303

    from app.models.lease_account import LeaseAccount
    la = db.query(LeaseAccount).filter(LeaseAccount.customer_id == c.id).first()
    assert la is not None
    assert la.financed_balance == Decimal("4500.00")


def test_submitted_financed_balance_ignored(client, db):
    """Any financed_balance value submitted in the form body is ignored."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    # Submit a financed_balance that does NOT equal agreed - down
    resp = client.post("/lease-accounts/new", data={
        "customer_id": str(c.id),
        "unit_id": str(u.id),
        "deal_date": "2026-03-14",
        "original_agreed_amount": "5000.00",
        "down_payment": "500.00",
        "financed_balance": "99999.00",   # should be completely ignored
        "scheduled_payment_amount": "250.00",
        "payment_frequency": "monthly",
        "status": "active",
        "delinquency_status": "current",
        "notes": "",
    }, follow_redirects=False)

    assert resp.status_code == 303

    from app.models.lease_account import LeaseAccount
    la = db.query(LeaseAccount).filter(LeaseAccount.customer_id == c.id).first()
    assert la is not None
    assert la.financed_balance == Decimal("4500.00")


def test_zero_down_payment_financed_equals_agreed(client, db):
    """With no down payment, financed_balance equals original_agreed_amount."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    resp = client.post("/lease-accounts/new", data={
        "customer_id": str(c.id),
        "unit_id": str(u.id),
        "deal_date": "2026-03-14",
        "original_agreed_amount": "8000.00",
        "down_payment": "0",
        "scheduled_payment_amount": "400.00",
        "payment_frequency": "monthly",
        "status": "active",
        "delinquency_status": "current",
        "notes": "",
    }, follow_redirects=False)

    assert resp.status_code == 303

    from app.models.lease_account import LeaseAccount
    la = db.query(LeaseAccount).filter(LeaseAccount.customer_id == c.id).first()
    assert la is not None
    assert la.financed_balance == Decimal("8000.00")


def test_down_payment_exceeds_agreed_amount_returns_error(client, db):
    """down_payment > original_agreed_amount → 303 redirect with ?error= param."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    resp = client.post("/lease-accounts/new", data={
        "customer_id": str(c.id),
        "unit_id": str(u.id),
        "deal_date": "2026-03-14",
        "original_agreed_amount": "3000.00",
        "down_payment": "4000.00",
        "scheduled_payment_amount": "200.00",
        "payment_frequency": "monthly",
        "status": "active",
        "delinquency_status": "current",
        "notes": "",
    }, follow_redirects=False)

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    from app.models.lease_account import LeaseAccount
    la = db.query(LeaseAccount).filter(LeaseAccount.customer_id == c.id).first()
    assert la is None


def test_missing_original_agreed_amount_returns_error(client, db):
    """Missing / zero original_agreed_amount → 303 redirect with ?error= param."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    resp = client.post("/lease-accounts/new", data={
        "customer_id": str(c.id),
        "unit_id": str(u.id),
        "deal_date": "2026-03-14",
        "original_agreed_amount": "",
        "down_payment": "0",
        "scheduled_payment_amount": "200.00",
        "payment_frequency": "monthly",
        "status": "active",
        "delinquency_status": "current",
        "notes": "",
    }, follow_redirects=False)

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    from app.models.lease_account import LeaseAccount
    la = db.query(LeaseAccount).filter(LeaseAccount.customer_id == c.id).first()
    assert la is None


def test_zero_original_agreed_amount_returns_error(client, db):
    """Zero original_agreed_amount → 303 redirect with ?error= param."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    resp = client.post("/lease-accounts/new", data={
        "customer_id": str(c.id),
        "unit_id": str(u.id),
        "deal_date": "2026-03-14",
        "original_agreed_amount": "0",
        "down_payment": "0",
        "scheduled_payment_amount": "200.00",
        "payment_frequency": "monthly",
        "status": "active",
        "delinquency_status": "current",
        "notes": "",
    }, follow_redirects=False)

    assert resp.status_code == 303
    assert "error=" in resp.headers["location"]

    from app.models.lease_account import LeaseAccount
    la = db.query(LeaseAccount).filter(LeaseAccount.customer_id == c.id).first()
    assert la is None
