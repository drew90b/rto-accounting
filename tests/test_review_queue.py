"""
Tests for the Review Queue — the owner's mobile inbox for verifying newly
entered data. Reuses Purchase.review_status and Transaction.review_status;
no new schema.

Covers:
  GET  /review/                        — queue list, filters, flags
  POST /review/purchases/{id}/reviewed — mark a purchase reviewed
  POST /review/transactions/{id}/reviewed — mark a transaction reviewed

Note: tests/conftest.py's per-test rollback isolation does not fully hold
(see memory/test_isolation_bug.md) — data committed via the client in one
test can still be visible in the next. Every test here therefore uses its
own unique VIN/vendor/stock-number rather than shared defaults, and
assertions check for that unique data rather than generic strings that a
leaked row from another test could also produce.
"""
from decimal import Decimal
from datetime import date


def make_vendor(db, name):
    from app.models.vendor import Vendor
    v = Vendor(name=name)
    db.add(v)
    db.flush()
    v.vendor_id = f"V-{v.id:04d}"
    return v


def post_vehicle_purchase(client, vin, stock_number, **overrides):
    data = {
        "purchaser": "Jane Doe",
        "purchase_date": "2026-09-13",
        "vendor": "Lakeland Auto Auction",
        "amount": "6500.00",
        "category": "AUCTION - Purchased Cars (RTO)",
        "site_location": "Eunice",
        "stock_number": stock_number,
        "mileage": "82000",
        "description": "",
        "entered_by": "",
        "shipping_cost": "200.00",
        "other_fees": "75.00",
        "vin": vin,
        "year": "2019",
        "make": "Ford",
        "model": "Fusion",
    }
    data.update(overrides)
    return client.post("/purchases/new", data=data, follow_redirects=False)


def post_parts_purchase(client, vendor, **overrides):
    data = {
        "purchaser": "Jane Doe",
        "purchase_date": "2026-09-13",
        "vendor": vendor,
        "amount": "125.50",
        "category": 'PARTS - "RTO" car parts (Repair)',
        "site_location": "Eunice",
        "stock_number": "",
        "mileage": "",
        "description": "Brake pads",
        "entered_by": "",
    }
    data.update(overrides)
    return client.post("/purchases/new", data=data, follow_redirects=False)


def card_containing(html, marker):
    """Return just the <div class="review-card ...">...</div> chunk that contains marker."""
    idx = html.find(marker)
    assert idx != -1, f"{marker!r} not found in response"
    # Trailing space disambiguates the outer card div from review-card-sub/-title,
    # which also start with "review-card" but appear closer to the marker.
    start = html.rfind('<div class="review-card ', 0, idx)
    end = html.find('\n  </div>\n', idx)
    return html[start:end]


def make_unit_linked_transaction(db, unit, amount=Decimal("450.00"), vendor=None):
    from app.models.transaction import Transaction
    t = Transaction(
        transaction_date=date(2026, 9, 13),
        entry_date=date(2026, 9, 13),
        transaction_type="materials_cost",
        business_line=unit.business_line,
        unit_id=unit.id,
        vendor_id=vendor.id if vendor else None,
        amount=amount,
        description="Brake job",
        category="materials_cost",
        review_status="pending",
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"
    return t


# ---------------------------------------------------------------------------
# Queue contents
# ---------------------------------------------------------------------------

def test_vehicle_purchase_appears_in_review_queue(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00001", stock_number="LOT-T00001")
    assert r.status_code == 303

    resp = client.get("/review/")
    assert resp.status_code == 200
    assert "1FAFP42X1YFT00001" in resp.text
    assert "LOT-T00001" in resp.text


def test_review_card_shows_correct_details(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00002", stock_number="LOT-T00002")
    assert r.status_code == 303

    resp = client.get("/review/")
    assert "Lakeland Auto Auction" in resp.text
    assert "LOT-T00002" in resp.text
    assert "1FAFP42X1YFT00002" in resp.text
    assert "$6500.00" in resp.text
    assert "$200.00" in resp.text
    assert "$75.00" in resp.text
    assert "$6775.00" in resp.text  # acquisition cost = 6500 + 200 + 75


def test_vehicle_purchase_unit_does_not_create_duplicate_review_item(client, db):
    """
    The Purchase's own acquisition Transaction is linked to the same Unit
    it just created — it must not also surface as a separate "expense"
    card. This unique VIN should therefore appear exactly once on the page.
    """
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00003", stock_number="LOT-T00003")
    assert r.status_code == 303

    resp = client.get("/review/")
    assert resp.text.count("1FAFP42X1YFT00003") == 1


def test_unit_linked_expense_appears_in_review_queue(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00004", stock_number="LOT-T00004")
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.stock_number == "LOT-T00004").first()
    vendor = make_vendor(db, name="Test Tire Shop T00004")
    make_unit_linked_transaction(db, purchase.unit, amount=Decimal("450.00"), vendor=vendor)
    db.commit()

    resp = client.get("/review/")
    assert "Test Tire Shop T00004" in resp.text
    assert "$450.00" in resp.text


def test_sale_transaction_on_unit_not_shown_as_expense(client, db):
    """
    A sale transaction linked to a unit is revenue, not a purchasing/expense
    data-quality concern — it must not leak into the review queue.
    """
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00014", stock_number="LOT-T00014")
    assert r.status_code == 303
    from app.models.purchase import Purchase
    from app.models.transaction import Transaction
    purchase = db.query(Purchase).filter(Purchase.stock_number == "LOT-T00014").first()

    sale_txn = Transaction(
        transaction_date=date(2026, 9, 13),
        entry_date=date(2026, 9, 13),
        transaction_type="sale",
        business_line=purchase.unit.business_line,
        unit_id=purchase.unit.id,
        amount=Decimal("9000.00"),
        description="Sale of unit",
        review_status="pending",
    )
    db.add(sale_txn)
    db.flush()
    sale_txn.transaction_id = f"T-TEST-SALE-{sale_txn.id}"
    db.commit()

    resp = client.get("/review/?show=expenses")
    assert "$9000.00" not in resp.text


def test_non_unit_linked_parts_purchase_transaction_not_shown_as_expense(client, db):
    """
    A parts purchase creates a Transaction too, but it has no unit_id —
    it should appear as its own Purchase card, not leak into the
    unit-linked "expenses" category.
    """
    r = post_parts_purchase(client, vendor="Test Parts Vendor T00005")
    assert r.status_code == 303

    resp = client.get("/review/?show=expenses")
    assert "Test Parts Vendor T00005" not in resp.text


# ---------------------------------------------------------------------------
# Mark Reviewed
# ---------------------------------------------------------------------------

def test_mark_purchase_reviewed_removes_it_from_default_queue(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00006", stock_number="LOT-T00006")
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.stock_number == "LOT-T00006").first()

    resp = client.get("/review/")
    assert "1FAFP42X1YFT00006" in resp.text

    r2 = client.post(f"/review/purchases/{purchase.id}/reviewed", follow_redirects=False)
    assert r2.status_code == 303

    resp2 = client.get("/review/")
    assert "1FAFP42X1YFT00006" not in resp2.text

    db.expire(purchase)
    from app.models.enums import ReviewStatus
    assert purchase.review_status == ReviewStatus.reviewed


def test_mark_transaction_reviewed_removes_it_from_default_queue(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00007", stock_number="LOT-T00007")
    assert r.status_code == 303
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.stock_number == "LOT-T00007").first()
    vendor = make_vendor(db, name="Test Tire Shop T00007")
    t = make_unit_linked_transaction(db, purchase.unit, vendor=vendor)
    db.commit()

    resp = client.get("/review/")
    assert "Test Tire Shop T00007" in resp.text

    r2 = client.post(f"/review/transactions/{t.id}/reviewed", follow_redirects=False)
    assert r2.status_code == 303

    resp2 = client.get("/review/")
    assert "Test Tire Shop T00007" not in resp2.text


def test_reviewed_item_still_visible_under_reviewed_filter(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00008", stock_number="LOT-T00008")
    assert r.status_code == 303
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.stock_number == "LOT-T00008").first()

    client.post(f"/review/purchases/{purchase.id}/reviewed", follow_redirects=False)

    resp = client.get("/review/?show=reviewed")
    assert "1FAFP42X1YFT00008" in resp.text

    resp_default = client.get("/review/")
    assert "1FAFP42X1YFT00008" not in resp_default.text


# ---------------------------------------------------------------------------
# Data quality flags
# ---------------------------------------------------------------------------

def test_missing_vin_flag_appears_for_vehicle_purchase_without_vin(client, db):
    r = post_vehicle_purchase(client, vin="", stock_number="LOT-T00009")
    assert r.status_code == 303

    resp = client.get("/review/")
    card = card_containing(resp.text, "LOT-T00009")
    assert "Missing VIN" in card


def test_missing_stock_number_flag_appears(client, db):
    r = post_vehicle_purchase(client, stock_number="", vin="1FAFP42X1YFT00010")
    assert r.status_code == 303

    resp = client.get("/review/")
    card = card_containing(resp.text, "1FAFP42X1YFT00010")
    assert "Missing stock number" in card


def test_zero_amount_flag_appears_for_expense(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00011", stock_number="LOT-T00011")
    assert r.status_code == 303
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.stock_number == "LOT-T00011").first()
    vendor = make_vendor(db, name="Test Zero Amount Vendor T00011")
    make_unit_linked_transaction(db, purchase.unit, amount=Decimal("0"), vendor=vendor)
    db.commit()

    resp = client.get("/review/")
    card = card_containing(resp.text, "Test Zero Amount Vendor T00011")
    assert "Zero or negative amount" in card


def test_clean_vehicle_purchase_has_no_flags(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YFT00012", stock_number="LOT-T00012")
    assert r.status_code == 303

    resp = client.get("/review/")
    card = card_containing(resp.text, "1FAFP42X1YFT00012")
    assert "Missing VIN" not in card
    assert "Missing stock number" not in card
    assert "has-flags" not in card


# ---------------------------------------------------------------------------
# Nav / count
# ---------------------------------------------------------------------------

def test_review_count_endpoint_reflects_pending_items(client, db):
    r0 = client.get("/review/count")
    assert r0.status_code == 200
    before = r0.json()["count"]

    post_vehicle_purchase(client, vin="1FAFP42X1YFT00013", stock_number="LOT-T00013")

    r1 = client.get("/review/count")
    assert r1.json()["count"] == before + 1


def test_review_queue_returns_200_when_empty(client):
    resp = client.get("/review/")
    assert resp.status_code == 200
