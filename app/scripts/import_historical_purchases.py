"""
One-time historical Purchase importer.

Reads a cleaned CSV and creates Purchase/Unit/Transaction records through
the exact same app.services.purchase_service.record_purchase() the live
Purchases form uses, so imported records behave identically to native ones.
This is a one-time migration tool, not a generalized ETL framework — there
is no plugin system, no mapping DSL, just explicit field-by-field parsing.

Usage:
    python -m app.scripts.import_historical_purchases <csv_path> --dry-run
    python -m app.scripts.import_historical_purchases <csv_path> --commit

Neither flag: refuses to run. Both flags: refuses to run. This is
deliberately hard to fire by accident.

Two-pass processing:
    Vehicle-acquisition rows (is_vehicle_purchase(category) is True) are
    processed first, building an in-memory map of
    {historical stock number -> newly created Unit}. Non-vehicle rows are
    processed second, so a parts/repair row referencing a stock number
    acquired earlier IN THIS FILE links to that Unit regardless of which
    order the two rows happen to appear in the CSV. This is the only
    importer-specific logic in the whole script — every actual business
    rule (vendor find-or-create, Unit creation, VIN/stock matching,
    acquisition cost, business_line) is the existing, unmodified
    app.services.purchase_service code, called exactly as the route calls it.

Idempotence:
    Every imported Purchase gets source_reference =
    "historical-import:<source_row>". Before processing a row, the importer
    checks whether a Purchase with that reference already exists and skips
    it if so. The column also has a database-level unique constraint as a
    backstop. Re-running the importer against the same file is always safe.

Dry run:
    Runs every row through the exact same code path as a real import, then
    rolls back everything — nothing is ever committed, so dry-run output is
    guaranteed to match what a real run would do, since it's the same code.
    Per-row failures use a SAVEPOINT (db.begin_nested()) so one bad row
    doesn't lose the rest of the dry run.

Real run (--commit):
    One database transaction per row. A bad row is rolled back and the
    import continues; nothing is left half-created for that row.
"""
import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.purchase import Purchase
from app.models.unit import Unit
from app.models.vendor import Vendor
from app.models.enums import PurchaseCategory, ReviewStatus
from app.services.purchase_service import record_purchase, is_vehicle_purchase

SOURCE_REFERENCE_PREFIX = "historical-import"
ENTERED_BY = "Historical Import"

# Explicit historical-only category remaps. NOT enum normalization — these
# two legacy strings don't match any current PurchaseCategory at all, and
# per the business definition (WIP = prep for INITIAL sale/RTO placement;
# Repair = rework after the vehicle was already placed into service), both
# legacy labels below describe prep work, not rework, so they map to the
# WIP variant of their line — never to Repair. Scoped to this importer only;
# the PurchaseCategory enum itself is not touched.
LEGACY_CATEGORY_MAP = {
    'PARTS - "RTO" car parts (RTO)': PurchaseCategory.parts_rto_car_wip,
    'PARTS - "Flip" - Car Parts (Built to sale)': PurchaseCategory.parts_flip_car_wip,
}


class RowError(Exception):
    """A single CSV row failed to parse or validate. Message is the reason."""


# ---------------------------------------------------------------------------
# Parsing — explicit, one field at a time. No mapping DSL.
# ---------------------------------------------------------------------------

@dataclass
class ParsedRow:
    source_row: str
    source_reference: str
    purchase_date: date
    purchaser: str
    vendor: str
    amount: Decimal
    shipping_cost: Decimal
    other_fees: Decimal
    category: PurchaseCategory
    site_location: str
    stock_number: str
    vin: str
    year: Optional[int]
    make: str
    model: str
    mileage: Optional[int]
    notes: str
    receipt_urls: str


def _parse_date(raw: str, source_row: str) -> date:
    raw = raw.strip()
    if not raw:
        raise RowError(f"purchase_date is blank")
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        raise RowError(f"unparseable purchase_date {raw!r}")


def _parse_money(raw: str, field_name: str, allow_blank_zero: bool = False) -> Decimal:
    raw = raw.strip()
    if not raw:
        if allow_blank_zero:
            return Decimal("0")
        raise RowError(f"{field_name} is blank")
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise RowError(f"unparseable {field_name} {raw!r}")


def _parse_mileage(raw: str) -> tuple:
    """
    Mileage gets the one deliberate relaxation in this importer: malformed
    historical mileage becomes NULL + a warning, never a row failure. An
    otherwise-valid financial purchase must not be lost over a stray note
    like "142 288" or "97000.   Not actual" in a decades-old mileage field.
    Returns (value_or_None, warning_or_None).
    """
    raw = raw.strip()
    if not raw:
        return None, None
    cleaned = raw.replace(",", "")
    if not cleaned.isdigit():
        return None, f"unparseable mileage {raw!r} — imported as blank"
    return int(cleaned), None


def _parse_year(raw: str) -> Optional[int]:
    raw = raw.strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise RowError(f"unparseable year {raw!r}")
    return int(raw)


def _parse_category(raw: str) -> tuple:
    """Returns (category, legacy_raw_string_or_None) — the second element is
    set only when raw was remapped via LEGACY_CATEGORY_MAP, so the caller can
    count which legacy label it came from."""
    raw = raw.strip()
    if raw in LEGACY_CATEGORY_MAP:
        return LEGACY_CATEGORY_MAP[raw], raw
    try:
        return PurchaseCategory(raw), None
    except ValueError:
        raise RowError(f"category does not match any current PurchaseCategory: {raw!r}")


def parse_row(raw: dict) -> tuple:
    """
    Raises RowError on any invalid field except mileage (see _parse_mileage).
    Never guesses or defaults a real value otherwise.
    Returns (ParsedRow, warnings, legacy_category_raw_or_None).
    """
    source_row = (raw.get("source_row") or "").strip() or "?"
    warnings = []

    purchaser = (raw.get("source_purchaser") or "").strip()
    if not purchaser:
        raise RowError(f"row {source_row}: source_purchaser is blank")
    vendor = (raw.get("vendor") or "").strip()
    if not vendor:
        raise RowError(f"row {source_row}: vendor is blank")

    try:
        category, legacy_raw = _parse_category(raw["category"])
        mileage, mileage_warning = _parse_mileage(raw.get("mileage") or "")
        if mileage_warning:
            warnings.append(mileage_warning)

        parsed = ParsedRow(
            source_row=source_row,
            source_reference=f"{SOURCE_REFERENCE_PREFIX}:{source_row}",
            purchase_date=_parse_date(raw["purchase_date"], source_row),
            purchaser=purchaser,
            vendor=vendor,
            amount=_parse_money(raw["amount"], "amount"),
            shipping_cost=_parse_money(raw["shipping_cost"], "shipping_cost", allow_blank_zero=True),
            other_fees=_parse_money(raw["other_fees"], "other_fees", allow_blank_zero=True),
            category=category,
            site_location=(raw.get("site_location") or "").strip(),
            stock_number=(raw.get("stock_number") or "").strip(),
            vin=(raw.get("vin") or "").strip(),
            year=_parse_year(raw.get("year") or ""),
            make=(raw.get("make") or "").strip(),
            model=(raw.get("model") or "").strip(),
            mileage=mileage,
            notes=(raw.get("notes") or "").strip(),
            receipt_urls=(raw.get("receipt_urls") or "").strip(),
        )
        return parsed, warnings, legacy_raw
    except RowError as e:
        raise RowError(f"row {source_row}: {e}" if not str(e).startswith("row ") else str(e))


def read_csv(path: Path) -> list:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# Summary / reporting
# ---------------------------------------------------------------------------

@dataclass
class Summary:
    rows_read: int = 0
    purchases_created: int = 0
    units_created: int = 0
    units_linked: int = 0
    transactions_created: int = 0
    vendors_created: int = 0
    vendors_linked: int = 0
    unmatched_stock_numbers: int = 0
    duplicate_skipped: int = 0
    failed: int = 0
    mileage_defaulted_to_null: int = 0
    legacy_category_counts: dict = field(default_factory=dict)  # {legacy raw string: count remapped}
    failures: list = field(default_factory=list)  # (source_row, purchase_date, vendor, category, stock_number, error)
    warnings: list = field(default_factory=list)   # free-text notices (e.g. repeated stock number across vehicles)


def print_summary(summary: Summary, dry_run: bool, verbose: bool) -> None:
    print()
    print("Rows read:", summary.rows_read)
    if dry_run:
        print("Would create purchases:", summary.purchases_created)
        print("Would create units:", summary.units_created)
        print("Would link existing units:", summary.units_linked)
        print("Would create vendors:", summary.vendors_created)
        print("Would link existing vendors:", summary.vendors_linked)
        print("Unmatched stock numbers:", summary.unmatched_stock_numbers)
        print("Duplicate/skipped rows:", summary.duplicate_skipped)
        print("Invalid rows:", summary.failed)
    else:
        print("Rows imported successfully:", summary.purchases_created)
        print("Rows skipped as duplicates:", summary.duplicate_skipped)
        print("Purchases created:", summary.purchases_created)
        print("Units created:", summary.units_created)
        print("Existing Units linked:", summary.units_linked)
        print("Transactions created:", summary.transactions_created)
        print("Vendors created:", summary.vendors_created)
        print("Existing Vendors reused:", summary.vendors_linked)
        print("Unmatched stock-number references:", summary.unmatched_stock_numbers)
        print("Rows failed:", summary.failed)

    print("Warning count:", len(summary.warnings))
    print("Malformed mileage converted to NULL:", summary.mileage_defaulted_to_null)
    for legacy_raw, mapped in LEGACY_CATEGORY_MAP.items():
        print(f"Mapped from legacy {legacy_raw!r} -> {mapped.value!r}:", summary.legacy_category_counts.get(legacy_raw, 0))

    if summary.warnings:
        print()
        print(f"Warnings ({len(summary.warnings)}):")
        for w in summary.warnings[:20 if not verbose else None]:
            print(" -", w)
        if not verbose and len(summary.warnings) > 20:
            print(f"   ...and {len(summary.warnings) - 20} more (use --verbose)")

    if summary.failures:
        print()
        print(f"Failures ({len(summary.failures)}):")
        for source_row, purchase_date, vendor, category, stock_number, error in summary.failures[:20 if not verbose else None]:
            print(f" - row {source_row} ({purchase_date}, {vendor}, {category}, stock={stock_number!r}): {error}")
        if not verbose and len(summary.failures) > 20:
            print(f"   ...and {len(summary.failures) - 20} more (use --verbose)")


def write_failure_csv(summary: Summary, source_csv_path: Path) -> Optional[Path]:
    if not summary.failures:
        return None
    out_path = source_csv_path.parent / "historical_import_failures.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source_row", "purchase_date", "vendor", "category", "stock_number", "error"])
        writer.writerows(summary.failures)
    return out_path


# ---------------------------------------------------------------------------
# Row processing — the only place record_purchase() is called
# ---------------------------------------------------------------------------

def _already_imported(row: ParsedRow, db: Session) -> bool:
    return (
        db.query(Purchase.id)
        .filter(Purchase.source_reference == row.source_reference)
        .first()
        is not None
    )


def _finalize(purchase: Purchase, row: ParsedRow, db: Session) -> None:
    purchase.source_reference = row.source_reference
    purchase.review_status = ReviewStatus.reviewed
    if purchase.transaction:
        purchase.transaction.review_status = ReviewStatus.reviewed
    db.flush()


def process_vehicle_row(row: ParsedRow, db: Session, stock_to_unit: dict, summary: Summary) -> None:
    vendor_count_before = db.query(Vendor.id).count()
    unit_count_before = db.query(Unit.id).count()

    purchase = record_purchase(
        purchase_date=row.purchase_date,
        purchaser=row.purchaser,
        vendor_name=row.vendor,
        amount=row.amount,
        category=row.category,
        site_location=row.site_location,
        stock_number=row.stock_number,
        mileage=row.mileage,
        description=row.notes,
        entered_by=ENTERED_BY,
        shipping_cost=row.shipping_cost,
        other_fees=row.other_fees,
        vin=row.vin,
        year=row.year,
        make=row.make,
        model=row.model,
        db=db,
    )
    _finalize(purchase, row, db)

    summary.purchases_created += 1
    summary.transactions_created += 1
    if db.query(Vendor.id).count() > vendor_count_before:
        summary.vendors_created += 1
    else:
        summary.vendors_linked += 1
    if db.query(Unit.id).count() > unit_count_before:
        summary.units_created += 1
    else:
        summary.units_linked += 1

    if row.stock_number:
        if row.stock_number in stock_to_unit:
            summary.warnings.append(
                f"stock number {row.stock_number!r} has more than one vehicle-acquisition row; "
                f"later expenses will link to the most recently processed one (row {row.source_row})"
            )
        stock_to_unit[row.stock_number] = purchase.unit


def process_non_vehicle_row(row: ParsedRow, db: Session, stock_to_unit: dict, summary: Summary) -> None:
    vendor_count_before = db.query(Vendor.id).count()

    purchase = record_purchase(
        purchase_date=row.purchase_date,
        purchaser=row.purchaser,
        vendor_name=row.vendor,
        amount=row.amount,
        category=row.category,
        site_location=row.site_location,
        stock_number=row.stock_number,
        mileage=row.mileage,
        description=row.notes,
        entered_by=ENTERED_BY,
        shipping_cost=row.shipping_cost,
        other_fees=row.other_fees,
        db=db,
    )

    # Existing match_unit_by_stock_number() only matches Unit.unit_id/vin_serial,
    # which a real-world historical stock tag will never equal. If a vehicle
    # acquired earlier in this same file used this stock number, link explicitly.
    if purchase.unit_id is None and row.stock_number and row.stock_number in stock_to_unit:
        unit = stock_to_unit[row.stock_number]
        purchase.unit_id = unit.id
        if purchase.transaction:
            purchase.transaction.unit_id = unit.id

    _finalize(purchase, row, db)

    summary.purchases_created += 1
    summary.transactions_created += 1
    if db.query(Vendor.id).count() > vendor_count_before:
        summary.vendors_created += 1
    else:
        summary.vendors_linked += 1
    if purchase.unit_id is not None:
        summary.units_linked += 1
    elif row.stock_number:
        summary.unmatched_stock_numbers += 1


def run_import(rows: list, db: Session, summary: Summary, dry_run: bool) -> None:
    """
    dry_run=True is a complete, self-contained guarantee: nothing this call
    does survives it, including the final db.rollback() below. Callers
    (main(), tests) never need to remember to roll back afterward.
    """
    try:
        _run_import(rows, db, summary, dry_run)
    finally:
        if dry_run:
            db.rollback()


def _run_import(rows: list, db: Session, summary: Summary, dry_run: bool) -> None:
    parsed = []
    for raw in rows:
        summary.rows_read += 1
        try:
            row, row_warnings, legacy_raw = parse_row(raw)
        except RowError as e:
            raw_row = raw.get("source_row", "?")
            summary.failed += 1
            summary.failures.append((
                raw_row, raw.get("purchase_date", ""), raw.get("vendor", ""),
                raw.get("category", ""), raw.get("stock_number", ""), str(e),
            ))
            continue

        for w in row_warnings:
            summary.warnings.append(f"row {row.source_row}: {w}")
            if w.startswith("unparseable mileage"):
                summary.mileage_defaulted_to_null += 1
        if legacy_raw:
            summary.legacy_category_counts[legacy_raw] = summary.legacy_category_counts.get(legacy_raw, 0) + 1
        parsed.append(row)

    vehicle_rows = [r for r in parsed if is_vehicle_purchase(r.category)]
    non_vehicle_rows = [r for r in parsed if not is_vehicle_purchase(r.category)]

    stock_to_unit = {}

    for row in vehicle_rows + non_vehicle_rows:
        is_vehicle = is_vehicle_purchase(row.category)

        if _already_imported(row, db):
            summary.duplicate_skipped += 1
            continue

        try:
            if dry_run:
                # A SAVEPOINT per row: released (committed-within-the-outer-
                # transaction) on success, rolled back to on failure — either
                # way nothing survives run_import()'s own final db.rollback().
                with db.begin_nested():
                    if is_vehicle:
                        process_vehicle_row(row, db, stock_to_unit, summary)
                    else:
                        process_non_vehicle_row(row, db, stock_to_unit, summary)
            else:
                if is_vehicle:
                    process_vehicle_row(row, db, stock_to_unit, summary)
                else:
                    process_non_vehicle_row(row, db, stock_to_unit, summary)
                db.commit()
        except Exception as e:
            if not dry_run:
                db.rollback()
            summary.failed += 1
            summary.failures.append((
                row.source_row, str(row.purchase_date), row.vendor,
                row.category.value, row.stock_number, str(e),
            ))
            continue

        if row.receipt_urls:
            summary.warnings.append(
                f"row {row.source_row}: has receipt_urls but no document import path exists yet "
                f"({row.receipt_urls}) — logged only, no Document created"
            )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="One-time historical Purchase importer. Requires --dry-run or --commit."
    )
    parser.add_argument("csv_path", type=Path, help="Path to the import-ready CSV")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report; write nothing")
    parser.add_argument("--commit", action="store_true", help="Perform the real import")
    parser.add_argument("--verbose", action="store_true", help="Show every warning/failure, not just the first 20")
    args = parser.parse_args(argv)

    if args.dry_run and args.commit:
        print("Error: pass only one of --dry-run or --commit, not both.", file=sys.stderr)
        return 2
    if not args.dry_run and not args.commit:
        print("Error: pass --dry-run or --commit explicitly. Refusing to guess.", file=sys.stderr)
        return 2
    if not args.csv_path.exists():
        print(f"Error: file not found: {args.csv_path}", file=sys.stderr)
        return 2

    rows = read_csv(args.csv_path)
    summary = Summary()
    db = SessionLocal()
    try:
        run_import(rows, db, summary, dry_run=args.dry_run)
    finally:
        db.close()

    print_summary(summary, dry_run=args.dry_run, verbose=args.verbose)

    if not args.dry_run:
        out_path = write_failure_csv(summary, args.csv_path)
        if out_path:
            print()
            print(f"Failure detail written to: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
