"""
Tests for duplicate-sale guard on sale create and edit routes.

Business rule: a unit may not be linked to more than one sale with a
non-terminal status (pending or complete). Only cancelled sales are
terminal, making the unit available again.

Routes under test:
  POST /sales/new
  POST /sales/{id}/edit
"""

from decimal import Decimal


# ---------------------------------------------------------------------------
# Helpers — create prerequisites directly in DB
# ---------------------------------------------------------------------------

def make_customer(db):
    from app.models.customer import Customer
    c = Customer(full_name="Guard Test Customer", status="active")
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


def post_sale(client, customer_id, unit_id, amount="5000"):
    """POST to /sales/new and return the response (no follow)."""
    return client.post(
        "/sales/new",
        data={
            "customer_id": str(customer_id),
            "unit_id": str(unit_id),
            "sale_date": "2026-03-15",
            "business_line": "car",
            "sale_amount": amount,
            "down_payment": "0",
            "fees": "0",
            "total_contract_amount": "",
            "status": "pending",
            "payment_method": "cash",
            "notes": "",
        },
        follow_redirects=False,
    )


# ---------------------------------------------------------------------------
# Create guard
# ---------------------------------------------------------------------------

def test_create_blocked_when_unit_has_pending_sale(client, db):
    """Second sale on same unit with pending status is rejected."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    # First sale — should succeed
    r1 = post_sale(client, c.id, u.id)
    assert r1.status_code == 303
    assert "error" not in r1.headers.get("location", "")

    # Second sale on the same unit — should be blocked
    r2 = post_sale(client, c.id, u.id, amount="9999")
    assert r2.status_code == 303
    assert "error=" in r2.headers["location"]

    # Only one sale record should exist for this unit
    from app.models.sale import Sale
    sales = db.query(Sale).filter(Sale.unit_id == u.id).all()
    assert len(sales) == 1
    assert float(sales[0].sale_amount) == 5000.0


def test_create_allowed_after_sale_is_cancelled(client, db):
    """A unit whose only sale is cancelled is available for a new sale."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    # Create and then cancel the first sale directly in DB
    from app.models.sale import Sale
    from app.models.enums import SaleStatus
    s = Sale(
        customer_id=c.id,
        unit_id=u.id,
        sale_date=__import__("datetime").date(2026, 1, 1),
        business_line="car",
        sale_amount=Decimal("5000"),
        down_payment=Decimal("0"),
        fees=Decimal("0"),
        status=SaleStatus.cancelled,
    )
    db.add(s)
    db.flush()
    s.sale_id = f"S-{s.id:04d}"
    db.commit()

    # New sale on the same unit should be allowed
    r = post_sale(client, c.id, u.id, amount="6000")
    assert r.status_code == 303
    assert "error" not in r.headers.get("location", "")

    sales = db.query(Sale).filter(Sale.unit_id == u.id).all()
    assert len(sales) == 2
    non_cancelled = [x for x in sales if x.status != SaleStatus.cancelled]
    assert len(non_cancelled) == 1
    assert float(non_cancelled[0].sale_amount) == 6000.0


def test_create_allowed_for_clean_unit(client, db):
    """A unit with no prior sales can always receive a new sale."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    r = post_sale(client, c.id, u.id)
    assert r.status_code == 303
    assert "error" not in r.headers.get("location", "")

    from app.models.sale import Sale
    sales = db.query(Sale).filter(Sale.unit_id == u.id).all()
    assert len(sales) == 1


def test_create_blocked_when_unit_has_complete_sale(client, db):
    """A unit with a complete (not cancelled) sale blocks a new sale."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    from app.models.sale import Sale
    from app.models.enums import SaleStatus
    s = Sale(
        customer_id=c.id,
        unit_id=u.id,
        sale_date=__import__("datetime").date(2026, 1, 10),
        business_line="car",
        sale_amount=Decimal("8000"),
        down_payment=Decimal("0"),
        fees=Decimal("0"),
        status=SaleStatus.complete,
    )
    db.add(s)
    db.flush()
    s.sale_id = f"S-{s.id:04d}"
    db.commit()

    r = post_sale(client, c.id, u.id, amount="100")
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
    # Error message should name the conflicting sale
    assert s.sale_id in r.headers["location"]


# ---------------------------------------------------------------------------
# Edit guard
# ---------------------------------------------------------------------------

def test_edit_blocked_when_switching_to_unit_with_active_sale(client, db):
    """Editing a sale to point to a unit that already has an active sale is blocked."""
    c = make_customer(db)
    u1 = make_unit(db)
    u2 = make_unit(db)
    db.commit()

    # u1 already has an active sale
    r1 = post_sale(client, c.id, u1.id, amount="5000")
    assert r1.status_code == 303

    # Create a clean sale on u2
    r2 = post_sale(client, c.id, u2.id, amount="3000")
    assert r2.status_code == 303

    # Look up the sale on u2
    from app.models.sale import Sale
    sale_on_u2 = db.query(Sale).filter(Sale.unit_id == u2.id).first()
    assert sale_on_u2 is not None

    # Try to edit sale_on_u2 to switch its unit to u1 (which has an active sale)
    r3 = client.post(
        f"/sales/{sale_on_u2.id}/edit",
        data={
            "customer_id": str(c.id),
            "unit_id": str(u1.id),   # switching to u1
            "sale_date": "2026-03-15",
            "business_line": "car",
            "sale_amount": "3000",
            "down_payment": "0",
            "fees": "0",
            "total_contract_amount": "",
            "status": "pending",
            "notes": "",
        },
        follow_redirects=False,
    )

    assert r3.status_code == 303
    assert "error=" in r3.headers["location"]

    # sale_on_u2 should still be on u2
    db.expire(sale_on_u2)
    assert sale_on_u2.unit_id == u2.id


def test_edit_allowed_when_keeping_same_unit(client, db):
    """Editing other fields on a sale without changing the unit is never blocked."""
    c = make_customer(db)
    u = make_unit(db)
    db.commit()

    r1 = post_sale(client, c.id, u.id, amount="5000")
    assert r1.status_code == 303

    from app.models.sale import Sale
    sale = db.query(Sale).filter(Sale.unit_id == u.id).first()
    assert sale is not None

    r2 = client.post(
        f"/sales/{sale.id}/edit",
        data={
            "customer_id": str(c.id),
            "unit_id": str(u.id),   # same unit
            "sale_date": "2026-03-15",
            "business_line": "car",
            "sale_amount": "5500",   # just changing amount
            "down_payment": "0",
            "fees": "0",
            "total_contract_amount": "",
            "status": "pending",
            "notes": "",
        },
        follow_redirects=False,
    )

    assert r2.status_code == 303
    assert "error" not in r2.headers.get("location", "")

    db.expire(sale)
    assert float(sale.sale_amount) == 5500.0


def test_edit_allowed_when_switching_to_unit_with_only_cancelled_sale(client, db):
    """Switching a sale to a unit whose only prior sale is cancelled is allowed."""
    c = make_customer(db)
    u1 = make_unit(db)
    u2 = make_unit(db)
    db.commit()

    # u2 has a cancelled sale
    from app.models.sale import Sale
    from app.models.enums import SaleStatus
    cancelled = Sale(
        customer_id=c.id,
        unit_id=u2.id,
        sale_date=__import__("datetime").date(2026, 1, 1),
        business_line="car",
        sale_amount=Decimal("4000"),
        down_payment=Decimal("0"),
        fees=Decimal("0"),
        status=SaleStatus.cancelled,
    )
    db.add(cancelled)
    db.flush()
    cancelled.sale_id = f"S-{cancelled.id:04d}"
    db.commit()

    # Create a sale on u1
    r1 = post_sale(client, c.id, u1.id, amount="5000")
    assert r1.status_code == 303
    sale_on_u1 = db.query(Sale).filter(
        Sale.unit_id == u1.id, Sale.status != SaleStatus.cancelled
    ).first()
    assert sale_on_u1 is not None

    # Edit sale_on_u1 to switch to u2 — should be allowed (u2 only has cancelled)
    r2 = client.post(
        f"/sales/{sale_on_u1.id}/edit",
        data={
            "customer_id": str(c.id),
            "unit_id": str(u2.id),
            "sale_date": "2026-03-15",
            "business_line": "car",
            "sale_amount": "5000",
            "down_payment": "0",
            "fees": "0",
            "total_contract_amount": "",
            "status": "pending",
            "notes": "",
        },
        follow_redirects=False,
    )

    assert r2.status_code == 303
    assert "error" not in r2.headers.get("location", "")
