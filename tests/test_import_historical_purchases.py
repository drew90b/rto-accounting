"""
Tests for the one-time historical Purchase importer
(app/scripts/import_historical_purchases.py).

Calls run_import() directly against the test's own isolated db session —
never through main()/SessionLocal(), which is bound to the real configured
DATABASE_URL and would otherwise hit the actual local Postgres database.

Uses small, hand-built CSV-row dicts, not the real 959-row file.
"""
from decimal import Decimal

from app.scripts.import_historical_purchases import Summary, run_import


def row(**overrides):
    """A raw CSV-row-shaped dict with sensible defaults, matching the real
    import-ready CSV's column names exactly."""
    data = {
        "purchase_date": "10/6/2025",
        "vendor": "AutoZone",
        "amount": "15.46",
        "shipping_cost": "0.00",
        "other_fees": "0.00",
        "category": 'PARTS - "RTO" car parts (Repair)',
        "site_location": "Eunice",
        "stock_number": "",
        "vin": "",
        "year": "",
        "make": "",
        "model": "",
        "mileage": "",
        "notes": "",
        "receipt_urls": "",
        "source_purchaser": "Steve",
        "source_timestamp": "10/6/2025 13:34:48",
        "source_row": "2",
        "review_status": "reviewed",
    }
    data.update(overrides)
    return data


def run(db, rows, dry_run):
    summary = Summary()
    run_import(rows, db, summary, dry_run=dry_run)
    return summary


# ---------------------------------------------------------------------------
# Dry run writes nothing
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(db):
    from app.models.purchase import Purchase
    from app.models.unit import Unit
    from app.models.vendor import Vendor

    units_before = db.query(Unit).count()

    rows = [row(source_row="100", vendor="Dry Run Test Vendor", stock_number="")]
    summary = run(db, rows, dry_run=True)

    assert summary.purchases_created == 1
    assert db.query(Purchase).filter(Purchase.source_reference == "historical-import:100").first() is None
    assert db.query(Vendor).filter(Vendor.name == "Dry Run Test Vendor").first() is None
    assert db.query(Unit).count() == units_before


# ---------------------------------------------------------------------------
# Vehicle acquisitions
# ---------------------------------------------------------------------------

def test_rto_vehicle_acquisition_creates_purchase_unit_and_transaction(db):
    rows = [row(
        source_row="200",
        vendor="Lakeland Auto Auction",
        amount="6500.00",
        category="AUCTION - Purchased Cars (RTO)",
        stock_number="LOT-200",
        vin="1FAFP42X1YFHIST001",
        year="2019", make="Ford", model="Fusion",
    )]
    summary = run(db, rows, dry_run=False)

    assert summary.purchases_created == 1
    assert summary.units_created == 1

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:200").first()
    assert purchase is not None
    assert purchase.unit is not None
    assert purchase.unit.vin_serial == "1FAFP42X1YFHIST001"
    assert purchase.unit.year == 2019
    assert purchase.unit.make == "Ford"
    assert purchase.transaction is not None
    assert purchase.transaction.unit_id == purchase.unit_id


def test_flip_vehicle_acquisition_creates_unit(db):
    rows = [row(
        source_row="201",
        vendor="Flip Auction House",
        amount="4000.00",
        category='AUCTION - Purchased Cars ("Flip")',
        stock_number="LOT-201",
    )]
    summary = run(db, rows, dry_run=False)

    assert summary.units_created == 1
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:201").first()
    assert purchase.unit is not None
    assert purchase.unit.business_line.value == "car"


def test_golf_cart_acquisition_creates_unit(db):
    rows = [row(
        source_row="202",
        vendor="Golf Cart Auction",
        amount="2500.00",
        category='AUCTION - Purchased Golf Carts - ("Flip")',
        stock_number="LOT-202",
    )]
    summary = run(db, rows, dry_run=False)

    assert summary.units_created == 1
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:202").first()
    assert purchase.unit is not None
    assert purchase.unit.business_line.value == "golf_cart"


def test_acquisition_cost_equals_amount_plus_shipping_plus_fees(db):
    rows = [row(
        source_row="203",
        category="AUCTION - Purchased Cars (RTO)",
        amount="5000.00",
        shipping_cost="200.00",
        other_fees="75.00",
        stock_number="LOT-203",
    )]
    run(db, rows, dry_run=False)

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:203").first()
    assert float(purchase.unit.acquisition_cost) == 5000.00 + 200.00 + 75.00
    assert float(purchase.transaction.amount) == 5000.00 + 200.00 + 75.00


# ---------------------------------------------------------------------------
# Non-vehicle purchases
# ---------------------------------------------------------------------------

def test_part_repair_purchase_does_not_create_unit(db):
    from app.models.unit import Unit
    before = db.query(Unit).count()

    rows = [row(source_row="300", category='PARTS - "RTO" car parts (Repair)', stock_number="")]
    summary = run(db, rows, dry_run=False)

    assert db.query(Unit).count() == before
    assert summary.units_created == 0


def test_part_expense_links_to_existing_unit_by_stock_number(db):
    """A part purchased for a vehicle acquired EARLIER in the same import
    (regardless of file order) links to that vehicle's Unit."""
    rows = [
        row(source_row="400", category="AUCTION - Purchased Cars (RTO)",
            amount="6000.00", stock_number="17726"),
        row(source_row="401", category='PARTS - "RTO" - Car parTs (WIP)',
            amount="45.00", vendor="AutoZone", stock_number="17726"),
    ]
    summary = run(db, rows, dry_run=False)

    from app.models.purchase import Purchase
    vehicle_purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:400").first()
    part_purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:401").first()

    assert vehicle_purchase.unit_id is not None
    assert part_purchase.unit_id == vehicle_purchase.unit_id
    assert part_purchase.transaction.unit_id == vehicle_purchase.unit_id
    assert summary.units_created == 1
    assert summary.unmatched_stock_numbers == 0


def test_part_expense_links_even_when_vehicle_row_appears_later_in_file(db):
    """Confirms the two-pass (vehicles first) design: order in the CSV
    doesn't matter."""
    rows = [
        row(source_row="500", category='PARTS - "RTO" - Car parTs (WIP)',
            amount="30.00", vendor="AutoZone", stock_number="55555"),
        row(source_row="501", category="AUCTION - Purchased Cars (RTO)",
            amount="7000.00", stock_number="55555"),
    ]
    run(db, rows, dry_run=False)

    from app.models.purchase import Purchase
    vehicle_purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:501").first()
    part_purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:500").first()
    assert part_purchase.unit_id == vehicle_purchase.unit_id


def test_unmatched_non_vehicle_stock_number_does_not_create_fake_unit(db):
    from app.models.unit import Unit
    before = db.query(Unit).count()

    rows = [row(source_row="600", category='PARTS - "RTO" - Car parTs (WIP)', stock_number="NO-SUCH-VEHICLE")]
    summary = run(db, rows, dry_run=False)

    assert db.query(Unit).count() == before
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:600").first()
    assert purchase.unit_id is None
    assert purchase.stock_number == "NO-SUCH-VEHICLE"
    assert summary.unmatched_stock_numbers == 1


# ---------------------------------------------------------------------------
# Review status
# ---------------------------------------------------------------------------

def test_imported_purchase_and_transaction_are_reviewed(db):
    from app.models.enums import ReviewStatus
    rows = [row(source_row="700")]
    run(db, rows, dry_run=False)

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:700").first()
    assert purchase.review_status == ReviewStatus.reviewed
    assert purchase.transaction.review_status == ReviewStatus.reviewed


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_duplicate_import_does_not_create_second_purchase(db):
    from app.models.purchase import Purchase

    rows = [row(source_row="800", vendor="Idempotence Test Vendor")]
    run(db, rows, dry_run=False)
    summary2 = run(db, rows, dry_run=False)

    assert summary2.duplicate_skipped == 1
    assert summary2.purchases_created == 0
    count = db.query(Purchase).filter(Purchase.source_reference == "historical-import:800").count()
    assert count == 1


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------

def test_bad_row_rolls_back_cleanly_without_blocking_later_rows(db):
    """
    Pre-existing units A and B (distinct unit_id/VIN) make the next row's
    stock_number match A while its VIN matches B — an unresolvable conflict
    that resolve_vehicle_unit() raises on. That failure must not block the
    row after it.
    """
    from app.models.purchase import Purchase
    from app.models.unit import Unit
    from app.models.enums import UnitType, BusinessLine

    unit_a = Unit(unit_type=UnitType.car, business_line=BusinessLine.car, vin_serial="HIST-VIN-A", status="in_repair")
    unit_b = Unit(unit_type=UnitType.car, business_line=BusinessLine.car, vin_serial="HIST-VIN-B", status="in_repair")
    db.add_all([unit_a, unit_b])
    db.flush()
    unit_a.unit_id = "U-HIST-CONFLICT-A"
    unit_b.unit_id = "U-HIST-CONFLICT-B"
    db.commit()

    rows = [
        row(source_row="900", category="AUCTION - Purchased Cars (RTO)",
            stock_number="U-HIST-CONFLICT-A", vin="HIST-VIN-B", amount="1000.00"),
        row(source_row="902", vendor="After The Bad Row"),
    ]
    summary = run(db, rows, dry_run=False)

    assert summary.failed == 1
    assert summary.purchases_created == 1
    assert db.query(Purchase).filter(Purchase.source_reference == "historical-import:900").first() is None
    assert db.query(Purchase).filter(Purchase.source_reference == "historical-import:902").first() is not None


def test_malformed_amount_is_reported_not_defaulted(db):
    rows = [row(source_row="903", amount="not-a-number")]
    summary = run(db, rows, dry_run=False)

    assert summary.purchases_created == 0
    assert summary.failed == 1
    assert "amount" in summary.failures[0][-1]


def test_malformed_date_is_reported_not_substituted(db):
    rows = [row(source_row="904", purchase_date="not-a-date")]
    summary = run(db, rows, dry_run=False)

    assert summary.purchases_created == 0
    assert summary.failed == 1
    assert "purchase_date" in summary.failures[0][-1]


def test_unrecognized_category_is_reported(db):
    rows = [row(source_row="905", category="Some Category That Does Not Exist")]
    summary = run(db, rows, dry_run=False)

    assert summary.purchases_created == 0
    assert summary.failed == 1
    assert "category" in summary.failures[0][-1]


# ---------------------------------------------------------------------------
# Legacy category remapping — WIP means prep-for-initial-sale, Repair means
# rework after the vehicle was already placed into service. Neither legacy
# label describes rework, so both must land on WIP, never Repair.
# ---------------------------------------------------------------------------

def test_legacy_rto_category_maps_to_wip_not_repair(db):
    rows = [row(source_row="1000", category='PARTS - "RTO" car parts (RTO)')]
    summary = run(db, rows, dry_run=False)

    assert summary.purchases_created == 1
    assert summary.legacy_category_counts == {'PARTS - "RTO" car parts (RTO)': 1}

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:1000").first()
    assert purchase.category.value == 'PARTS - "RTO" - Car parTs (WIP)'


def test_legacy_flip_built_to_sale_maps_to_flip_wip_not_repair(db):
    rows = [row(source_row="1001", category='PARTS - "Flip" - Car Parts (Built to sale)', vendor="AutoZone")]
    summary = run(db, rows, dry_run=False)

    assert summary.purchases_created == 1
    assert summary.legacy_category_counts == {'PARTS - "Flip" - Car Parts (Built to sale)': 1}

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:1001").first()
    assert purchase.category.value == 'PARTS - "Flip" - Car Parts (WIP)'


def test_existing_repair_categories_are_unaffected_by_legacy_remap(db):
    rows = [row(source_row="1002", category='PARTS - "RTO" car parts (Repair)')]
    summary = run(db, rows, dry_run=False)

    assert summary.purchases_created == 1
    assert summary.legacy_category_counts == {}

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:1002").first()
    assert purchase.category.value == 'PARTS - "RTO" car parts (Repair)'


# ---------------------------------------------------------------------------
# Historical mileage relaxation — this importer only.
# ---------------------------------------------------------------------------

def test_blank_mileage_becomes_null(db):
    rows = [row(source_row="1100", mileage="")]
    run(db, rows, dry_run=False)

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:1100").first()
    assert purchase.mileage is None


def test_valid_mileage_parses_normally(db):
    rows = [row(source_row="1101", mileage="145,912")]
    run(db, rows, dry_run=False)

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:1101").first()
    assert purchase.mileage == 145912


def test_malformed_mileage_becomes_null_with_warning_not_a_failure(db):
    rows = [row(source_row="1102", mileage="142 288", amount="99.00")]
    summary = run(db, rows, dry_run=False)

    assert summary.failed == 0
    assert summary.purchases_created == 1
    assert summary.mileage_defaulted_to_null == 1
    assert any("mileage" in w for w in summary.warnings)

    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:1102").first()
    assert purchase is not None
    assert purchase.mileage is None
    assert float(purchase.amount) == 99.00


def test_malformed_mileage_does_not_block_an_otherwise_valid_financial_purchase(db):
    """The exact scenario the relaxation exists for: a real dollar amount
    must never be lost over a stray note in the mileage field."""
    rows = [row(source_row="1103", mileage="97000.   Not actual", amount="250.00", vendor="Sattlers")]
    summary = run(db, rows, dry_run=False)

    assert summary.failed == 0
    from app.models.purchase import Purchase
    purchase = db.query(Purchase).filter(Purchase.source_reference == "historical-import:1103").first()
    assert purchase is not None
    assert float(purchase.amount) == 250.00
    assert purchase.mileage is None
