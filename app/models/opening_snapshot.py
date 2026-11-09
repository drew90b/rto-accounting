"""
Opening Snapshot: what was reported for a starting date, kept as reported.

Four tables, all insert-only in practice:

  opening_snapshots         the dated snapshot (normally one row)
  opening_figures           one reported figure. Every column except withdrawn_*
                            is the ORIGINAL REPORT, written once at insert.
  opening_figure_entries    append-only history: clarifications, corrections,
                            evidence-added and withdrawal notes
  opening_figure_evidence   an uploaded file with its hash, in its own store
                            (never the shared documents table)

Nothing here is ever edited or deleted through the app. The listeners at the
bottom refuse an UPDATE of an original column and any DELETE, so a future code
path cannot quietly break that rule.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text, event, inspect,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.enums import (
    OpeningEntryType, OpeningEvidenceKind, OpeningFigureStatus, OpeningSection,
)


class OpeningSnapshot(Base):
    __tablename__ = "opening_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(String(20), unique=True, nullable=True)
    name = Column(String(100), nullable=False)
    as_of_date = Column(Date, nullable=False)
    label = Column(String(50), nullable=False)
    cutoff_note = Column(Text, nullable=False, default="Unknown")
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    figures = relationship("OpeningFigure", back_populates="snapshot",
                           order_by="OpeningFigure.display_order, OpeningFigure.id")


class OpeningFigure(Base):
    __tablename__ = "opening_figures"

    id = Column(Integer, primary_key=True, index=True)
    figure_id = Column(String(20), unique=True, nullable=True)
    snapshot_id = Column(Integer, ForeignKey("opening_snapshots.id"), nullable=False, index=True)

    # ---- the original report: written once, never changed ------------------
    section = Column(SAEnum(OpeningSection, native_enum=False, length=20), nullable=False)
    label = Column(String(150), nullable=False)
    reported_amount = Column(Numeric(12, 2), nullable=True)
    reported_count = Column(Integer, nullable=True)
    reported_statement = Column(Text, nullable=False, default="")
    reported_by_name = Column(String(100), nullable=False)
    reported_via = Column(String(100), nullable=False)
    reported_on = Column(Date, nullable=True)
    reported_time_note = Column(String(100), nullable=True)
    reported_cutoff = Column(String(200), nullable=False, default="Unknown")
    status = Column(SAEnum(OpeningFigureStatus, native_enum=False, length=20), nullable=False)
    basis_note = Column(String(200), nullable=True)
    pending_need = Column(String(300), nullable=True)
    due_note = Column(String(200), nullable=True)
    display_order = Column(Integer, nullable=False, default=0)
    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # ---- withdrawal: set once, when a mistaken entry is taken off the page --
    withdrawn_at = Column(DateTime, nullable=True)
    withdrawn_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    withdrawn_reason = Column(Text, nullable=True)

    snapshot = relationship("OpeningSnapshot", back_populates="figures")
    recorded_by = relationship("User", foreign_keys=[recorded_by_user_id])
    withdrawn_by = relationship("User", foreign_keys=[withdrawn_by_user_id])
    entries = relationship("OpeningFigureEntry", back_populates="figure",
                           order_by="OpeningFigureEntry.created_at, OpeningFigureEntry.id")
    evidence = relationship("OpeningFigureEvidence", back_populates="figure",
                            order_by="OpeningFigureEvidence.uploaded_at, OpeningFigureEvidence.id")

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None


class OpeningFigureEntry(Base):
    __tablename__ = "opening_figure_entries"

    id = Column(Integer, primary_key=True, index=True)
    figure_id = Column(Integer, ForeignKey("opening_figures.id"), nullable=False, index=True)
    entry_type = Column(SAEnum(OpeningEntryType, native_enum=False, length=20), nullable=False)
    body = Column(Text, nullable=False)
    refers_to_date = Column(Date, nullable=True)
    cutoff_update = Column(String(200), nullable=True)
    basis_update = Column(String(200), nullable=True)
    basis_confirmed = Column(Boolean, nullable=True)
    corrects_entry_id = Column(Integer, ForeignKey("opening_figure_entries.id"), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    figure = relationship("OpeningFigure", back_populates="entries")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    corrects = relationship("OpeningFigureEntry", remote_side=[id])


class OpeningFigureEvidence(Base):
    __tablename__ = "opening_figure_evidence"

    id = Column(Integer, primary_key=True, index=True)
    figure_id = Column(Integer, ForeignKey("opening_figures.id"), nullable=False, index=True)
    entry_id = Column(Integer, ForeignKey("opening_figure_entries.id"), nullable=False)
    kind = Column(SAEnum(OpeningEvidenceKind, native_enum=False, length=30), nullable=False)
    description = Column(Text, nullable=False)
    original_filename = Column(String(200), nullable=False)
    stored_path = Column(String(500), nullable=False)
    content_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    no_pay_detail_confirmed = Column(Boolean, nullable=False)
    uploaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    figure = relationship("OpeningFigure", back_populates="evidence")
    entry = relationship("OpeningFigureEntry")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])


# ---------------------------------------------------------------------------
# Insert-only guards
# ---------------------------------------------------------------------------

# Human-readable ids are generated after the first flush (docs/business_rules.md
# section 0), so setting one from None is the only allowed "update" of those columns.
_HUMAN_IDS = {"snapshot_id": "OpeningSnapshot", "figure_id": "OpeningFigure"}
_MUTABLE_FIGURE_COLUMNS = {"withdrawn_at", "withdrawn_by_user_id", "withdrawn_reason"}


class ImmutableRecordError(RuntimeError):
    """An attempt to change or delete something the Opening Snapshot keeps as first recorded."""


def _changed_columns(target):
    state = inspect(target)
    changed = []
    for attr in state.mapper.column_attrs:
        hist = state.attrs[attr.key].history
        if not hist.has_changes():
            continue
        if attr.key in ("snapshot_id", "figure_id") and attr.key == _own_human_id(target):
            # Allowed only for None -> value.
            if not [d for d in hist.deleted if d is not None]:
                continue
        changed.append(attr.key)
    return changed


def _own_human_id(target):
    return "snapshot_id" if isinstance(target, OpeningSnapshot) else (
        "figure_id" if isinstance(target, OpeningFigure) else None)


def _guard_update(mapper, connection, target):
    changed = _changed_columns(target)
    if isinstance(target, OpeningFigure):
        changed = [c for c in changed if c not in _MUTABLE_FIGURE_COLUMNS]
        # A figure may be withdrawn once; never un-withdrawn or re-withdrawn.
        hist = inspect(target).attrs.withdrawn_at.history
        if hist.has_changes() and [d for d in hist.deleted if d is not None]:
            changed.append("withdrawn_at")
    if changed:
        raise ImmutableRecordError(
            f"{type(target).__name__} is kept as first recorded; cannot change {', '.join(changed)}."
        )


def _guard_delete(mapper, connection, target):
    raise ImmutableRecordError(f"{type(target).__name__} cannot be deleted.")


for _model in (OpeningSnapshot, OpeningFigure, OpeningFigureEntry, OpeningFigureEvidence):
    event.listen(_model, "before_update", _guard_update)
    event.listen(_model, "before_delete", _guard_delete)
