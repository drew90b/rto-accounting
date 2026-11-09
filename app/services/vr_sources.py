"""
Loading source records for vehicle reconciliation (spec REQ-VR-03, -07, -09).

Two kinds of source:
  * Frazer files (vehicle list, customer-balance list), parsed with the existing Frazer parser (app/services/frazer_parse.py,
    a byte-identical copy of the Frazer-intake branch's pure parser). Only the fields reconciliation needs are kept.
    NO customer name, balance, payment or phone figure is stored: the customer-balance list contributes only the fact that a
    contract exists for a stock number, its sale date and its account label.
  * The app's own units and purchases, READ ONLY. This module never writes to units, purchases, transactions or payments.

Everything is stored as an immutable, dated source record. Loading the same data again changes nothing.
"""
import hashlib
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.models.enums import UnitType
from app.models.purchase import Purchase
from app.models.unit import Unit
from app.models.vehicle_recon import (
    SOURCE_APP_PURCHASES, SOURCE_APP_UNITS, SOURCE_FRAZER_CONTRACTS, SOURCE_FRAZER_VEHICLES,
    VrSnapshot, VrSourceRecord,
)
from app.services import frazer_parse as fp
from app.services.purchase_service import purchase_total


class SourceLoadError(ValueError):
    pass


def _text(v) -> Optional[str]:
    """Identifiers and labels exactly as written (only surrounding whitespace removed). Blank -> None, never 0."""
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _money(v) -> Optional[Decimal]:
    """Blank/None -> None (not reported). '0.00' -> Decimal('0.00') (reported zero, NOT free)."""
    s = _text(v)
    if s is None:
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _date(v) -> Optional[date]:
    s = _text(v)
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def content_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


FILE_SOURCES = (SOURCE_FRAZER_VEHICLES, SOURCE_FRAZER_CONTRACTS)


def _core_hash(row: dict) -> str:
    """Content without the snapshot date, to tell 'same content, newer export' from 'content changed' for file sources."""
    p = _record_payload(row)
    p["reported_status_date"] = None
    return content_hash(p)


def _record_payload(row: dict) -> dict:
    """The stored, hashed content of a record (everything except ids and timestamps)."""
    return {
        "stock_number": row.get("stock_number"), "vin": row.get("vin"), "year": row.get("year"), "make": row.get("make"),
        "model": row.get("model"), "reported_status": row.get("reported_status"),
        "reported_status_date": str(row["reported_status_date"]) if row.get("reported_status_date") else None,
        "reported_cost": str(row["reported_cost"]) if row.get("reported_cost") is not None else None,
        "fields": row.get("fields_json") or {},
    }


def _row_of(rec: VrSourceRecord) -> dict:
    return {"stock_number": rec.stock_number, "vin": rec.vin, "year": rec.year, "make": rec.make, "model": rec.model,
            "reported_status": rec.reported_status, "reported_status_date": rec.reported_status_date,
            "reported_cost": rec.reported_cost, "fields_json": rec.fields_json}


def store_rows(db: Session, snapshot: VrSnapshot, rows: list) -> dict:
    """
    Insert rows as source records. A row byte-for-byte equal to the newest record for that key is NOT stored again. For a file
    source, a row whose content is the same but whose export is a different, newer snapshot is stored as a new dated observation
    and counted "unchanged". A changed row becomes a new record and the older one stops being 'current' but stays as history. A snapshot older than the
    current record's snapshot is stored as history only (it never replaces newer evidence).
    Returns counts: new, changed, unchanged.
    """
    counts = {"new": 0, "changed": 0, "unchanged": 0}
    current = {
        r.source_key: r for r in db.query(VrSourceRecord)
        .filter(VrSourceRecord.source_type == snapshot.source_type, VrSourceRecord.is_current.is_(True)).all()
    }
    snap_dates = {s.id: s.source_date for s in db.query(VrSnapshot).filter(VrSnapshot.source_type == snapshot.source_type).all()}
    seen = set()
    for row in rows:
        key = row["source_key"]
        if key in seen:
            raise SourceLoadError(f"Source key {key!r} appears twice in one load; nothing was stored.")
        seen.add(key)
        digest = content_hash(_record_payload(row))
        prior = current.get(key)
        if prior is not None and prior.content_hash == digest:
            counts["unchanged"] += 1
            continue
        # Same content in a different export: stored as a new dated observation (so "as of" moves forward), not a change.
        same_content = prior is not None and snapshot.source_type in FILE_SOURCES and _core_hash(row) == _core_hash(_row_of(prior))
        make_current = prior is None or snap_dates.get(prior.snapshot_id, date.min) <= snapshot.source_date
        rec = VrSourceRecord(
            snapshot_id=snapshot.id, source_type=snapshot.source_type, source_key=key,
            stock_number=row.get("stock_number"), vin=row.get("vin"), year=row.get("year"), make=row.get("make"),
            model=row.get("model"), reported_status=row.get("reported_status"),
            reported_status_date=row.get("reported_status_date"), reported_cost=row.get("reported_cost"),
            fields_json=row.get("fields_json") or {}, content_hash=digest, is_current=make_current,
        )
        if prior is not None and make_current:
            prior.is_current = False
        db.add(rec)
        counts["unchanged" if same_content else "changed" if prior is not None else "new"] += 1
    snapshot.row_count = len(rows)
    db.flush()
    return counts


# ---------------------------------------------------------------------------
# Frazer files
# ---------------------------------------------------------------------------

def vehicle_rows(field_dicts: list, source_date: date) -> list:
    """Parsed Frazer vehicle fields (the parser's own dicts) -> source rows. Shared by the file loader and the intake bridge."""
    rows = []
    for f in field_dicts:
        stock = _text(f.get("stock"))
        if stock is None:
            continue
        floor = {k: v for k, v in f.items() if k.startswith("floor_plan:")}
        rows.append({
            "source_key": stock, "stock_number": stock, "vin": _text(f.get("vin")), "year": _text(f.get("year")),
            "make": _text(f.get("make")), "model": _text(f.get("model")),
            # The Nov 8 export has no status column. We record only that the stock number was LISTED on this date.
            "reported_status": "Listed", "reported_status_date": source_date,
            "reported_cost": _money(f.get("total_cost")),
            "fields_json": {
                "vehicle_source": _text(f.get("source")), "purchase_date": _text(f.get("purchase_date")),
                "original_cost": _text(f.get("original_cost")), "total_cost": _text(f.get("total_cost")),
                "floor_plan_columns_present": bool(floor),
            },
        })
    return rows


def contract_rows(field_dicts: list, source_date: date) -> list:
    """Keeps only: stock number, VIN, year/make/model, sale date and the account label. No name, balance or payment."""
    rows = []
    for f in field_dicts:
        stock = _text(f.get("stock"))
        if stock is None:
            continue
        rows.append({
            "source_key": stock, "stock_number": stock, "vin": _text(f.get("vin")), "year": _text(f.get("year")),
            "make": _text(f.get("make")), "model": _text(f.get("model")),
            "reported_status": "Contract listed", "reported_status_date": source_date, "reported_cost": None,
            "fields_json": {"sale_date": _text(f.get("sale_date")), "account_label": _text(f.get("account_label"))},
        })
    return rows


def snapshot_for_bytes_or_hash(db, source_type, sha, label, file_name, source_date, user_id, cutoff_note):
    """Create the snapshot for a file identified by its SHA-256, or return the existing one (idempotent)."""
    existing = db.query(VrSnapshot).filter(VrSnapshot.source_type == source_type, VrSnapshot.file_sha256 == sha).first()
    if existing is not None:
        return existing, False
    snap = VrSnapshot(source_type=source_type, label=label, source_date=source_date, file_name=file_name, file_sha256=sha,
                      cutoff_note=cutoff_note or "Unknown", loaded_by_user_id=user_id)
    db.add(snap)
    db.flush()
    return snap, True


def _frazer_snapshot(db, source_type, data: bytes, file_name: str, source_date: date, user_id, cutoff_note):
    sha = hashlib.sha256(data).hexdigest()
    existing = db.query(VrSnapshot).filter(VrSnapshot.source_type == source_type, VrSnapshot.file_sha256 == sha).first()
    if existing is not None:
        return existing, False
    snap = VrSnapshot(
        source_type=source_type, label=file_name, source_date=source_date, file_name=file_name, file_sha256=sha,
        cutoff_note=cutoff_note or "Unknown", loaded_by_user_id=user_id,
    )
    db.add(snap)
    db.flush()
    return snap, True


def load_frazer_vehicles(db: Session, data: bytes, file_name: str, source_date: date, user_id: Optional[int] = None,
                         cutoff_note: str = "Unknown") -> dict:
    """The same bytes loaded twice are a no-op. Rows the parser flags as errors stop the load (nothing is guessed)."""
    pf = fp.parse_export(data, fp.VEHICLES)
    if pf.status != "parsed" or pf.export_type != fp.VEHICLES:
        raise SourceLoadError("That file is not a recognised Frazer vehicle list; nothing was loaded.")
    bad = [e for r in pf.records for e in r.exceptions if e.severity == fp.ERROR] + [e for e in pf.file_exceptions if e.severity == fp.ERROR]
    if bad:
        raise SourceLoadError(f"The vehicle list has {len(bad)} unreadable row(s) (first: {bad[0].message}); nothing was loaded.")
    snap, created = _frazer_snapshot(db, SOURCE_FRAZER_VEHICLES, data, file_name, source_date, user_id, cutoff_note)
    if not created:
        return {"snapshot": snap, "created": False, "new": 0, "changed": 0, "unchanged": snap.row_count}
    rows = vehicle_rows([r.fields for r in pf.records], source_date)
    counts = store_rows(db, snap, rows)
    return {"snapshot": snap, "created": True, **counts}


def load_frazer_contracts(db: Session, data: bytes, file_name: str, source_date: date, user_id: Optional[int] = None,
                          cutoff_note: str = "Unknown") -> dict:
    """Keeps only: stock number, VIN, year/make/model, sale date and the account label. No name, balance or payment."""
    pf = fp.parse_export(data, fp.CUSTOMERS)
    if pf.status != "parsed" or pf.export_type != fp.CUSTOMERS:
        raise SourceLoadError("That file is not a recognised Frazer customer-balance list; nothing was loaded.")
    bad = [e for r in pf.records for e in r.exceptions if e.severity == fp.ERROR] + [e for e in pf.file_exceptions if e.severity == fp.ERROR]
    if bad:
        raise SourceLoadError(f"The balance list has {len(bad)} unreadable row(s); nothing was loaded.")
    snap, created = _frazer_snapshot(db, SOURCE_FRAZER_CONTRACTS, data, file_name, source_date, user_id, cutoff_note)
    if not created:
        return {"snapshot": snap, "created": False, "new": 0, "changed": 0, "unchanged": snap.row_count}
    rows = contract_rows([r.fields for r in pf.records], source_date)
    counts = store_rows(db, snap, rows)
    return {"snapshot": snap, "created": True, **counts}


# ---------------------------------------------------------------------------
# The app's own records (read only)
# ---------------------------------------------------------------------------

def _observe(db: Session, source_type: str, rows: list, today: date, label: str) -> dict:
    """Store a snapshot only when something changed since the newest records, so repeated refreshes add nothing."""
    current = {
        r.source_key: r.content_hash for r in db.query(VrSourceRecord)
        .filter(VrSourceRecord.source_type == source_type, VrSourceRecord.is_current.is_(True)).all()
    }
    have_snapshot = db.query(VrSnapshot).filter(VrSnapshot.source_type == source_type).first() is not None
    changed = [r for r in rows if current.get(r["source_key"]) != content_hash(_record_payload(r))]
    if have_snapshot and not changed:
        return {"snapshot": None, "created": False, "new": 0, "changed": 0, "unchanged": len(rows)}
    snap = VrSnapshot(source_type=source_type, label=label, source_date=today, cutoff_note=f"Read from the app on {today}")
    db.add(snap)
    db.flush()
    counts = store_rows(db, snap, rows)
    return {"snapshot": snap, "created": True, **counts}


def observe_app_units(db: Session, today: date) -> dict:
    rows = []
    for u in db.query(Unit).filter(Unit.unit_type == UnitType.car).order_by(Unit.id).all():
        rows.append({
            "source_key": str(u.id), "stock_number": None, "vin": _text(u.vin_serial), "year": _text(u.year),
            "make": _text(u.make), "model": _text(u.model),
            "reported_status": u.status.value if u.status else None,
            "reported_status_date": u.updated_at.date() if u.updated_at else None,
            "reported_cost": u.acquisition_cost,
            "fields_json": {"unit_ref": u.unit_id, "unit_pk": u.id},
        })
    return _observe(db, SOURCE_APP_UNITS, rows, today, "App units (cars)")


def observe_app_purchases(db: Session, today: date) -> dict:
    """Purchases that name a stock number or are linked to a unit. Read only: the purchase and its transaction are untouched."""
    rows = []
    q = db.query(Purchase).order_by(Purchase.id)
    for p in q.all():
        stock = _text(p.stock_number)
        if stock is None and p.unit_id is None:
            continue
        unit = p.unit
        rows.append({
            "source_key": str(p.id), "stock_number": stock, "vin": _text(unit.vin_serial) if unit else None,
            "year": None, "make": None, "model": None,
            "reported_status": p.review_status.value if p.review_status else None, "reported_status_date": None,
            "reported_cost": purchase_total(p),
            "fields_json": {
                "purchase_ref": p.purchase_id, "purchase_pk": p.id, "purchase_date": str(p.purchase_date),
                "category": p.category.value if p.category else None,
                "unit_pk": p.unit_id, "unit_ref": unit.unit_id if unit else None,
                "unit_vin": _text(unit.vin_serial) if unit else None,
            },
        })
    return _observe(db, SOURCE_APP_PURCHASES, rows, today, "App purchases (with a stock number or unit)")


def has_source(db: Session, source_type: str) -> bool:
    return db.query(VrSnapshot.id).filter(VrSnapshot.source_type == source_type).first() is not None
