"""Add Monthly Review workspace tables.

Revision ID: 012
Revises: 011
Create Date: 2026-11-08

Rationale:
    Monthly Review (docs/specs/cash-financial-control-spec.md, section 20): issues with owners and history,
    owner review notes, attestations, and close-step marks that a source record exists outside the app. They organize work;
    they hold no financial source data. Additive only: no existing table or column changes.

    Chain in the combined monthly-review branch: 009 -> 010 (Opening Snapshot) -> 011 (Frazer intake) -> 012.

    Downgrade drops these five tables and every issue, note and attestation in them.
"""

from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_issues",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_id", sa.String(20), nullable=True, unique=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("auto_key", sa.String(120), nullable=True),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("checklist_ref", sa.String(10), nullable=True),
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("links_json", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("due_is_proposed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("evidence_updated_at", sa.DateTime(), nullable=True),
        sa.Column("auto_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("month", "auto_key", name="uq_review_issue_month_auto_key"),
    )
    op.create_index("ix_review_issues_id", "review_issues", ["id"])
    op.create_index("ix_review_issues_month", "review_issues", ["month"])

    op.create_table(
        "review_issue_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_pk", sa.Integer(), sa.ForeignKey("review_issues.id"), nullable=False),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("from_value", sa.String(200), nullable=True),
        sa.Column("to_value", sa.String(200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_issue_events_id", "review_issue_events", ["id"])
    op.create_index("ix_review_issue_events_issue_pk", "review_issue_events", ["issue_pk"])

    op.create_table(
        "review_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("note_type", sa.String(15), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("corrects_note_id", sa.Integer(), sa.ForeignKey("review_notes.id"), nullable=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_notes_id", "review_notes", ["id"])
    op.create_index("ix_review_notes_month", "review_notes", ["month"])

    op.create_table(
        "review_source_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("source_key", sa.String(30), nullable=False),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("period_label", sa.String(60), nullable=False),
        sa.Column("coverage_start", sa.Date(), nullable=True),
        sa.Column("coverage_end", sa.Date(), nullable=True),
        sa.Column("where_kept", sa.String(200), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_source_records_id", "review_source_records", ["id"])
    op.create_index("ix_review_source_records_month", "review_source_records", ["month"])


    op.create_table(
        "review_step_marks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("month", sa.Date(), nullable=False),
        sa.Column("step_key", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(15), nullable=False),
        sa.Column("action", sa.String(5), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_step_marks_id", "review_step_marks", ["id"])
    op.create_index("ix_review_step_marks_month", "review_step_marks", ["month"])


def downgrade() -> None:
    op.drop_table("review_step_marks")
    op.drop_table("review_source_records")
    op.drop_table("review_notes")
    op.drop_table("review_issue_events")
    op.drop_table("review_issues")
