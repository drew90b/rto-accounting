"""
Frazer source intake: store an uploaded export, keep what was read from it, and preview what a later
import would do. SOURCE EVIDENCE ONLY (docs/specs/cash-financial-control-spec.md, section 19).

Nothing in this module reads or writes a purchase, unit, payment, transaction, customer, vendor,
document or cash movement. It touches only the four frazer_* tables and its own folder under
STORAGE_DIR. Callers commit; store_upload() writes the original file BEFORE the caller commits, so a
route must call discard_file() if its commit fails.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import os
import re
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.config import STORAGE_DIR
from app.models.frazer_intake import FrazerSourceRow, FrazerUpload, FrazerUploadException, FrazerUploadNote
from app.services import frazer_parse as fp

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
TYPE_LABEL = {fp.VEHICLES: "Vehicle list", fp.CUSTOMERS: "Customer balances", fp.PAYMENTS: "Transactions",
              fp.UNRECOGNIZED: "Not recognised"}
SNAPSHOT_TYPES = (fp.VEHICLES, fp.CUSTOMERS)
STATUSES = ("new", "unchanged", "changed", "conflicting", "unresolved")
KEY_FIELDS = {fp.VEHICLES: ("vin",), fp.CUSTOMERS: ("vin",), fp.PAYMENTS: ("transaction_date", "stock")}


class IntakeError(ValueError):
    """Bad input; the message is safe to show the user."""


class DuplicateUpload(IntakeError):
    def __init__(self, existing: FrazerUpload):
        self.existing = existing
        super().__init__(f"This exact file was already uploaded as {existing.upload_id}.")


def storage_root() -> str:
    # Looked up at call time so tests can point it at a temporary folder.
    return os.path.join(STORAGE_DIR, "frazer_intake")


def _iso(value: str, label: str) -> Optional[date]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise IntakeError(f"{label} must look like 2026-11-08.")


# ---------------------------------------------------------------------------
# Storing an upload
# ---------------------------------------------------------------------------

def store_upload(db: Session, user, *, file, requested_type: str = "", export_date: str = "",
                 coverage_start: str = "", coverage_end: str = "", actual_cutoff: str = "") -> FrazerUpload:
    if file is None or not getattr(file, "filename", ""):
        raise IntakeError("Choose a file to upload.")
    original = re.sub(r"[\x00-\x1f\x7f]", "", os.path.basename(file.filename.replace("\\", "/"))).strip()[:255] or "upload.csv"
    if not original.lower().endswith(".csv"):
        raise IntakeError("Only .csv files are accepted (a Frazer export may end in .CSV or .csv.CSV).")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise IntakeError("That file is larger than 10 MB.")
    if not data.strip():
        raise IntakeError("That file is empty.")
    requested = (requested_type or "").strip()
    if requested and requested not in fp.EXPORT_TYPES:
        raise IntakeError("Choose an export type from the list, or leave it on automatic detection.")
    declared = fp.Declared(_iso(export_date, "Export date"), _iso(coverage_start, "Coverage start"),
                           _iso(coverage_end, "Coverage end"))
    if declared.coverage_start and declared.coverage_end and declared.coverage_start > declared.coverage_end:
        raise IntakeError("Coverage start is after coverage end.")

    declared = fp.declared_from_filename(original, declared)
    sha = hashlib.sha256(data).hexdigest()
    existing = db.query(FrazerUpload).filter(FrazerUpload.sha256 == sha).order_by(FrazerUpload.id).first()
    if existing is not None:
        raise DuplicateUpload(existing)

    parsed = fp.parse_export(data, requested or None, declared)

    os.makedirs(storage_root(), exist_ok=True)
    dest = os.path.join(storage_root(), f"{uuid.uuid4().hex}.csv")
    with open(dest, "wb") as out:
        out.write(data)
    try:
        up = FrazerUpload(
            export_type=parsed.export_type, layout=parsed.layout, parser_version=fp.PARSER_VERSION,
            original_filename=original, stored_path=dest, size_bytes=len(data), sha256=sha, encoding=parsed.encoding,
            status=parsed.status,
            declared_export_date=declared.export_date, declared_coverage_start=declared.coverage_start,
            declared_coverage_end=declared.coverage_end,
            observed_coverage_start=parsed.observed_start, observed_coverage_end=parsed.observed_end,
            actual_cutoff=(actual_cutoff or "").strip() or "Unknown",
            row_count=len(parsed.records), headers_json=parsed.headers,
            summary_json={**parsed.summary, "declared_from_filename": list(declared.from_filename)},
            uploaded_by_user_id=user.id,
        )
        db.add(up)
        db.flush()
        up.upload_id = f"FU-{up.id:04d}"
        for r in parsed.records:
            db.add(FrazerSourceRow(upload_id=up.id, row_number=r.row_number, record_type=r.record_type,
                                   identity=r.identity, content_hash=r.content_hash, entry_class=r.entry_class,
                                   raw_json=r.raw, fields_json=r.fields))
        for e in parsed.all_exceptions:
            db.add(FrazerUploadException(upload_id=up.id, row_number=e.row, identity=e.identity, code=e.code,
                                         severity=e.severity, field=e.field, message=e.message))
        db.flush()
    except Exception:
        discard_file(dest)
        raise
    return up


def discard_file(path: str) -> None:
    """Remove a file written by store_upload() whose transaction did not commit."""
    try:
        os.remove(path)
    except OSError:
        pass


def stored_file_path(up: FrazerUpload) -> Optional[str]:
    """The stored path, only if it really is inside the intake folder."""
    root = os.path.realpath(storage_root())
    path = os.path.realpath(up.stored_path)
    try:
        inside = os.path.commonpath([root, path]) == root
    except ValueError:
        inside = False
    return path if inside and os.path.isfile(path) else None


def add_note(db: Session, up: FrazerUpload, user, *, note: str, actual_cutoff: str = "") -> FrazerUploadNote:
    note = (note or "").strip()
    if not note:
        raise IntakeError("Write what you learned.")
    n = FrazerUploadNote(upload_id=up.id, note=note, actual_cutoff=(actual_cutoff or "").strip() or None,
                         created_by_user_id=user.id)
    db.add(n)
    db.flush()
    return n


def current_cutoff(up: FrazerUpload) -> dict:
    """The cutoff as originally recorded, and the latest note that states one (the original is never changed)."""
    stated = [n for n in up.notes if n.actual_cutoff]
    return {"original": up.actual_cutoff, "latest": stated[-1] if stated else None,
            "known": up.actual_cutoff.strip().lower() != "unknown" or bool(stated)}


# ---------------------------------------------------------------------------
# Listing and exceptions
# ---------------------------------------------------------------------------

def list_uploads(db: Session) -> list:
    return db.query(FrazerUpload).order_by(FrazerUpload.id.desc()).all()


def grouped_exceptions(db: Session, up: FrazerUpload, code: Optional[str] = None) -> list:
    q = db.query(FrazerUploadException).filter(FrazerUploadException.upload_id == up.id)
    if code:
        q = q.filter(FrazerUploadException.code == code)
    groups = defaultdict(list)
    for e in q.order_by(FrazerUploadException.row_number, FrazerUploadException.id).all():
        groups[(e.severity, e.code)].append(e)
    order = {fp.ERROR: 0, fp.WARNING: 1, fp.INFO: 2}
    out = []
    for (sev, c), es in groups.items():
        lines = [e.row_number for e in es if e.row_number]
        out.append({"severity": sev, "code": c, "count": len(es), "lines": lines, "message": es[0].message,
                    "file_level": any(e.row_number is None for e in es), "items": es})
    out.sort(key=lambda g: (order.get(g["severity"], 9), -g["count"], g["code"]))
    return out


# ---------------------------------------------------------------------------
# Baseline and the preview
# ---------------------------------------------------------------------------

def _snap_key(u: FrazerUpload):
    return (u.declared_export_date or u.uploaded_at.date(), u.id)


def pick_baseline(db: Session, up: FrazerUpload):
    """Snapshots: the previous snapshot of the same type by export date (one upload or None).
    Transactions: every earlier transaction upload (a list, possibly empty)."""
    if up.status != "parsed":
        return None
    others = db.query(FrazerUpload).filter(FrazerUpload.export_type == up.export_type,
                                           FrazerUpload.status == "parsed", FrazerUpload.id != up.id).all()
    if up.export_type == fp.PAYMENTS:
        return sorted([u for u in others if u.id < up.id], key=lambda u: u.id)
    earlier = [u for u in others if _snap_key(u) < _snap_key(up)]
    return max(earlier, key=_snap_key) if earlier else None


@dataclass
class Classified:
    rows: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    with_notes: int = 0
    has_baseline: bool = False
    baseline_text: str = ""
    baseline_uploads: list = field(default_factory=list)
    removed_identities: list = field(default_factory=list)
    overlap_identities: int = 0
    is_snapshot: bool = False


def classify(db: Session, up: FrazerUpload) -> Classified:
    out = Classified(is_snapshot=up.export_type in SNAPSHOT_TYPES)
    if up.status != "parsed":
        return out
    rows = db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == up.id).order_by(FrazerSourceRow.row_number).all()
    excs = defaultdict(list)
    for e in db.query(FrazerUploadException).filter(FrazerUploadException.upload_id == up.id,
                                                    FrazerUploadException.row_number.isnot(None)).all():
        excs[e.row_number].append(e)

    baseline = pick_baseline(db, up)
    base_rows = {}
    if up.export_type == fp.PAYMENTS:
        out.baseline_uploads = list(baseline or [])
        for b in out.baseline_uploads:                      # later uploads overwrite earlier in this map ONLY for comparison
            for r in db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == b.id).order_by(FrazerSourceRow.row_number).all():
                if r.identity:
                    base_rows[r.identity] = r
        out.has_baseline = bool(out.baseline_uploads)
        if out.has_baseline:
            out.baseline_text = "Compared with {} earlier transaction upload{} ({}) by Sequential ID.".format(
                len(out.baseline_uploads), "" if len(out.baseline_uploads) == 1 else "s",
                ", ".join(b.upload_id for b in out.baseline_uploads))
    else:
        if baseline is not None:
            out.baseline_uploads = [baseline]
            out.has_baseline = True
            for r in db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == baseline.id).order_by(FrazerSourceRow.row_number).all():
                if r.identity:
                    base_rows[r.identity] = r
            when = baseline.declared_export_date.isoformat() if baseline.declared_export_date else "an unstated date"
            out.baseline_text = (f"Compared with the previous snapshot {baseline.upload_id} (exported {when}). "
                                 "This is a comparison only: a different snapshot is not a correction of this one.")

    key_fields = KEY_FIELDS.get(up.export_type, ())
    seen_identities = set()
    for r in rows:
        es = excs.get(r.row_number, [])
        codes = {e.code for e in es}
        hard = [e for e in es if e.severity == fp.ERROR and e.code not in fp.CONFLICT_CODES]
        diffs, note = [], None
        if hard:
            status = "unresolved"
        elif codes & fp.CONFLICT_CODES:
            status = "conflicting"
        else:
            b = base_rows.get(r.identity) if r.identity else None
            if not out.has_baseline or b is None:
                status = "new"
            elif b.content_hash == r.content_hash:
                status = "unchanged"
            else:
                diffs = _diffs(b.fields_json, r.fields_json)
                bad = [k for k in key_fields if _norm(b.fields_json.get(k)) and _norm(r.fields_json.get(k))
                       and _norm(b.fields_json.get(k)) != _norm(r.fields_json.get(k))]
                status = "conflicting" if bad else "changed"
                if bad:
                    codes.add("baseline_key_conflict")
                    note = "Key field(s) disagree with the earlier version: " + ", ".join(bad)
        if r.identity:
            seen_identities.add(r.identity)
        has_notes = any(e.severity == fp.WARNING for e in es)
        out.rows.append({"row_number": r.row_number, "identity": r.identity, "status": status, "entry_class": r.entry_class,
                         "codes": sorted(codes), "diffs": diffs, "note": note, "has_notes": has_notes,
                         "fields": r.fields_json, "raw": r.raw_json,
                         "exceptions": [(e.severity, e.code, e.message) for e in es]})
        out.counts[status] += 1
        out.with_notes += 1 if has_notes else 0
    if up.export_type in SNAPSHOT_TYPES and out.has_baseline:
        out.removed_identities = sorted(set(base_rows) - seen_identities)
    if up.export_type == fp.PAYMENTS and out.has_baseline:
        out.overlap_identities = len(seen_identities & set(base_rows))
    return out


def _norm(v) -> str:
    return str(v or "").strip().upper()


def _diffs(old: dict, new: dict) -> list:
    keys = sorted(set(old) | set(new))
    return [(k, old.get(k), new.get(k)) for k in keys if old.get(k) != new.get(k)]


# ---------------------------------------------------------------------------
# Cross-file checks (computed, never stored; nothing is corrected)
# ---------------------------------------------------------------------------

def counterpart(db: Session, up: FrazerUpload, other_type: str):
    cands = db.query(FrazerUpload).filter(FrazerUpload.export_type == other_type, FrazerUpload.status == "parsed",
                                          FrazerUpload.id != up.id).all()
    if not cands:
        return None
    ref = up.declared_export_date or up.uploaded_at.date()

    def dist(u):
        d = u.declared_export_date or u.uploaded_at.date()
        return (abs((d - ref).days), -u.id)
    return min(cands, key=dist)


def cross_checks(db: Session, up: FrazerUpload) -> Optional[dict]:
    if up.status != "parsed":
        return None
    if up.export_type in (fp.CUSTOMERS, fp.PAYMENTS):
        other = counterpart(db, up, fp.VEHICLES)
        mine, theirs = up, other
        label = "vehicle list"
    elif up.export_type == fp.VEHICLES:
        other = counterpart(db, up, fp.CUSTOMERS)
        mine, theirs = up, other
        label = "customer-balance snapshot"
    else:
        return None
    if other is None:
        return {"available": False, "text": f"No {label} has been uploaded yet, so cross-file checks were not run."}
    days = None
    if up.declared_export_date and other.declared_export_date:
        days = abs((up.declared_export_date - other.declared_export_date).days)
    res = {"available": True, "other": other, "label": label, "days_apart": days,
           "caveat": (None if days == 0 else
                      "These two uploads have different export dates; stock and VIN differences can be real changes between the snapshots."
                      if days else "An export date was not declared on one of the uploads, so their dates cannot be compared.")}
    my_rows = db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == up.id).order_by(FrazerSourceRow.row_number).all()
    th_rows = db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == other.id).order_by(FrazerSourceRow.row_number).all()

    if up.export_type == fp.VEHICLES:
        # which of this vehicle list's stock numbers appear in the customer-balance snapshot, and do the VINs agree
        cust = defaultdict(list)
        for r in th_rows:
            if r.identity:
                cust[r.identity].append(r)
        veh = {r.identity for r in my_rows if r.identity}
        matched = [r for r in my_rows if r.identity in cust]
        differ = [r.row_number for r in matched for c in cust[r.identity]
                  if _norm(r.fields_json.get("vin")) and _norm(c.fields_json.get("vin")) and _norm(r.fields_json.get("vin")) != _norm(c.fields_json.get("vin"))]
        res.update(vehicles_with_a_balance_record=len(matched), vehicles_total=len(my_rows),
                   balance_records_not_in_vehicle_list=sorted({s for s in cust if s not in veh}),
                   vin_differs_rows=differ)
        return res

    vmap = defaultdict(list)
    for r in th_rows:
        if r.identity:
            vmap[r.identity].append(r)
    total = found = vin_ok = vin_differ = vin_unchecked = 0
    missing, differ = [], []
    ambiguous = 0
    for r in my_rows:
        # a payments row is identified by its Sequential ID; the link to a vehicle is its Stock column
        stock = r.fields_json.get("stock") if up.export_type == fp.PAYMENTS else r.identity
        if not stock:
            continue
        total += 1
        hits = vmap.get(stock)
        if not hits:
            missing.append(r.row_number)
            continue
        found += 1
        if len(hits) > 1:
            ambiguous += 1
        mine_vin, their_vin = _norm(r.fields_json.get("vin")), _norm(hits[0].fields_json.get("vin"))
        if not mine_vin or not their_vin:
            vin_unchecked += 1
        elif mine_vin == their_vin:
            vin_ok += 1
        else:
            vin_differ += 1
            differ.append(r.row_number)
    if up.export_type == fp.PAYMENTS:
        res["down_payments"] = _down_payment_check(db, up, my_rows)
    res.update(records_total=total, stock_found=found, stock_missing=len(missing), stock_missing_rows=missing,
               vin_agree=vin_ok, vin_differ=vin_differ, vin_differ_rows=differ, vin_unchecked=vin_unchecked,
               ambiguous_vehicle_stock=ambiguous)
    return res


def _down_payment_check(db: Session, up: FrazerUpload, my_rows) -> Optional[dict]:
    """Do 'Down Payment' entries agree with the Down Payment column of the nearest customer-balance snapshot?
    Corroboration only: a snapshot taken later can differ for real reasons, and nothing is corrected."""
    cust = counterpart(db, up, fp.CUSTOMERS)
    rows = [r for r in my_rows if (r.fields_json.get("description") or "").strip().casefold() == "down payment"]
    if cust is None or not rows:
        return None
    by_stock = {r.identity: r for r in db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == cust.id)}
    agree, differ, absent = 0, [], []
    for r in rows:
        c = by_stock.get(r.fields_json.get("stock"))
        if c is None:
            absent.append(r.row_number)
        elif _same_amount(r.fields_json.get("amount"), c.fields_json.get("down_payment")):
            agree += 1
        else:
            differ.append(r.row_number)
    return {"other": cust, "total": len(rows), "agree": agree, "differ_rows": differ, "no_balance_record_rows": absent}


def _same_amount(a, b) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, TypeError):
        return False


# ---------------------------------------------------------------------------
# What is ready, in words
# ---------------------------------------------------------------------------

def readiness(up: FrazerUpload, c: Classified, cross: Optional[dict]) -> dict:
    if up.status != "parsed":
        return {"headline": "Rejected: nothing was read from this file.", "ready": 0, "attention": 0, "lines": []}
    n = up.row_count
    ready = c.counts["new"] + c.counts["unchanged"]
    attention = c.counts["changed"] + c.counts["conflicting"] + c.counts["unresolved"]
    lines = []
    if not c.has_baseline:
        lines.append("There is no earlier upload of this type to compare with, so every record is shown as new. "
                     "That means \"not seen before\", not \"verified\".")
    if c.counts["unresolved"]:
        lines.append(f"{c.counts['unresolved']} record(s) cannot be used as they stand (see the errors). They need a corrected export or a decision.")
    if c.counts["conflicting"]:
        lines.append(f"{c.counts['conflicting']} record(s) conflict: the same identity appears with different content, or a key identifier disagrees with an earlier upload.")
    if c.counts["changed"]:
        lines.append(f"{c.counts['changed']} record(s) changed since the earlier upload. Both versions are kept; this is a revision, not a replacement.")
    if c.with_notes:
        lines.append(f"{c.with_notes} record(s) carry warnings worth a look.")
    if cross and cross.get("available"):
        bad = cross.get("vin_differ", 0) + cross.get("stock_missing", 0) + len(cross.get("vin_differs_rows", []))
        if bad:
            lines.append(f"Cross-file check against {cross['other'].upload_id}: {bad} stock/VIN difference(s) to look at.")
    lines.append("Nothing from this file has been imported. It is source evidence only, and no purchase, unit, payment or "
                 "cash record was created or changed.")
    head = (f"{ready} of {n} record(s) parsed without errors and would be candidates for a later import; "
            f"{attention} need attention." if n else "No records were read.")
    return {"headline": head, "ready": ready, "attention": attention, "lines": lines}
