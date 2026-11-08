"""
Frazer source intake: uploaded Frazer exports and what was read from them.

These tables hold SOURCE EVIDENCE only. Nothing here is a purchase, unit, payment,
transaction, customer, vendor, document or cash movement, and nothing in this module
creates or changes one (spec section 19, REQ-FZ-06).

  frazer_uploads            one uploaded file: type, name, hash, declared and observed coverage,
                            actual cutoff (default "Unknown"), summary
  frazer_source_rows        one data row of a parsed file, with every original cell and the
                            parsed values, so versions of the same identity are all kept
  frazer_upload_exceptions  coded problems and notes, with source line numbers
  frazer_upload_notes       append-only notes (for example what the actual cutoff turned out to be)

All four are insert-only in practice; the listeners at the bottom refuse an UPDATE (apart from
setting a row's human-readable id once) and any DELETE, so no later code path can quietly
overwrite an earlier version.
"""
from datetime import datetime

from sqlalchemy import JSON, Column, Date, DateTime, ForeignKey, Integer, String, Text, event, inspect
from sqlalchemy.orm import relationship

from app.database import Base


class FrazerUpload(Base):
    __tablename__ = "frazer_uploads"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(String(20), unique=True, nullable=True)          # FU-0001, set after the first flush
    export_type = Column(String(30), nullable=False)                    # vehicles | customer_balances | payments | unrecognized
    layout = Column(String(60), nullable=True)
    parser_version = Column(String(30), nullable=False)
    original_filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    encoding = Column(String(20), nullable=True)
    status = Column(String(20), nullable=False)                          # parsed | rejected
    # What the uploader says (never inferred from the filename).
    declared_export_date = Column(Date, nullable=True)
    declared_coverage_start = Column(Date, nullable=True)
    declared_coverage_end = Column(Date, nullable=True)
    # What the data shows.
    observed_coverage_start = Column(Date, nullable=True)
    observed_coverage_end = Column(Date, nullable=True)
    # The real moment the export was taken, kept apart from the above. "Unknown" until supported.
    actual_cutoff = Column(Text, nullable=False, default="Unknown")
    row_count = Column(Integer, nullable=False, default=0)
    headers_json = Column(JSON, nullable=True)
    summary_json = Column(JSON, nullable=True)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])
    rows = relationship("FrazerSourceRow", back_populates="upload", order_by="FrazerSourceRow.row_number")
    exceptions = relationship("FrazerUploadException", back_populates="upload",
                              order_by="FrazerUploadException.id")
    notes = relationship("FrazerUploadNote", back_populates="upload", order_by="FrazerUploadNote.id")


class FrazerSourceRow(Base):
    __tablename__ = "frazer_source_rows"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("frazer_uploads.id"), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)                         # source line; the header is line 1
    record_type = Column(String(30), nullable=False)                     # vehicle | customer_balance | payment_entry
    identity = Column(String(100), nullable=True, index=True)            # candidate identity, as text
    content_hash = Column(String(64), nullable=False)
    entry_class = Column(String(30), nullable=True)                      # payments only; from the source description
    raw_json = Column(JSON, nullable=False)                              # header -> original cell, as written
    fields_json = Column(JSON, nullable=False)                           # parsed values (identifiers stay text)

    upload = relationship("FrazerUpload", back_populates="rows")


class FrazerUploadException(Base):
    __tablename__ = "frazer_upload_exceptions"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("frazer_uploads.id"), nullable=False, index=True)
    row_number = Column(Integer, nullable=True)                          # null = about the whole file
    identity = Column(String(100), nullable=True)
    code = Column(String(60), nullable=False)
    severity = Column(String(10), nullable=False)                        # error | warning | info
    field = Column(String(60), nullable=True)
    message = Column(Text, nullable=False)

    upload = relationship("FrazerUpload", back_populates="exceptions")


class FrazerUploadNote(Base):
    __tablename__ = "frazer_upload_notes"

    id = Column(Integer, primary_key=True, index=True)
    upload_id = Column(Integer, ForeignKey("frazer_uploads.id"), nullable=False, index=True)
    note = Column(Text, nullable=False)
    actual_cutoff = Column(Text, nullable=True)                          # what the note says the cutoff was, if stated
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    upload = relationship("FrazerUpload", back_populates="notes")
    created_by = relationship("User", foreign_keys=[created_by_user_id])


# ---------------------------------------------------------------------------
# Insert-only guards
# ---------------------------------------------------------------------------

class ImmutableSourceRecordError(RuntimeError):
    """An attempt to change or delete uploaded Frazer source data."""


def _guard_update(mapper, connection, target):
    state = inspect(target)
    changed = []
    for attr in state.mapper.column_attrs:
        hist = state.attrs[attr.key].history
        if not hist.has_changes():
            continue
        # The human-readable id is set once, right after the first flush.
        if isinstance(target, FrazerUpload) and attr.key == "upload_id" and not [d for d in hist.deleted if d is not None]:
            continue
        changed.append(attr.key)
    if changed:
        raise ImmutableSourceRecordError(
            f"{type(target).__name__} is kept as uploaded; cannot change {', '.join(changed)}."
        )


def _guard_delete(mapper, connection, target):
    raise ImmutableSourceRecordError(f"{type(target).__name__} cannot be deleted.")


for _model in (FrazerUpload, FrazerSourceRow, FrazerUploadException, FrazerUploadNote):
    event.listen(_model, "before_update", _guard_update)
    event.listen(_model, "before_delete", _guard_delete)
