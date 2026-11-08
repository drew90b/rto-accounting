"""
Vehicle reconciliation records (companion spec docs/specs/vehicle-reconciliation-spec.md, REQ-VR-*).

Every table is prefixed vr_. Nothing here is an operational record: no purchase, unit, transaction or payment is created or
changed by reading or writing these tables (REQ-VR-02).

  VrSnapshot      one load of one source (a file, or an observation of the app's units / purchases), with its source date
  VrSourceRecord  one row as that source reported it; immutable; never holds customer names, balances or payments
  VrQuestion      one specific question with a stable key; mutable only through the service (state, owner, version)
  VrDecision      one answer and, for identity/financial kinds, its approval; superseded, never edited or deleted
  VrEvent         the append-only history of everything above
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, event,
)

from app.database import Base

SOURCE_FRAZER_VEHICLES = "frazer_vehicles"
SOURCE_FRAZER_CONTRACTS = "frazer_contracts"
SOURCE_APP_UNITS = "app_units"
SOURCE_APP_PURCHASES = "app_purchases"
SOURCES = (SOURCE_FRAZER_VEHICLES, SOURCE_FRAZER_CONTRACTS, SOURCE_APP_UNITS, SOURCE_APP_PURCHASES)
SOURCE_LABEL = {
    SOURCE_FRAZER_VEHICLES: "Frazer vehicle list",
    SOURCE_FRAZER_CONTRACTS: "Frazer customer-balance list (contract exists)",
    SOURCE_APP_UNITS: "App unit",
    SOURCE_APP_PURCHASES: "App purchase",
}


class VrSnapshot(Base):
    __tablename__ = "vr_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    source_type = Column(String(30), nullable=False, index=True)
    label = Column(String(200), nullable=False, default="")
    # The date the SOURCE says it is as of (an export date, or the day an app observation was taken). Not the load time.
    source_date = Column(Date, nullable=False)
    # What the uploader/exporter says vs what is known: the real cutoff time stays Unknown unless a person records it.
    cutoff_note = Column(String(200), nullable=False, default="Unknown")
    file_name = Column(String(255), nullable=True)
    file_sha256 = Column(String(64), nullable=True)
    row_count = Column(Integer, nullable=False, default=0)
    loaded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)   # null = run from the command line
    loaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("source_type", "file_sha256", name="uq_vr_snapshot_file"),)


class VrSourceRecord(Base):
    __tablename__ = "vr_source_records"

    id = Column(Integer, primary_key=True, index=True)
    snapshot_id = Column(Integer, ForeignKey("vr_snapshots.id"), nullable=False, index=True)
    source_type = Column(String(30), nullable=False, index=True)
    source_key = Column(String(120), nullable=False)          # the source's own identifier, as text (stock number, unit pk, purchase pk)
    stock_number = Column(String(50), nullable=True, index=True)   # exactly as written; leading zeros kept
    vin = Column(String(50), nullable=True, index=True)             # exactly as written
    year = Column(String(10), nullable=True)
    make = Column(String(60), nullable=True)
    model = Column(String(100), nullable=True)
    reported_status = Column(String(60), nullable=True)        # what the source labels it (never "where the car is")
    reported_status_date = Column(Date, nullable=True)
    # None = the source did not report a cost. Decimal("0.00") = the source reported zero (NOT "free").
    reported_cost = Column(Numeric(12, 2), nullable=True)
    fields_json = Column(JSON, nullable=False, default=dict)   # only the fields this feature needs; no customer data
    content_hash = Column(String(64), nullable=False)
    is_current = Column(Boolean, nullable=False, default=True)  # newest record for this (source, key); older ones stay as history
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (UniqueConstraint("snapshot_id", "source_key", name="uq_vr_record_per_snapshot"),)


class VrQuestion(Base):
    __tablename__ = "vr_questions"

    id = Column(Integer, primary_key=True, index=True)
    ref = Column(String(20), unique=True, nullable=True)             # VQ-0001, set once after the first flush
    question_key = Column(String(200), nullable=False, unique=True)  # stable: one question per subject, ever
    kind = Column(String(20), nullable=False, index=True)           # shared_vin | purchase_match | status_conflict
    title = Column(String(300), nullable=False)
    why = Column(Text, nullable=False, default="")                   # why it was raised
    subject_json = Column(JSON, nullable=False, default=dict)        # stock numbers / purchase refs it concerns
    evidence_json = Column(JSON, nullable=False, default=dict)       # frozen evidence summary + source record ids
    choices_json = Column(JSON, nullable=False, default=list)        # [{key, label, params?}]
    evidence_fingerprint = Column(String(64), nullable=False)
    # open | proposed (an answer waits for an administrator) | decided | review_needed | withdrawn
    status = Column(String(15), nullable=False, default="open", index=True)
    needs_someone_else = Column(Boolean, nullable=False, default=False)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    version = Column(Integer, nullable=False, default=1)             # compare-and-swap counter (REQ-VR-14)
    # The live decision (proposed or accepted). A plain integer, not a foreign key, to avoid a circular table dependency.
    current_decision_id = Column(Integer, nullable=True)
    withdrawn_reason = Column(String(300), nullable=True)
    raised_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class VrDecision(Base):
    __tablename__ = "vr_decisions"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("vr_questions.id"), nullable=False, index=True)
    choice_key = Column(String(60), nullable=False)
    params_json = Column(JSON, nullable=False, default=dict)         # e.g. {"as_of": "2026-11-08", "stock": "10118"}
    note = Column(Text, nullable=False, default="")
    evidence_json = Column(JSON, nullable=False, default=dict)       # what the answerer was shown (frozen)
    evidence_fingerprint = Column(String(64), nullable=False)
    effect_json = Column(JSON, nullable=False, default=dict)         # the defined effect, stated (REQ-VR-11)
    needs_approval = Column(Boolean, nullable=False, default=False)
    # proposed | accepted | rejected | superseded
    state = Column(String(12), nullable=False, default="proposed", index=True)
    answered_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    answered_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    decided_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)   # accepter / rejecter
    decided_at = Column(DateTime, nullable=True)
    supersedes_id = Column(Integer, ForeignKey("vr_decisions.id"), nullable=True)
    superseded_by_id = Column(Integer, ForeignKey("vr_decisions.id"), nullable=True)
    reason = Column(Text, nullable=False, default="")               # why a correction or rejection was made


class VrEvent(Base):
    __tablename__ = "vr_events"

    id = Column(Integer, primary_key=True, index=True)
    question_id = Column(Integer, ForeignKey("vr_questions.id"), nullable=True, index=True)
    event_type = Column(String(24), nullable=False)
    body = Column(Text, nullable=False, default="")
    data_json = Column(JSON, nullable=False, default=dict)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)   # null = the system (a refresh)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ImmutableRecordError(RuntimeError):
    pass


def _refuse_update(mapper, connection, target):
    """Source records, snapshots and events are append-only. Permitted: a record's is_current flag and a snapshot's row count
    (set once while the load that created it finishes)."""
    from sqlalchemy import inspect
    state = inspect(target)
    changed = {a.key for a in state.attrs if a.history.has_changes()}
    if isinstance(target, VrSourceRecord) and changed <= {"is_current"}:
        return
    if isinstance(target, VrSnapshot) and changed <= {"row_count"}:
        return
    raise ImmutableRecordError(f"{type(target).__name__} rows are append-only")


def _refuse_delete(mapper, connection, target):
    raise ImmutableRecordError(f"{type(target).__name__} rows are never deleted")


for _cls in (VrSourceRecord, VrEvent, VrSnapshot):
    event.listen(_cls, "before_update", _refuse_update)
    event.listen(_cls, "before_delete", _refuse_delete)
for _cls in (VrQuestion, VrDecision):
    event.listen(_cls, "before_delete", _refuse_delete)
