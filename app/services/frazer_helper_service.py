"""
Server side of the Frazer export helper (spec docs/specs/frazer-export-helper-spec.md, REQ-FX-*).

A person starts the helper on the Frazer computer; the helper sends the three exports here. Every file goes through the EXISTING
intake service (frazer_intake_service.store_upload: size, type, hash, duplicate and revision handling, original file kept) and the
EXISTING reconciliation synchronisation (vr_intake_bridge). This module writes only its own three tables plus whatever those services
write. It never edits an upload, resolves a question, approves a match, changes a source record or posts a financial entry.

Per-file outcomes (a "good" outcome counts toward a complete run):
  stored       good  read and stored as a new upload
  duplicate    good  byte-identical to an earlier upload (intake refuses those); nothing new is stored
  carried      good  taken from the earlier run this one retries (same business day), not sent again
  stored_with_errors   stored as evidence, but unreadable rows mean reconciliation will not use it -> run is partial
  rejected / stale / incomplete / layout / failed / missing   not stored
The three exports are taken at different moments; each result keeps its own file-written time. Nothing here says they are one snapshot.
"""
import hashlib
import io
import secrets
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.frazer_helper import FrazerHelperLayout, FrazerHelperRun, FrazerHelperToken
from app.models.frazer_intake import FrazerUpload
from app.services import frazer_intake_service as intake
from app.services import frazer_parse as fp
from app.services import vr_intake_bridge as bridge

EXPORT_TYPES = (fp.VEHICLES, fp.CUSTOMERS, fp.PAYMENTS)
GOOD = ("stored", "duplicate", "carried")
LEASE_MINUTES = 45                      # a run that has been silent this long is treated as abandoned and no longer blocks a new one
CLOCK_SKEW = timedelta(minutes=3)       # allowed difference between the helper computer's clock and the server's
STALE_AFTER_HOURS = 30                  # data older than this is shown as stale on the intake and helper pages
CHICAGO, UTC = ZoneInfo("America/Chicago"), ZoneInfo("UTC")
TOKEN_PREFIX = "fxh_"


class HelperError(Exception):
    status_code = 400


class Unauthorized(HelperError):
    status_code = 401


class ActiveRun(HelperError):
    status_code = 409

    def __init__(self, run: FrazerHelperRun):
        self.run = run
        super().__init__(f"Run {run.run_id} is already in progress (started {run.started_at:%H:%M} UTC). Only one run at a time.")


class BadRun(HelperError):
    status_code = 409


def _now() -> datetime:
    return datetime.utcnow()


def business_date(now: datetime) -> date:
    return now.replace(tzinfo=UTC).astimezone(CHICAGO).date()


# --- tokens -------------------------------------------------------------------------------------------------------------

def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_token(db: Session, admin, *, label: str, user, days_valid: Optional[int] = 90) -> tuple:
    """-> (token row, secret). The secret is returned once and only its hash is kept."""
    label = (label or "").strip()
    if not label:
        raise HelperError("Give the token a label, such as where the helper is installed.")
    if not admin.is_admin:
        raise HelperError("Only an administrator can create a helper token.")
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    t = FrazerHelperToken(label=label[:80], token_hash=_hash(raw), token_prefix=raw[:8], user_id=user.id, created_by_user_id=admin.id,
                          expires_at=_now() + timedelta(days=days_valid) if days_valid else None)
    db.add(t)
    db.flush()
    return t, raw


def revoke_token(db: Session, token_id: int) -> FrazerHelperToken:
    t = db.get(FrazerHelperToken, token_id)
    if t is None:
        raise HelperError("No such token.")
    if t.revoked_at is None:
        t.revoked_at = _now()
        # a revoked token cannot keep a run open
        for r in db.query(FrazerHelperRun).filter(FrazerHelperRun.token_id == t.id, FrazerHelperRun.status == "running"):
            _close(db, r, "abandoned", "The token was revoked while this run was open.")
    db.flush()
    return t


def authenticate(db: Session, raw: Optional[str]) -> FrazerHelperToken:
    if not raw or not raw.startswith(TOKEN_PREFIX):
        raise Unauthorized("Missing or invalid helper token.")
    t = db.query(FrazerHelperToken).filter(FrazerHelperToken.token_hash == _hash(raw)).first()
    if t is None or t.revoked_at is not None or (t.expires_at and t.expires_at < _now()) or not t.user.active:
        raise Unauthorized("Missing or invalid helper token.")
    t.last_used_at = _now()
    return t


# --- layouts ------------------------------------------------------------------------------------------------------------

def _norm_headers(headers) -> list:
    return [fp._norm(h) for h in headers or []]


def set_layout(db: Session, admin, upload_pk: int) -> FrazerHelperLayout:
    """Approve an existing, parsed upload's header row as the expected layout of its export type."""
    up = db.get(FrazerUpload, upload_pk)
    if up is None or up.status != "parsed" or up.export_type not in EXPORT_TYPES:
        raise HelperError("Choose a parsed vehicle, customer-balance or transaction upload.")
    if not admin.is_admin:
        raise HelperError("Only an administrator can approve a layout.")
    row = db.get(FrazerHelperLayout, up.export_type)
    if row is None:
        row = FrazerHelperLayout(export_type=up.export_type, headers_json=list(up.headers_json or []), source_upload_id=up.id, set_by_user_id=admin.id)
        db.add(row)
    else:
        row.headers_json, row.source_upload_id, row.set_by_user_id, row.set_at = list(up.headers_json or []), up.id, admin.id, _now()
    db.flush()
    return row


def layouts(db: Session) -> dict:
    return {r.export_type: r for r in db.query(FrazerHelperLayout).all()}


# --- runs ---------------------------------------------------------------------------------------------------------------

def _close(db: Session, run: FrazerHelperRun, status: str, summary: str):
    run.status, run.finished_at, run.summary = status, _now(), summary
    db.flush()


def start_run(db: Session, token: FrazerHelperToken, *, params: Optional[dict] = None, retry_of: Optional[str] = None) -> FrazerHelperRun:
    now = _now()
    for r in db.query(FrazerHelperRun).filter(FrazerHelperRun.status == "running").all():
        if r.lease_expires_at > now:
            raise ActiveRun(r)
        _close(db, r, "abandoned", "No activity for %d minutes; closed so a new run can start." % LEASE_MINUTES)
    missing = [t for t in EXPORT_TYPES if db.get(FrazerHelperLayout, t) is None]
    if missing:
        raise HelperError("An administrator must approve the expected column layout first (no layout for: " + ", ".join(missing) + "). "
                          "Do this on the Helper page, from an upload that was read correctly.")
    run = FrazerHelperRun(token_id=token.id, status="running", started_at=now, lease_expires_at=now + timedelta(minutes=LEASE_MINUTES),
                          business_date=business_date(now).isoformat(), params_json=params or {}, results_json={})
    if retry_of:
        prev = db.query(FrazerHelperRun).filter(FrazerHelperRun.run_id == retry_of).first()
        if prev is None or prev.status not in ("partial", "failed", "abandoned") or prev.token_id != token.id:
            raise BadRun("That run cannot be retried (it must be this token's earlier partial, failed or abandoned run).")
        if prev.business_date != run.business_date:
            raise BadRun("An earlier day's run cannot be carried forward. Start a fresh run.")
        run.retry_of_run_id = prev.id
        for t, res in (prev.results_json or {}).items():
            if res.get("outcome") in GOOD:
                run.results_json = {**run.results_json, t: {**res, "outcome": "carried", "carried_from": prev.run_id}}
    db.add(run)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        other = db.query(FrazerHelperRun).filter(FrazerHelperRun.status == "running").first()
        raise ActiveRun(other) if other else BadRun("Another run started at the same moment.")
    run.run_id = f"FR-{run.id:04d}"
    db.flush()
    return run


def get_run(db: Session, token: FrazerHelperToken, run_id: str) -> FrazerHelperRun:
    run = db.query(FrazerHelperRun).filter(FrazerHelperRun.run_id == run_id).first()
    if run is None or run.token_id != token.id:
        raise BadRun("No such run for this token.")
    return run


def _set_result(run: FrazerHelperRun, export_type: str, result: dict):
    run.results_json = {**(run.results_json or {}), export_type: result}      # reassign: JSON columns do not see in-place edits
    run.lease_expires_at = _now() + timedelta(minutes=LEASE_MINUTES)


def _parse_iso(value: str, label: str) -> datetime:
    try:
        d = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        raise HelperError(f"{label} must be an ISO date-time such as 2026-11-09T22:41:00Z.")
    return d.astimezone(UTC).replace(tzinfo=None) if d.tzinfo else d


class _Upload:
    """The little file-like object intake.store_upload reads."""
    def __init__(self, name: str, data: bytes):
        self.filename, self.file = name, io.BytesIO(data)


def submit_file(db: Session, token: FrazerHelperToken, run: FrazerHelperRun, *, export_type: str, filename: str, data: bytes,
                sha256: str, generated_at: str, export_date: str, coverage_start: str = "", coverage_end: str = "") -> dict:
    """Validate and store one file. Always returns a result dict (never raises for a bad FILE); a bad request raises HelperError."""
    if run.status != "running":
        raise BadRun(f"Run {run.run_id} is {run.status}; start a new run (or retry it).")
    if export_type not in EXPORT_TYPES:
        raise HelperError("export_type must be one of: " + ", ".join(EXPORT_TYPES))
    now = _now()
    base = {"export_type": export_type, "filename": filename, "size_bytes": len(data), "received_at": now.isoformat(timespec="seconds"),
            "export_date": export_date, "coverage_start": coverage_start or None, "coverage_end": coverage_end or None}

    def refuse(outcome: str, message: str) -> dict:
        res = {**base, "outcome": outcome, "message": message, "upload": None}
        _set_result(run, export_type, res)
        db.flush()
        return res

    try:
        written = _parse_iso(generated_at, "generated_at")
    except HelperError as e:
        return refuse("stale", str(e))
    base["file_written_at"] = written.isoformat(timespec="seconds")
    # 1 the transfer: what arrived is what was read from the disk
    if not data.strip():
        return refuse("incomplete", "The file is empty.")
    if hashlib.sha256(data).hexdigest() != (sha256 or "").lower():
        return refuse("incomplete", "The file that arrived does not match the file the helper read (damaged or still being written). Nothing was stored.")
    # 2 freshness: written during this run, on this business day. Yesterday's file is never taken as today's snapshot.
    started = run.started_at
    if written < started - CLOCK_SKEW:
        return refuse("stale", f"The file was written {written:%Y-%m-%d %H:%M} UTC, before this run started ({started:%Y-%m-%d %H:%M} UTC). "
                               "It is an older export; generate a fresh one.")
    if written > now + CLOCK_SKEW:
        return refuse("stale", "The file's time is in the future; check the clock of the computer running the helper.")
    if export_date != run.business_date:
        return refuse("stale", f"The export is declared for {export_date or 'no date'}, but this run's business date is {run.business_date}.")
    if export_type == fp.PAYMENTS:
        try:
            cs, ce = date.fromisoformat(coverage_start), date.fromisoformat(coverage_end)
        except ValueError:
            return refuse("stale", "A transaction export needs its coverage start and end dates.")
        if cs > ce or ce.isoformat() != run.business_date:
            return refuse("stale", f"The transaction period {coverage_start} to {coverage_end} must end on the run's date ({run.business_date}).")
    # 3 the layout: an approved header row, in order
    declared = fp.Declared(date.fromisoformat(export_date), date.fromisoformat(coverage_start) if coverage_start else None,
                           date.fromisoformat(coverage_end) if coverage_end else None)
    lay = db.get(FrazerHelperLayout, export_type)
    parsed = fp.parse_export(data, export_type, declared)
    if parsed.status != "parsed":
        why = "; ".join(e.message for e in parsed.file_exceptions[:2]) or "unreadable"
        return refuse("rejected", f"Not a {export_type.replace('_', ' ')} export: {why}")
    if lay is None:
        return refuse("layout", "No approved column layout for this export type yet.")
    if _norm_headers(parsed.headers) != _norm_headers(lay.headers_json):
        have, want = _norm_headers(parsed.headers), _norm_headers(lay.headers_json)
        added, removed = [h for h in have if h not in want], [h for h in want if h not in have]
        detail = (f" New columns: {added[:5]}." if added else "") + (f" Missing columns: {removed[:5]}." if removed else "") + ("" if added or removed else " Same columns, different order.")
        return refuse("layout", "The column headings differ from the approved layout of this saved export." + detail + " The saved preset in Frazer may have been edited.")
    # 4 the existing intake service
    try:
        up = intake.store_upload(db, token.user, file=_Upload(filename, data), requested_type=export_type, export_date=export_date,
                                 coverage_start=coverage_start, coverage_end=coverage_end)
    except intake.DuplicateUpload as e:
        existing = e.existing
        note = "Identical to an earlier upload; nothing new was stored."
        if export_type != fp.PAYMENTS and (existing.declared_export_date or date.min) < date.fromisoformat(export_date):
            note += " A snapshot that is byte-identical on a later day is unusual; check that Frazer produced a fresh file."
        res = {**base, "outcome": "duplicate", "message": note, "upload": existing.upload_id, "upload_pk": existing.id}
        _set_result(run, export_type, res)
        db.flush()
        return res
    except intake.IntakeError as e:
        return refuse("rejected", str(e))
    errors = sum(1 for x in up.exceptions if x.severity == "error")
    outcome = "stored_with_errors" if errors else "stored"
    msg = (f"Stored as {up.upload_id}: {up.row_count} rows." if not errors else
           f"Stored as {up.upload_id}, but {errors} row(s) could not be read, so reconciliation will not use it.")
    res = {**base, "outcome": outcome, "message": msg, "upload": up.upload_id, "upload_pk": up.id, "rows": up.row_count,
           "observed_start": up.observed_coverage_start.isoformat() if up.observed_coverage_start else None,
           "observed_end": up.observed_coverage_end.isoformat() if up.observed_coverage_end else None}
    _set_result(run, export_type, res)
    db.flush()
    return res


def report_failure(db: Session, run: FrazerHelperRun, export_type: str, outcome: str, message: str) -> dict:
    """The helper could not produce or find a file (export failed, never appeared, would not finish writing)."""
    if run.status != "running":
        raise BadRun(f"Run {run.run_id} is {run.status}.")
    if export_type not in EXPORT_TYPES or outcome not in ("failed", "incomplete", "stale", "missing"):
        raise HelperError("Unknown export type or outcome.")
    res = {"export_type": export_type, "outcome": outcome, "message": (message or "")[:400], "upload": None,
           "received_at": _now().isoformat(timespec="seconds")}
    _set_result(run, export_type, res)
    db.flush()
    return res


def _sync(db: Session) -> dict:
    """Reconciliation reads the new uploads through its own bridge. It regenerates questions; it resolves none of them."""
    before = bridge.pending(db)
    if not before:
        return {"state": "nothing_to_sync", "detail": "No new vehicle or customer-balance upload needed reading."}
    bridge.auto_sync(db)                    # commits (or rolls back and logs) by itself
    left = [u.upload_id for u in bridge.pending(db)]
    if left:
        return {"state": "failed", "detail": "Reconciliation could not read: " + ", ".join(left)}
    return {"state": "synced", "detail": f"{len(before)} upload(s) read by reconciliation."}


def finish_run(db: Session, run: FrazerHelperRun) -> FrazerHelperRun:
    if run.status != "running":
        return run                                                      # finishing twice is harmless
    results = dict(run.results_json or {})
    for t in EXPORT_TYPES:
        results.setdefault(t, {"export_type": t, "outcome": "missing", "message": "No file was sent for this export.", "upload": None})
    run.results_json = results
    good = [t for t in EXPORT_TYPES if results[t]["outcome"] in GOOD]
    new_snapshot = any(results[t]["outcome"] == "stored" and t in (fp.VEHICLES, fp.CUSTOMERS) for t in EXPORT_TYPES)
    run.sync_json = _sync(db) if new_snapshot else {"state": "not_needed", "detail": "No new snapshot was stored."}
    if len(good) == 3 and run.sync_json["state"] != "failed":
        status, summary = "complete", "All three exports were stored (or were already stored)."
    elif good:
        status = "partial"
        summary = "Partly done: " + ", ".join(good) + " ok; not ok: " + ", ".join(t for t in EXPORT_TYPES if t not in good) + "."
        if run.sync_json["state"] == "failed":
            summary += " Reconciliation did not finish reading."
    else:
        status, summary = "failed", "Nothing was stored."
    _close(db, run, status, summary)
    return run


# --- what the screens show ----------------------------------------------------------------------------------------------

def freshness(db: Session, now: Optional[datetime] = None) -> dict:
    """When each export type was last confirmed current, from whichever is newer: the newest parsed upload (however it arrived), or a
    helper run whose file for that type was checked fresh and stored or recognised as identical to an earlier upload. The second
    matters: an unchanged file is a duplicate and adds no upload, yet the run did prove the data is current as of that file's time."""
    now = now or _now()
    runs = (db.query(FrazerHelperRun).filter(FrazerHelperRun.status.in_(("complete", "partial")))
            .order_by(FrazerHelperRun.id.desc()).limit(60).all())
    out = {}
    for t in EXPORT_TYPES:
        up = (db.query(FrazerUpload).filter(FrazerUpload.export_type == t, FrazerUpload.status == "parsed")
              .order_by(FrazerUpload.uploaded_at.desc(), FrazerUpload.id.desc()).first())
        confirmed, via = (up.uploaded_at, f"upload {up.upload_id}") if up else (None, None)
        for r in runs:
            res = (r.results_json or {}).get(t) or {}
            if res.get("outcome") in ("stored", "duplicate", "carried") and res.get("file_written_at"):
                when = datetime.fromisoformat(res["file_written_at"])
                if confirmed is None or when > confirmed:
                    confirmed, via = when, f"helper run {res.get('carried_from') or r.run_id} ({res['outcome']}, file written {res['file_written_at']} UTC)"
                break
        age = (now - confirmed).total_seconds() / 3600 if confirmed else None
        out[t] = {"upload": up, "age_hours": age, "stale": age is None or age > STALE_AFTER_HOURS, "confirmed_at": confirmed, "via": via,
                  "declared": up.declared_export_date if up else None, "coverage_end": up.declared_coverage_end if up else None}
    last = db.query(FrazerHelperRun).filter(FrazerHelperRun.status == "complete").order_by(FrazerHelperRun.finished_at.desc()).first()
    out["last_complete_run"] = last
    out["any_stale"] = any(out[t]["stale"] for t in EXPORT_TYPES)
    return out
