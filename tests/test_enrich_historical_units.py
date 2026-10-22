"""
Tests for the one-time historical Unit enrichment script
(app/scripts/enrich_historical_units.py).

Most tests exercise the pure parsing functions (find_year,
find_make_and_model) directly -- no database needed. A few integration
tests use the test's own isolated db session to confirm the
read/skip-existing/write orchestration (run()) behaves correctly, without
ever touching the real local Postgres SessionLocal().
"""
from datetime import date
from decimal import Decimal

from app.scripts.enrich_historical_units import (
    find_year, find_make_and_model, run, historical_vehicle_units,
)


# ---------------------------------------------------------------------------
# Year parsing
# ---------------------------------------------------------------------------

def test_year_found_clear_4_digit():
    year, reason = find_year("2014 Ford Escape Titanium")
    assert year == 2014


def test_year_found_when_not_first_token():
    year, _ = find_year("GMC Terrain 2013.   Black")
    assert year == 2013


def test_year_glued_to_make_still_found():
    year, _ = find_year("2013Ford Escape red")
    assert year == 2013


def test_year_out_of_range_typo_rejected():
    year, reason = find_year("1015 Ford Expedition. White")
    assert year is None
    assert "range" in reason


def test_year_far_out_of_range_typo_rejected():
    year, _ = find_year("2100 Lexus ES 350. Green")
    assert year is None


def test_two_digit_year_not_supported_stays_unresolved():
    """Deliberate design decision: a bare 2-digit number is not interpreted
    as a year, since it's too easily something else (voltage, trim code)."""
    year, reason = find_year("14 Ford Escape")
    assert year is None
    assert "no 4-digit number" in reason


def test_ambiguous_multiple_in_range_years_unresolved():
    year, reason = find_year("traded a 2007 Ford focus for a 2015 Chevy equinox")
    assert year is None
    assert "ambiguous" in reason


def test_dollar_amount_and_mileage_do_not_get_mistaken_for_year():
    """$1970 looks like a year-shaped number but is out of range; 148523 is
    6 digits (mileage) and never even matches the 4-digit filter."""
    year, _ = find_year("$1970 2015 Ford fusion red. Mil-148523")
    assert year == 2015


def test_no_year_present():
    year, reason = find_year("Seat belt repair")
    assert year is None


# ---------------------------------------------------------------------------
# Make/model parsing — obvious cases
# ---------------------------------------------------------------------------

def test_obvious_full_vehicle_description():
    make, model, _ = find_make_and_model("2014 Ford Escape Titanium")
    assert make == "Ford"
    assert model == "Escape"


def test_trim_suffix_is_dropped():
    make, model, _ = find_make_and_model("2011 GMC Acadia Denali")
    assert make == "GMC"
    assert model == "Acadia"


def test_upper_and_lower_case_variations():
    make, model, _ = find_make_and_model("2014 FORD ESCAPE")
    assert make == "Ford"
    assert model == "Escape"

    make2, model2, _ = find_make_and_model("2015 ford fusion se")
    assert make2 == "Ford"
    assert model2 == "Fusion"


def test_trim_word_after_model_in_parts_note():
    make, model, _ = find_make_and_model("2014 Ford Escape Titanium parts")
    assert make == "Ford"
    assert model == "Escape"


def test_chevy_abbreviation_normalizes_to_chevrolet():
    make, model, _ = find_make_and_model("2013 Chevy sonic blue")
    assert make == "Chevrolet"
    assert model == "Sonic"


def test_color_before_make_does_not_interfere():
    make, model, _ = find_make_and_model("White 2015 Buick encore leather")
    assert make == "Buick"
    assert model == "Encore"


def test_color_immediately_after_make_is_skipped():
    make, model, _ = find_make_and_model("2015 blk Ford Escape")
    assert make == "Ford"
    assert model == "Escape"


def test_repeated_make_word_as_model_prefix_is_skipped():
    make, model, _ = find_make_and_model("2012 Mazda Mazda 3.  black")
    assert make == "Mazda"
    assert model == "3"


def test_written_out_number_word_still_captured_literally():
    """Not corrected to a digit -- 'three' is taken literally, since
    guessing it means '3' would be inventing, even though plausible."""
    make, model, _ = find_make_and_model("2013 Mazda Mazda three sport")
    assert make == "Mazda"
    assert model == "Three"


def test_short_alpha_code_combines_with_following_number():
    make, model, _ = find_make_and_model("2011. Blk Lexus is 250")
    assert make == "Lexus"
    assert model == "IS250"


def test_short_alpha_code_alone_is_kept_as_is():
    make, model, _ = find_make_and_model("2014 Acura RDX. White")
    assert make == "Acura"
    assert model == "RDX"


def test_year_glued_to_make_model_still_parses():
    make, model, _ = find_make_and_model("2013Ford Escape red")
    assert make == "Ford"
    assert model == "Escape"


def test_numeric_model_after_make():
    make, model, _ = find_make_and_model("2015 Chrysler 200 S gray")
    assert make == "Chrysler"
    assert model == "200"


# ---------------------------------------------------------------------------
# Ambiguous / unresolvable notes
# ---------------------------------------------------------------------------

def test_bare_model_word_without_make_is_unresolved():
    make, model, reason = find_make_and_model("Escape")
    assert make is None
    assert model is None


def test_generic_word_is_unresolved():
    make, model, _ = find_make_and_model("car")
    assert make is None
    assert model is None


def test_non_vehicle_note_is_unresolved():
    make, model, _ = find_make_and_model("auction fee")
    assert make is None
    assert model is None


def test_misspelled_make_is_not_guessed():
    """'FOR FUSION' is a real typo in the historical data for 'FORD
    FUSION' -- must not be corrected/guessed."""
    make, model, _ = find_make_and_model("BLK 2016 FOR FUSION TITANIUM")
    assert make is None
    assert model is None


def test_part_only_note_is_unresolved():
    make, model, _ = find_make_and_model("Trailer lights")
    assert make is None
    assert model is None


# ---------------------------------------------------------------------------
# DB integration — existing values, dry run
# ---------------------------------------------------------------------------

def make_historical_vehicle_unit(db, notes, source_row, stock_number,
                                  unit_year=None, unit_make=None, unit_model=None):
    from app.models.vendor import Vendor
    from app.models.unit import Unit
    from app.models.purchase import Purchase
    from app.models.transaction import Transaction
    from app.models.enums import UnitType, BusinessLine, PurchaseCategory, ReviewStatus

    vendor = Vendor(name=f"Vendor {source_row}")
    db.add(vendor)
    db.flush()
    vendor.vendor_id = f"V-{vendor.id:04d}"

    unit = Unit(
        unit_type=UnitType.car, business_line=BusinessLine.car,
        status="in_repair", year=unit_year, make=unit_make, model=unit_model,
    )
    db.add(unit)
    db.flush()
    unit.unit_id = f"U-{unit.id:04d}"

    t = Transaction(
        transaction_date=date(2025, 10, 6), entry_date=date(2025, 10, 6),
        transaction_type="purchase", business_line="car",
        vendor_id=vendor.id, unit_id=unit.id, amount=Decimal("5000.00"),
        description=notes, category="auction_car_rto",
        review_status=ReviewStatus.reviewed,
    )
    db.add(t)
    db.flush()
    t.transaction_id = f"T-{t.id:05d}"

    purchase = Purchase(
        purchase_date=date(2025, 10, 6), purchaser="Steve", vendor_id=vendor.id,
        amount=Decimal("5000.00"), category=PurchaseCategory.auction_car_rto,
        stock_number=stock_number, unit_id=unit.id, description=notes,
        review_status=ReviewStatus.reviewed, transaction_id=t.id,
        source_reference=f"historical-import:{source_row}",
    )
    db.add(purchase)
    db.flush()
    purchase.purchase_id = f"PO-{purchase.id:04d}"
    db.commit()
    return unit, purchase


def test_run_populates_all_three_fields_for_clean_note(db):
    make_historical_vehicle_unit(db, "2014 Ford Escape Titanium white", "2001", "STK-2001")

    summary = run(db, dry_run=False)

    matches = [p for p in summary.proposals if p.purchase.stock_number == "STK-2001"]
    assert len(matches) == 1
    proposal = matches[0]
    assert proposal.action == "update"
    assert proposal.unit.year == 2014
    assert proposal.unit.make == "Ford"
    assert proposal.unit.model == "Escape"


def test_existing_unit_values_are_not_overwritten(db):
    unit, purchase = make_historical_vehicle_unit(
        db, "2014 Ford Escape Titanium white", "2002", "STK-2002",
        unit_year=2020, unit_make="Toyota", unit_model="Corolla",
    )

    run(db, dry_run=False)

    db.expire(unit)
    assert unit.year == 2020
    assert unit.make == "Toyota"
    assert unit.model == "Corolla"


def test_partial_existing_value_only_fills_the_blank_fields(db):
    unit, purchase = make_historical_vehicle_unit(
        db, "2014 Ford Escape Titanium white", "2003", "STK-2003",
        unit_year=2020,  # year already set; make/model still blank
    )

    run(db, dry_run=False)

    db.expire(unit)
    assert unit.year == 2020          # untouched
    assert unit.make == "Ford"        # filled in
    assert unit.model == "Escape"     # filled in


def test_dry_run_writes_nothing(db):
    unit, purchase = make_historical_vehicle_unit(db, "2014 Ford Escape Titanium white", "2004", "STK-2004")

    run(db, dry_run=True)

    db.expire(unit)
    assert unit.year is None
    assert unit.make is None
    assert unit.model is None


def test_non_historical_purchase_is_never_inspected(db):
    """A native/UI Purchase (source_reference NULL) must never be touched
    by historical_vehicle_units()."""
    from app.models.vendor import Vendor
    from app.models.unit import Unit
    from app.models.purchase import Purchase
    from app.models.enums import UnitType, BusinessLine, PurchaseCategory

    vendor = Vendor(name="Native Vendor")
    db.add(vendor)
    db.flush()
    vendor.vendor_id = "V-TEST-NATIVE"

    unit = Unit(unit_type=UnitType.car, business_line=BusinessLine.car, status="in_repair")
    db.add(unit)
    db.flush()
    unit.unit_id = "U-TEST-NATIVE"

    purchase = Purchase(
        purchase_date=date(2026, 1, 1), purchaser="Jane", vendor_id=vendor.id,
        amount=Decimal("1000.00"), category=PurchaseCategory.auction_car_rto,
        unit_id=unit.id, description="2020 Ford Escape",
        source_reference=None,
    )
    db.add(purchase)
    db.commit()

    pairs = historical_vehicle_units(db)
    assert unit.id not in [u.id for u, _ in pairs]
