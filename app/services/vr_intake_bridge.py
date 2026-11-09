"""
Connect Frazer intake (finance/frazer, admin uploads) to vehicle reconciliation, without a command-line step or a hook in intake.

Intake already parsed and stored every row of an upload (frazer_source_rows). This bridge turns each parsed vehicle or
customer-balance upload into a reconciliation snapshot, identified by the upload's SHA-256 so it is idempotent: re-running it, a
second upload of identical bytes (intake refuses those anyway) or a command-line load of the same file all resolve to the one
snapshot. It reads intake tables and writes only vr_* tables. It never edits an upload, a row or an exception.

An upload with any error-severity exception is NOT used (one unreadable row could hide a stock number): it is reported as
"not used" with the reason, and reconciliation shows that. Rejected uploads and payment exports are not relevant here.
"""
import logging
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.frazer_intake import FrazerSourceRow, FrazerUpload, FrazerUploadException
from app.models.vehicle_recon import SOURCE_FRAZER_CONTRACTS, SOURCE_FRAZER_VEHICLES, VrSnapshot
from app.services import vr_sources as vr

log = logging.getLogger(__name__)
_TYPES = {"vehicles": SOURCE_FRAZER_VEHICLES, "customer_balances": SOURCE_FRAZER_CONTRACTS}


def _source_date(up: FrazerUpload):
    """The export date the uploader declared (or intake read from the filename). Otherwise the upload day, said plainly."""
    if up.declared_export_date:
        return up.declared_export_date, up.actual_cutoff or "Unknown"
    day = up.uploaded_at.date() if up.uploaded_at else date.today()
    return day, f"Export date not declared; upload date used. Actual cutoff: {up.actual_cutoff or 'Unknown'}"


def ingest_upload(db: Session, up: FrazerUpload) -> dict:
    """-> {"state": ingested | already | not_used | not_relevant, "reason": str}"""
    stype = _TYPES.get(up.export_type)
    if up.status != "parsed" or stype is None:
        return {"state": "not_relevant", "reason": "Not a parsed vehicle or customer-balance export."}
    errors = db.query(FrazerUploadException).filter(FrazerUploadException.upload_id == up.id,
                                                    FrazerUploadException.severity == "error").count()
    if errors:
        return {"state": "not_used", "reason": f"{errors} unreadable row(s) in the upload; reconciliation does not use a file it cannot read completely."}
    d, cutoff = _source_date(up)
    snap, created = vr.snapshot_for_bytes_or_hash(db, stype, up.sha256, f"{up.upload_id} {up.original_filename}", up.original_filename,
                                                  d, up.uploaded_by_user_id, cutoff)
    if not created:
        return {"state": "already", "reason": ""}
    fields = [r.fields_json for r in db.query(FrazerSourceRow).filter(FrazerSourceRow.upload_id == up.id).order_by(FrazerSourceRow.row_number)]
    rows = vr.vehicle_rows(fields, d) if stype == SOURCE_FRAZER_VEHICLES else vr.contract_rows(fields, d)
    counts = vr.store_rows(db, snap, rows)
    return {"state": "ingested", "reason": "", **counts}


def sync_from_intake(db: Session) -> list:
    """Ingest every relevant upload not yet used, oldest first. Returns [(upload_id, result)]."""
    out = []
    for up in db.query(FrazerUpload).order_by(FrazerUpload.id).all():
        res = ingest_upload(db, up)
        if res["state"] != "not_relevant":
            out.append((up.upload_id, res))
    return out


def pending(db: Session) -> list:
    """Relevant, readable uploads that reconciliation has not used yet."""
    return [r["upload"] for r in intake_status(db) if not r["used"] and not r["errors"]]


def auto_sync(db: Session) -> Optional[dict]:
    """
    Called when an administrator opens the vehicle list: pull in any new Frazer upload, then regenerate questions.
    Intake itself is untouched (REQ-FZ-06: an upload writes only to intake's own tables); reconciliation reads intake.
    Idempotent and failure-isolated: if it fails, the list still renders from what was already stored.
    """
    try:
        if not pending(db):
            return None
        res = sync_from_intake(db)
        from app.services import vehicle_recon_service as svc
        svc.refresh(db, date.today())
        db.commit()
        return {"uploads": res}
    except Exception:       # noqa: BLE001
        db.rollback()
        log.exception("vehicle reconciliation could not read Frazer intake")
        return None


def intake_status(db: Session) -> list:
    """For the reconciliation screen: each relevant upload and whether reconciliation uses it."""
    used = {s.file_sha256 for s in db.query(VrSnapshot).filter(VrSnapshot.file_sha256.isnot(None)).all()}
    rows = []
    for up in db.query(FrazerUpload).order_by(FrazerUpload.id.desc()).all():
        if _TYPES.get(up.export_type) is None or up.status != "parsed":
            continue
        errors = db.query(FrazerUploadException).filter(FrazerUploadException.upload_id == up.id, FrazerUploadException.severity == "error").count()
        rows.append({"upload": up, "used": up.sha256 in used, "errors": errors})
    return rows
