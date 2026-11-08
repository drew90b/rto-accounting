"""Add the vehicle reconciliation tables (vr_*).

Revision ID: vr01
Revises: 009
Create Date: 2026-11-08

Rationale:
    Source records, reconciliation questions, decisions and their history for
    docs/specs/vehicle-reconciliation-spec.md. Additive only; no operational
    table is touched.

Numbering note (read before merging):
    Other unmerged branches carry migrations 010-013 on top of 009. This branch
    deliberately does NOT claim a number. Whichever branch merges later must set
    its down_revision to the then-current single head (for this file: edit the
    `down_revision` line below), and `alembic heads` must show one head.

    Downgrade drops these tables and so destroys every question, decision and
    event recorded since. To undo a bad rollout, restore from backup.
"""

from alembic import op
import sqlalchemy as sa

revision = "vr01"
down_revision = "009"   # <- re-point at the current head when merging after 010..013
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vr_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("source_date", sa.Date(), nullable=False),
        sa.Column("cutoff_note", sa.String(200), nullable=False, server_default="Unknown"),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_sha256", sa.String(64), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loaded_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("loaded_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("source_type", "file_sha256", name="uq_vr_snapshot_file"),
    )
    op.create_index("ix_vr_snapshots_id", "vr_snapshots", ["id"])
    op.create_index("ix_vr_snapshots_source_type", "vr_snapshots", ["source_type"])

    op.create_table(
        "vr_source_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), sa.ForeignKey("vr_snapshots.id"), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_key", sa.String(120), nullable=False),
        sa.Column("stock_number", sa.String(50), nullable=True),
        sa.Column("vin", sa.String(50), nullable=True),
        sa.Column("year", sa.String(10), nullable=True),
        sa.Column("make", sa.String(60), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("reported_status", sa.String(60), nullable=True),
        sa.Column("reported_status_date", sa.Date(), nullable=True),
        sa.Column("reported_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("fields_json", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("snapshot_id", "source_key", name="uq_vr_record_per_snapshot"),
    )
    for col in ("id", "snapshot_id", "source_type", "stock_number", "vin"):
        op.create_index(f"ix_vr_source_records_{col}", "vr_source_records", [col])

    op.create_table(
        "vr_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ref", sa.String(20), nullable=True, unique=True),
        sa.Column("question_key", sa.String(200), nullable=False, unique=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("why", sa.Text(), nullable=False),
        sa.Column("subject_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("choices_json", sa.JSON(), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(15), nullable=False, server_default="open"),
        sa.Column("needs_someone_else", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_decision_id", sa.Integer(), nullable=True),
        sa.Column("withdrawn_reason", sa.String(300), nullable=True),
        sa.Column("raised_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    for col in ("id", "kind", "status", "owner_user_id"):
        op.create_index(f"ix_vr_questions_{col}", "vr_questions", [col])

    op.create_table(
        "vr_decisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("vr_questions.id"), nullable=False),
        sa.Column("choice_key", sa.String(60), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("effect_json", sa.JSON(), nullable=False),
        sa.Column("needs_approval", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("state", sa.String(12), nullable=False, server_default="proposed"),
        sa.Column("answered_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("answered_at", sa.DateTime(), nullable=False),
        sa.Column("decided_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("supersedes_id", sa.Integer(), sa.ForeignKey("vr_decisions.id"), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("vr_decisions.id"), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
    )
    for col in ("id", "question_id", "state"):
        op.create_index(f"ix_vr_decisions_{col}", "vr_decisions", [col])

    op.create_table(
        "vr_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("question_id", sa.Integer(), sa.ForeignKey("vr_questions.id"), nullable=True),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data_json", sa.JSON(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_vr_events_id", "vr_events", ["id"])
    op.create_index("ix_vr_events_question_id", "vr_events", ["question_id"])


def downgrade() -> None:
    for t in ("vr_events", "vr_decisions", "vr_questions", "vr_source_records", "vr_snapshots"):
        op.drop_table(t)
