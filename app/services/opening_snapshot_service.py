"""
Opening Snapshot: record what was reported for a starting date, exactly as reported.

Every function here inserts. None updates an original field; the model layer
refuses such an update anyway (app/models/opening_snapshot.py). Who did a thing
always comes from the signed-in user passed in by the route, never from a form.

Callers commit. add_evidence() writes the file before the caller commits, so a
route must call discard_evidence_file() if its commit fails.
"""
import hashlib
import mimetypes
import os
import re
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

from sqlalchemy.orm import Session

from app.config import STORAGE_DIR
from app.models.enums import (
    OpeningEntryType, OpeningEvidenceKind, OpeningFigureStatus, OpeningSection,
)
from app.models.opening_snapshot import (
    OpeningFigure, OpeningFigureEntry, OpeningFigureEvidence, OpeningSnapshot,
)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".csv", ".xlsx", ".xls", ".txt"}

# Offered in the clarification form; "other" takes free text.
BASIS_CHOICES = [
    ("unknown", "Unknown"),
    ("purchase cost", "Purchase cost"),
    ("total invested", "Total invested"),
    ("expected sale value", "Expected sale value"),
    ("statement balance", "Statement balance"),
]

SECTION_ORDER = [
    (OpeningSection.cash, "Cash reported"),
    (OpeningSection.other_asset, "Other reported figures"),
    (OpeningSection.debt, "Debts reported"),
    (OpeningSection.commitment, "Known commitments"),
    (OpeningSection.not_reported, "Not yet reported"),
]


class SnapshotError(ValueError):
    """Bad input; the message is safe to show the user."""


class Conflict(SnapshotError):
    """The action is not allowed in the record's current state (maps to HTTP 409)."""


def storage_root() -> str:
    # Looked up at call time so tests can point it at a temporary folder.
    return os.path.join(STORAGE_DIR, "opening_snapshot")


# ---- snapshot ------------------------------------------------------------

def get_snapshot(db: Session) -> Optional[OpeningSnapshot]:
    return db.query(OpeningSnapshot).order_by(OpeningSnapshot.id).first()


def create_snapshot(db: Session, user, *, name: str, as_of_date: date, label: str,
                    cutoff_note: str) -> OpeningSnapshot:
    if get_snapshot(db) is not None:
        raise Conflict("A snapshot already exists. Only one is allowed in this release.")
    name, label = name.strip(), label.strip()
    if not name or not label:
        raise SnapshotError("Name and label are required.")
    snap = OpeningSnapshot(
        name=name, as_of_date=as_of_date, label=label,
        cutoff_note=cutoff_note.strip() or "Unknown",
        created_by_user_id=user.id,
    )
    db.add(snap)
    db.flush()
    snap.snapshot_id = f"OS-{snap.id:04d}"
    return snap


# ---- figures -------------------------------------------------------------

def _money(value: str, field: str) -> Optional[Decimal]:
    value = (value or "").strip().replace(",", "").lstrip("$")
    if not value:
        return None
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise SnapshotError(f"{field} must be a number.")
    if amount < 0:
        raise SnapshotError(f"{field} can't be negative.")
    return amount.quantize(Decimal("0.01"))


def _whole(value: str, field: str) -> Optional[int]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        n = int(value)
    except ValueError:
        raise SnapshotError(f"{field} must be a whole number.")
    if n < 0:
        raise SnapshotError(f"{field} can't be negative.")
    return n


def _opt(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def create_figure(db: Session, snapshot: OpeningSnapshot, user, *, section: str, label: str,
                  amount: str = "", count: str = "", statement: str = "", reported_by: str,
                  reported_via: str, reported_on: Optional[date] = None, time_note: str = "",
                  cutoff: str = "", status: str, pending_need: str = "", basis_note: str = "",
                  due_note: str = "") -> OpeningFigure:
    try:
        section_v = OpeningSection(section)
        status_v = OpeningFigureStatus(status)
    except ValueError:
        raise SnapshotError("Choose a section and a status from the list.")
    label = label.strip()
    if not label:
        raise SnapshotError("Label is required.")
    if not reported_by.strip() or not reported_via.strip():
        raise SnapshotError("Say who reported it and how (for example, text message).")
    amount_v = _money(amount, "Amount")
    count_v = _whole(count, "Count")
    need = _opt(pending_need)
    if status_v is OpeningFigureStatus.pending:
        if amount_v is not None:
            raise SnapshotError("A pending item has no amount. Leave it blank until something is reported.")
        if not need:
            raise SnapshotError("Say what is needed to fill in a pending item.")
    elif amount_v is None and count_v is None and not statement.strip():
        raise SnapshotError("A reported figure needs an amount, a count, or what was said.")
    order = (db.query(OpeningFigure).filter(OpeningFigure.snapshot_id == snapshot.id).count()) + 1
    fig = OpeningFigure(
        snapshot_id=snapshot.id, section=section_v, label=label,
        reported_amount=amount_v, reported_count=count_v,
        reported_statement=statement.strip(),
        reported_by_name=reported_by.strip(), reported_via=reported_via.strip(),
        reported_on=reported_on, reported_time_note=_opt(time_note),
        reported_cutoff=cutoff.strip() or "Unknown", status=status_v,
        basis_note=_opt(basis_note), pending_need=need if status_v is OpeningFigureStatus.pending else None,
        due_note=_opt(due_note), display_order=order, recorded_by_user_id=user.id,
    )
    db.add(fig)
    db.flush()
    fig.figure_id = f"OF-{fig.id:04d}"
    return fig


def _require_open(figure: OpeningFigure) -> None:
    if figure.is_withdrawn:
        raise Conflict("This figure was withdrawn. Nothing more can be added to it.")


def withdraw_figure(db: Session, figure: OpeningFigure, user, reason: str) -> OpeningFigureEntry:
    if figure.is_withdrawn:
        raise Conflict("This figure was already withdrawn.")
    reason = reason.strip()
    if not reason:
        raise SnapshotError("A reason is required to withdraw a figure.")
    figure.withdrawn_at = datetime.utcnow()
    figure.withdrawn_by_user_id = user.id
    figure.withdrawn_reason = reason
    entry = OpeningFigureEntry(
        figure_id=figure.id, entry_type=OpeningEntryType.withdrawal,
        body=f"Withdrawn: {reason}", created_by_user_id=user.id,
    )
    db.add(entry)
    db.flush()
    return entry


# ---- clarifications ------------------------------------------------------

def add_clarification(db: Session, figure: OpeningFigure, user, *, body: str,
                      refers_to_date: Optional[date] = None, cutoff_update: str = "",
                      basis_update: str = "", basis_other: str = "", basis_confirmed: str = "",
                      corrects_entry_id: str = "") -> OpeningFigureEntry:
    _require_open(figure)
    body = body.strip()
    if not body:
        raise SnapshotError("Say what you learned.")
    basis = _opt(basis_other) if basis_update == "other" else _opt(basis_update)
    if basis_update == "other" and not basis:
        raise SnapshotError("Type the basis, or pick one from the list.")
    confirmed = {"yes": True, "no": False}.get((basis_confirmed or "").strip().lower())
    corrects = None
    if (corrects_entry_id or "").strip():
        try:
            corrects = int(corrects_entry_id)
        except ValueError:
            raise SnapshotError("Choose an entry from the list to correct.")
        target = next((e for e in figure.entries if e.id == corrects), None)
        if target is None or target.entry_type not in (OpeningEntryType.clarification, OpeningEntryType.correction):
            raise SnapshotError("That entry can't be corrected here.")
    entry = OpeningFigureEntry(
        figure_id=figure.id,
        entry_type=OpeningEntryType.correction if corrects else OpeningEntryType.clarification,
        body=body, refers_to_date=refers_to_date, cutoff_update=_opt(cutoff_update),
        basis_update=basis, basis_confirmed=confirmed, corrects_entry_id=corrects,
        created_by_user_id=user.id,
    )
    db.add(entry)
    db.flush()
    return entry


def current_understanding(figure: OpeningFigure) -> dict:
    """
    What is currently understood about a figure's cutoff and basis, built from the
    history without touching the original report.

    An entry that a later correction points at no longer counts; the correction's
    own updates (if any) do. For each of cutoff, basis and basis_confirmed the most
    recent surviving non-empty value wins. Each value carries who set it and when.
    """
    entries = [e for e in figure.entries
               if e.entry_type in (OpeningEntryType.clarification, OpeningEntryType.correction)]
    superseded = {e.corrects_entry_id for e in entries if e.corrects_entry_id}
    live = [e for e in entries if e.id not in superseded]
    out = {"cutoff": None, "basis": None, "basis_confirmed": None}
    for e in live:  # oldest first, so later ones overwrite
        for key, value in (("cutoff", e.cutoff_update), ("basis", e.basis_update),
                           ("basis_confirmed", e.basis_confirmed)):
            if value is not None:
                out[key] = {"value": value, "by": e.created_by, "at": e.created_at, "entry_id": e.id}
    return out


def clarification_count(figure: OpeningFigure) -> int:
    return sum(1 for e in figure.entries
               if e.entry_type in (OpeningEntryType.clarification, OpeningEntryType.correction))


def overview(db: Session, snapshot: OpeningSnapshot) -> list:
    """[(label, [figure cards])] in the fixed section order; withdrawn figures excluded."""
    figures = [f for f in snapshot.figures if not f.is_withdrawn]
    sections = []
    for section, label in SECTION_ORDER:
        cards = []
        for f in (x for x in figures if x.section == section):
            cu = current_understanding(f)
            cards.append({
                "figure": f, "understanding": cu,
                "cutoff": cu["cutoff"]["value"] if cu["cutoff"] else f.reported_cutoff,
                "basis_needed": f.status is OpeningFigureStatus.reported
                                and not (cu["basis_confirmed"] and cu["basis_confirmed"]["value"]),
                "clarifications": clarification_count(f),
                "evidence_count": len(f.evidence),
            })
        sections.append((label, cards))
    return sections


# ---- evidence ------------------------------------------------------------

def _safe_filename(raw: str) -> str:
    name = os.path.basename((raw or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().lstrip(".")
    return name[:200] or "upload"


def add_evidence(db: Session, figure: OpeningFigure, user, *, file, kind: str, description: str,
                 no_pay_detail: bool) -> OpeningFigureEvidence:
    """Store one file as evidence. Returns the evidence row; the file is already on disk."""
    _require_open(figure)
    if not no_pay_detail:
        raise SnapshotError("Confirm that the file does not contain individual employee pay.")
    try:
        kind_v = OpeningEvidenceKind(kind)
    except ValueError:
        raise SnapshotError("Choose what kind of evidence this is.")
    if file is None or not getattr(file, "filename", ""):
        raise SnapshotError("Choose a file to attach.")
    description = description.strip()
    if not description:
        raise SnapshotError("Add a short description of what the file shows.")
    original = _safe_filename(file.filename)
    ext = os.path.splitext(original)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise SnapshotError("That file type isn't accepted. Use PDF, PNG, JPG, HEIC, CSV, XLSX, XLS or TXT.")

    dest_dir = os.path.join(storage_root(), str(figure.id))
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"{uuid.uuid4().hex}{ext}")
    digest, size = hashlib.sha256(), 0
    try:
        with open(dest, "wb") as out:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise SnapshotError("That file is larger than 20 MB.")
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise SnapshotError("That file is empty.")
        entry = OpeningFigureEntry(
            figure_id=figure.id, entry_type=OpeningEntryType.evidence_added,
            body=f"Evidence added: {original} ({kind_v.value.replace('_', ' ')}). {description}",
            created_by_user_id=user.id,
        )
        db.add(entry)
        db.flush()
        ev = OpeningFigureEvidence(
            figure_id=figure.id, entry_id=entry.id, kind=kind_v, description=description,
            original_filename=original, stored_path=dest,
            content_type=mimetypes.guess_type(original)[0] or "application/octet-stream",
            size_bytes=size, sha256=digest.hexdigest(), no_pay_detail_confirmed=True,
            uploaded_by_user_id=user.id,
        )
        db.add(ev)
        db.flush()
    except Exception:
        discard_evidence_file(dest)
        raise
    return ev


def discard_evidence_file(path: str) -> None:
    """Remove a file written by add_evidence() whose transaction did not commit."""
    try:
        os.remove(path)
    except OSError:
        pass


def evidence_file_path(ev: OpeningFigureEvidence) -> Optional[str]:
    """The stored path, only if it really is inside the snapshot evidence folder."""
    root = os.path.realpath(storage_root())
    path = os.path.realpath(ev.stored_path)
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        return None
    return path
