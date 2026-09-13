"""
Tests for the Purchases module — the Google Purchase Order Form replacement.

Covers:
  POST /purchases/new     — creates Purchase + linked Transaction, vendor
                             find-or-create, unit stock-number matching,
                             general (no-unit) purchases, receipt upload.
  POST /purchases/{id}/edit   — corrections re-sync the linked Transaction.
  POST /purchases/{id}/review — review-status toggle.
  GET  /purchases/            — ledger list + filters.
"""

import io
from decimal import Decimal


def make_unit(db, unit_id="U-TEST-DEFAULT", vin="1FAFP42X1YF123456"):
    from app.models.unit import Unit
    from app.models.enums import UnitType, BusinessLine
    u = Unit(unit_type=UnitType.car, business_line=BusinessLine.car, vin_serial=vin, status="in_repair")
    db.add(u)
    db.flush()
    u.unit_id = unit_id
    return u


def post_purchase(client, **overrides):
    data = {
        "purchaser": "Jane Doe",
        "purchase_date": "2026-09-10",
        "vendor": "AutoZone",
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


def post_vehicle_purchase(client, **overrides):
    data = {
        "purchaser": "Jane Doe",
        "purchase_date": "2026-09-10",
        "vendor": "Lakeland Auto Auction",
        "amount": "6500.00",
        "category": "AUCTION - Purchased Cars (RTO)",
        "site_location": "Eunice",
        "stock_number": "LOT-42",
        "mileage": "82000",
        "description": "",
        "entered_by": "",
        "shipping_cost": "200.00",
        "other_fees": "75.00",
        "vin": "1FAFP42X1YF123456",
        "year": "2019",
        "make": "Ford",
        "model": "Fusion",
    }
    data.update(overrides)
    return client.post("/purchases/new", data=data, follow_redirects=False)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def test_create_purchase_creates_purchase_and_transaction(client, db):
    r = post_purchase(client)
    assert r.status_code == 303, r.text[:500]
    assert "/purchases/" in r.headers["location"] and "/edit" in r.headers["location"]

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.purchaser == "Jane Doe").first()
    assert purchase is not None
    assert purchase.purchase_id.startswith("PO-")
    assert float(purchase.amount) == 125.50
    assert purchase.vendor is not None
    assert purchase.vendor.name == "AutoZone"

    assert purchase.transaction_id is not None
    t = purchase.transaction
    assert t.transaction_type.value == "purchase"
    assert t.business_line.value == "car"
    assert float(t.amount) == 125.50
    assert t.vendor_id == purchase.vendor_id


def test_create_purchase_finds_existing_vendor_case_insensitive(client, db):
    from app.models.vendor import Vendor
    existing = Vendor(name="Case Insensitive Vendor Co")
    db.add(existing)
    db.flush()
    existing.vendor_id = f"V-{existing.id:04d}"
    db.commit()

    r = post_purchase(client, vendor="case insensitive vendor co")
    assert r.status_code == 303

    vendors = db.query(Vendor).filter(Vendor.name.ilike("case insensitive vendor co")).all()
    assert len(vendors) == 1, "A new vendor should not be created for a case-insensitive match"

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.vendor_id == existing.id


def test_create_purchase_links_matching_unit_by_stock_number(client, db):
    unit = make_unit(db)
    db.commit()

    r = post_purchase(client, stock_number="U-TEST-DEFAULT")
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.unit_id == unit.id
    assert purchase.transaction.unit_id == unit.id


def test_create_purchase_with_unmatched_stock_number_does_not_create_unit(client, db):
    r = post_purchase(client, stock_number="NOT-A-REAL-UNIT-XYZ")
    assert r.status_code == 303

    from app.models.purchase import Purchase
    from app.models.unit import Unit
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.unit_id is None
    assert purchase.stock_number == "NOT-A-REAL-UNIT-XYZ"
    assert db.query(Unit).filter(Unit.unit_id == "NOT-A-REAL-UNIT-XYZ").first() is None
    assert db.query(Unit).filter(Unit.vin_serial == "NOT-A-REAL-UNIT-XYZ").first() is None


def test_create_general_purchase_with_no_stock_number(client, db):
    """A non-vehicle purchase may legitimately have no associated unit."""
    r = post_purchase(client, stock_number="", category='PARTS - "RTO" car parts (Repair)')
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.unit_id is None
    assert purchase.transaction.business_line.value == "car"


def test_golf_cart_category_sets_business_line(client, db):
    r = post_purchase(client, category='PARTS - "Repairs" - Golf Cart Parts - (repairs only)')
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.transaction.business_line.value == "golf_cart"


def test_create_purchase_with_receipt_marks_receipt_attached(client, db):
    r = client.post(
        "/purchases/new",
        data={
            "purchaser": "Jane Doe",
            "purchase_date": "2026-09-10",
            "vendor": "AutoZone",
            "amount": "50.00",
            "category": 'PARTS - "RTO" car parts (Repair)',
            "site_location": "Eunice",
            "stock_number": "",
            "mileage": "",
            "description": "",
            "entered_by": "",
        },
        files={"receipt": ("receipt.jpg", io.BytesIO(b"fake-image-bytes"), "image/jpeg")},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from app.models.purchase import Purchase
    from app.models.document import Document
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.receipt_attached is True
    assert purchase.transaction.receipt_attached is True

    doc = db.query(Document).filter(
        Document.linked_record_type == "purchase", Document.linked_record_id == purchase.id
    ).first()
    assert doc is not None
    assert doc.original_filename == "receipt.jpg"


def test_create_purchase_without_receipt_leaves_receipt_unattached(client, db):
    r = post_purchase(client)
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.receipt_attached is not True


# ---------------------------------------------------------------------------
# Vehicle purchase -> Unit creation
# ---------------------------------------------------------------------------

def test_vehicle_purchase_creates_unit_automatically(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YF000001")
    assert r.status_code == 303, r.text[:500]

    from app.models.purchase import Purchase
    from app.models.unit import Unit
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()

    assert purchase.unit_id is not None
    unit = db.query(Unit).filter(Unit.id == purchase.unit_id).first()
    assert unit is not None
    assert unit.unit_id.startswith("U-")
    assert unit.vin_serial == "1FAFP42X1YF000001"
    assert unit.year == 2019
    assert unit.make == "Ford"
    assert unit.model == "Fusion"
    assert unit.business_line.value == "car"
    assert unit.purchase_source == "Lakeland Auto Auction"

    # Acquisition Cost = purchase price + shipping + other fees
    assert float(unit.acquisition_cost) == 6500.00 + 200.00 + 75.00


def test_vehicle_purchase_links_purchase_to_new_unit(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YF000002")
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.unit is not None
    assert purchase.unit.vin_serial == "1FAFP42X1YF000002"


def test_vehicle_purchase_still_finds_or_creates_vendor(client, db):
    from app.models.vendor import Vendor
    r = post_vehicle_purchase(client, vin="1FAFP42X1YF000003")
    assert r.status_code == 303

    vendors = db.query(Vendor).filter(Vendor.name == "Lakeland Auto Auction").all()
    assert len(vendors) == 1


def test_vehicle_purchase_creates_transaction_for_full_acquisition_cost(client, db):
    r = post_vehicle_purchase(client, vin="1FAFP42X1YF000004")
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    t = purchase.transaction
    assert t is not None
    assert t.transaction_type.value == "purchase"
    assert t.unit_id == purchase.unit_id
    assert float(t.amount) == 6500.00 + 200.00 + 75.00


def test_vehicle_purchase_duplicate_stock_number_links_existing_unit_not_duplicate(client, db):
    unit = make_unit(db, unit_id="U-TEST-STOCKMATCH", vin="1FAFP42X1YF000005")
    db.commit()

    from app.models.unit import Unit
    before_count = db.query(Unit).count()

    r = post_vehicle_purchase(client, stock_number="U-TEST-STOCKMATCH", vin="")
    assert r.status_code == 303

    assert db.query(Unit).count() == before_count, "No new unit should have been created"
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.unit_id == unit.id


def test_vehicle_purchase_duplicate_vin_links_existing_unit_not_duplicate(client, db):
    unit = make_unit(db, unit_id="U-TEST-VINMATCH", vin="1FAFP42X1YF000006")
    db.commit()

    from app.models.unit import Unit
    before_count = db.query(Unit).count()

    r = post_vehicle_purchase(client, stock_number="", vin="1FAFP42X1YF000006")
    assert r.status_code == 303

    assert db.query(Unit).count() == before_count, "No new unit should have been created"
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.unit_id == unit.id


def test_vehicle_purchase_conflicting_stock_number_and_vin_is_rejected(client, db):
    """
    Also the atomicity proof: when stock number and VIN point to two
    different existing units, resolve_vehicle_unit() raises before any row
    is written, so the Purchase and its Transaction never get created
    either — nothing is left half-saved.
    """
    unit_a = make_unit(db, unit_id="U-TEST-CONFLICT-A", vin="AAAAAAAAAAAAAAAAA")
    unit_b = make_unit(db, unit_id="U-TEST-CONFLICT-B", vin="BBBBBBBBBBBBBBBBB")
    db.commit()

    from app.models.unit import Unit
    from app.models.purchase import Purchase
    from app.models.transaction import Transaction
    before_units = db.query(Unit).count()
    before_purchases = db.query(Purchase).count()
    before_txns = db.query(Transaction).count()

    # Stock number matches unit_a, VIN matches unit_b — an unresolvable conflict
    r = post_vehicle_purchase(client, stock_number="U-TEST-CONFLICT-A", vin="BBBBBBBBBBBBBBBBB")
    assert r.status_code == 303
    assert "error=" in r.headers["location"]

    assert db.query(Unit).count() == before_units, "No unit should be created on conflict"
    assert db.query(Purchase).count() == before_purchases, "No purchase should be saved on conflict"
    assert db.query(Transaction).count() == before_txns, "No transaction should be saved on conflict"


def test_non_vehicle_purchase_does_not_create_unit(client, db):
    from app.models.unit import Unit
    before_count = db.query(Unit).count()

    r = post_purchase(client, category='PARTS - "Flip" - Car Parts (Repair)', stock_number="")
    assert r.status_code == 303

    assert db.query(Unit).count() == before_count

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.unit_id is None


# ---------------------------------------------------------------------------
# Edit / correction
# ---------------------------------------------------------------------------

def test_edit_purchase_syncs_linked_transaction(client, db):
    r = post_purchase(client, amount="100.00")
    assert r.status_code == 303

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()

    r2 = client.post(
        f"/purchases/{purchase.id}/edit",
        data={
            "purchaser": "Jane Doe",
            "purchase_date": "2026-09-11",
            "vendor": "NAPA Auto Parts",
            "amount": "200.00",
            "category": 'PARTS - "Flip" - Car Parts (Repair)',
            "site_location": "Eunice",
            "stock_number": "",
            "mileage": "",
            "description": "Corrected entry",
            "entered_by": "",
        },
        follow_redirects=False,
    )
    assert r2.status_code == 303

    db.expire(purchase)
    assert float(purchase.amount) == 200.00
    assert purchase.vendor.name == "NAPA Auto Parts"
    assert purchase.transaction is not None
    assert float(purchase.transaction.amount) == 200.00
    assert purchase.transaction.vendor.name == "NAPA Auto Parts"
    assert purchase.transaction.description == "Corrected entry"


# ---------------------------------------------------------------------------
# Review workflow
# ---------------------------------------------------------------------------

def test_review_toggle_marks_reviewed_then_back_to_needs_review(client, db):
    r = post_purchase(client)
    assert r.status_code == 303

    from app.models.purchase import Purchase
    from app.models.enums import ReviewStatus
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    assert purchase.review_status == ReviewStatus.pending

    r2 = client.post(f"/purchases/{purchase.id}/review", follow_redirects=False)
    assert r2.status_code == 303
    db.expire(purchase)
    assert purchase.review_status == ReviewStatus.reviewed

    r3 = client.post(f"/purchases/{purchase.id}/review", follow_redirects=False)
    assert r3.status_code == 303
    db.expire(purchase)
    assert purchase.review_status == ReviewStatus.pending


# ---------------------------------------------------------------------------
# Ledger / list
# ---------------------------------------------------------------------------

def test_purchase_list_returns_200(client):
    response = client.get("/purchases/")
    assert response.status_code == 200


def test_purchase_new_form_returns_200(client):
    response = client.get("/purchases/new")
    assert response.status_code == 200


def test_purchase_list_filters_by_vendor(client, db):
    post_purchase(client, vendor="AutoZone")
    post_purchase(client, vendor="NAPA Auto Parts")

    r = client.get("/purchases/", params={"vendor": "NAPA"})
    assert r.status_code == 200
    assert "NAPA Auto Parts" in r.text
    assert "AutoZone" not in r.text


def test_purchase_list_filters_by_review_status(client, db):
    r0 = post_purchase(client, vendor="Reviewed Status Vendor")
    assert r0.status_code == 303
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).order_by(Purchase.id.desc()).first()
    client.post(f"/purchases/{purchase.id}/review", follow_redirects=False)

    r = client.get("/purchases/", params={"review_status": "reviewed"})
    assert r.status_code == 200
    assert f'/purchases/{purchase.id}/edit' in r.text
    assert "Reviewed Status Vendor" in r.text

    r2 = client.get("/purchases/", params={"review_status": "pending"})
    assert "Reviewed Status Vendor" not in r2.text
