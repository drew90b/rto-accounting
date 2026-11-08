"""
Monthly Review workspace: issues, their history, owner review notes, and source attestations.

Spec: docs/specs/cash-financial-control-spec.md section 20 (Increment M). These tables organize WORK. They hold no
financial source data and nothing here verifies, reconciles or closes a month.

  review_issues         one action/issue for one month. Its identity, month, kind and automatic key are written
                        once; only status, owner, due date and the automatic-evidence columns ever change, and each
                        change is also written as an event
  review_issue_events   append-only history (created, assigned, status, due date, note, evidence, condition)
  review_notes          append-only owner review notes and decisions; a correction is a new note
  review_source_records append-only attestations that a record exists outside the app (coverage dates, where kept)
  review_issue_files    append-only supporting files on an issue (admins and the issue's owner)
  review_step_marks     append-only close-step confirmations, reviews and not-applicable decisions (latest per kind wins)

Listeners at the bottom refuse any DELETE, any UPDATE of an insert-only table, and any UPDATE of an issue's fixed columns.
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event, inspect,
)
from sqlalchemy.orm import relationship

from app.database import Base


class ReviewIssue(Base):
    __tablename__ = "review_issues"
    __table_args__ = (UniqueConstraint("month", "auto_key", name="uq_review_issue_month_auto_key"),)

    id = Column(Integer, primary_key=True, index=True)
    issue_id = Column(String(20), unique=True, nullable=True)               # RI-0001, set once after the first flush
    # ---- fixed at creation ----
    month = Column(Date, nullable=False, index=True)                         # first day of the month
    kind = Column(String(10), nullable=False)                                # manual | auto
    auto_key = Column(String(120), nullable=True)                            # stable per month for automatic issues
    category = Column(String(20), nullable=False)                            # records | coverage | mapping | payroll | reconciliation | other
    checklist_ref = Column(String(10), nullable=True)                        # CL-1..CL-6
    restricted = Column(Boolean, nullable=False, default=False)             # payroll: no amounts or names, ever
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    links_json = Column(JSON, nullable=False, default=list)                  # [{"label": ..., "url": ...}], validated server side
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)   # null = the system
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # ---- current state (each change also written as an event) ----
    status = Column(String(15), nullable=False, default="open")             # open | in_progress | waiting | resolved
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    due_date = Column(Date, nullable=True)
    due_is_proposed = Column(Boolean, nullable=False, default=False)         # a proposal (DEC-17/DEC-40), not agreed
    # ---- automatic evidence, kept apart from every human decision ----
    evidence_json = Column(JSON, nullable=True)
    evidence_updated_at = Column(DateTime, nullable=True)
    auto_active = Column(Boolean, nullable=False, default=True)             # does the detected condition still hold
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    owner = relationship("User", foreign_keys=[owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    events = relationship("ReviewIssueEvent", back_populates="issue", order_by="ReviewIssueEvent.id")


class ReviewIssueEvent(Base):
    __tablename__ = "review_issue_events"

    id = Column(Integer, primary_key=True, index=True)
    issue_pk = Column(Integer, ForeignKey("review_issues.id"), nullable=False, index=True)
    event_type = Column(String(20), nullable=False)       # created | assigned | status | due_date | note | evidence | cleared | returned
    from_value = Column(String(200), nullable=True)
    to_value = Column(String(200), nullable=True)
    body = Column(Text, nullable=False, default="")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)           # null = the system
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    issue = relationship("ReviewIssue", back_populates="events")
    user = relationship("User", foreign_keys=[user_id])


class ReviewNote(Base):
    __tablename__ = "review_notes"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(Date, nullable=False, index=True)
    note_type = Column(String(15), nullable=False)        # review | decision
    review_date = Column(Date, nullable=False)            # the business date of the review
    body = Column(Text, nullable=False)
    corrects_note_id = Column(Integer, ForeignKey("review_notes.id"), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)   # entered-at (REQ-AUD-01)

    user = relationship("User", foreign_keys=[user_id])


class ReviewSourceRecord(Base):
    __tablename__ = "review_source_records"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(Date, nullable=False, index=True)
    source_key = Column(String(30), nullable=False)       # bank_statement | square | cash_count | payroll_weekly
    status = Column(String(30), nullable=False)           # received | not_required (proposed) | not_required_confirmed (second administrator)
    period_label = Column(String(60), nullable=False, default="")   # for payroll: the pay date (ISO)
    coverage_start = Column(Date, nullable=True)
    coverage_end = Column(Date, nullable=True)
    where_kept = Column(String(200), nullable=False, default="")
    note = Column(Text, nullable=False, default="")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])


class ReviewStepMark(Base):
    """A person's recorded confirmation, review or not-applicable decision for one close step. Latest per (month, step, kind) wins."""
    __tablename__ = "review_step_marks"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(Date, nullable=False, index=True)
    step_key = Column(String(10), nullable=False)          # CL-1 .. CL-6
    kind = Column(String(15), nullable=False)              # confirm | review | not_applicable
    action = Column(String(5), nullable=False)             # set | clear
    note = Column(Text, nullable=False, default="")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    user = relationship("User", foreign_keys=[user_id])


class ReviewIssueFile(Base):
    """A supporting file added to an issue by an administrator or by the issue's owner. Stored outside the shared Documents list and
    served only through permission-checked routes (REQ-PRM-04)."""
    __tablename__ = "review_issue_files"

    id = Column(Integer, primary_key=True, index=True)
    issue_pk = Column(Integer, ForeignKey("review_issues.id"), nullable=False, index=True)
    original_filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    note = Column(Text, nullable=False, default="")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    issue = relationship("ReviewIssue", foreign_keys=[issue_pk])
    user = relationship("User", foreign_keys=[user_id])


class ImmutableReviewRecordError(RuntimeError):
    pass


_FIXED_ISSUE_COLUMNS = {"month", "kind", "auto_key", "category", "checklist_ref", "restricted", "title", "description",
                        "links_json", "created_by_user_id", "created_at"}
_INSERT_ONLY = (ReviewIssueEvent, ReviewNote, ReviewSourceRecord, ReviewStepMark, ReviewIssueFile)


def _refuse_delete(mapper, connection, target):
    raise ImmutableReviewRecordError(f"{type(target).__name__} rows are never deleted.")


def _refuse_update_insert_only(mapper, connection, target):
    raise ImmutableReviewRecordError(f"{type(target).__name__} rows are never edited; add a new entry instead.")


def _refuse_fixed_issue_update(mapper, connection, target):
    state = inspect(target)
    for col in _FIXED_ISSUE_COLUMNS:
        h = state.attrs[col].history
        if h.has_changes():
            raise ImmutableReviewRecordError(f"Review issue column {col!r} is fixed once written.")
    h = state.attrs.issue_id.history
    if h.has_changes() and h.deleted and h.deleted[0] is not None:
        raise ImmutableReviewRecordError("A review issue's id is set once.")


for _model in (ReviewIssue, *_INSERT_ONLY):
    event.listen(_model, "before_delete", _refuse_delete)
for _model in _INSERT_ONLY:
    event.listen(_model, "before_update", _refuse_update_insert_only)
event.listen(ReviewIssue, "before_update", _refuse_fixed_issue_update)
