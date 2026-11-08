"""Add the Opening Snapshot tables.

Revision ID: 010
Revises: 009
Create Date: 2026-11-05

Rationale:
    The Opening Snapshot keeps what was reported for a starting date (Workday 1)
    exactly as reported, with dated clarifications and evidence added beside it.
    docs/specs/cash-financial-control-spec.md, section 18.

    Additive only: four new tables, no change to any existing table or enum,
    nothing backfilled, no figures seeded. Enum-like columns are varchar
    (native_enum=False), so there is no Postgres ALTER TYPE to run or undo.

    Evidence files live under storage/receipts/opening_snapshot/ and are not
    touched by this migration or its downgrade.

    Downgrade drops the four tables and therefore everything recorded in them.
    To undo a bad rollout after real figures were entered, restore from backup
    instead (the old code runs fine against the new schema).
"""

from alembic import op
import sqlalchemy as sa

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "opening_snapshots",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("snapshot_id", sa.String(20), nullable=True, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("as_of_date", sa.Date, nullable=False),
        sa.Column("label", sa.String(50), nullable=False),
        sa.Column("cutoff_note", sa.Text, nullable=False),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_opening_snapshots_id", "opening_snapshots", ["id"])

    op.create_table(
        "opening_figures",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("figure_id", sa.String(20), nullable=True, unique=True),
        sa.Column("snapshot_id", sa.Integer, sa.ForeignKey("opening_snapshots.id"), nullable=False),
        sa.Column("section", sa.String(20), nullable=False),
        sa.Column("label", sa.String(150), nullable=False),
        sa.Column("reported_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("reported_count", sa.Integer, nullable=True),
        sa.Column("reported_statement", sa.Text, nullable=False),
        sa.Column("reported_by_name", sa.String(100), nullable=False),
        sa.Column("reported_via", sa.String(100), nullable=False),
        sa.Column("reported_on", sa.Date, nullable=True),
        sa.Column("reported_time_note", sa.String(100), nullable=True),
        sa.Column("reported_cutoff", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("basis_note", sa.String(200), nullable=True),
        sa.Column("pending_need", sa.String(300), nullable=True),
        sa.Column("due_note", sa.String(200), nullable=True),
        sa.Column("display_order", sa.Integer, nullable=False),
        sa.Column("recorded_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime, nullable=False),
        sa.Column("withdrawn_at", sa.DateTime, nullable=True),
        sa.Column("withdrawn_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("withdrawn_reason", sa.Text, nullable=True),
    )
    op.create_index("ix_opening_figures_id", "opening_figures", ["id"])
    op.create_index("ix_opening_figures_snapshot_id", "opening_figures", ["snapshot_id"])

    op.create_table(
        "opening_figure_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("figure_id", sa.Integer, sa.ForeignKey("opening_figures.id"), nullable=False),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("refers_to_date", sa.Date, nullable=True),
        sa.Column("cutoff_update", sa.String(200), nullable=True),
        sa.Column("basis_update", sa.String(200), nullable=True),
        sa.Column("basis_confirmed", sa.Boolean, nullable=True),
        sa.Column("corrects_entry_id", sa.Integer, sa.ForeignKey("opening_figure_entries.id"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_opening_figure_entries_id", "opening_figure_entries", ["id"])
    op.create_index("ix_opening_figure_entries_figure_id", "opening_figure_entries", ["figure_id"])

    op.create_table(
        "opening_figure_evidence",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("figure_id", sa.Integer, sa.ForeignKey("opening_figures.id"), nullable=False),
        sa.Column("entry_id", sa.Integer, sa.ForeignKey("opening_figure_entries.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("original_filename", sa.String(200), nullable=False),
        sa.Column("stored_path", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("no_pay_detail_confirmed", sa.Boolean, nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_opening_figure_evidence_id", "opening_figure_evidence", ["id"])
    op.create_index("ix_opening_figure_evidence_figure_id", "opening_figure_evidence", ["figure_id"])


def downgrade():
    op.drop_index("ix_opening_figure_evidence_figure_id", table_name="opening_figure_evidence")
    op.drop_index("ix_opening_figure_evidence_id", table_name="opening_figure_evidence")
    op.drop_table("opening_figure_evidence")
    op.drop_index("ix_opening_figure_entries_figure_id", table_name="opening_figure_entries")
    op.drop_index("ix_opening_figure_entries_id", table_name="opening_figure_entries")
    op.drop_table("opening_figure_entries")
    op.drop_index("ix_opening_figures_snapshot_id", table_name="opening_figures")
    op.drop_index("ix_opening_figures_id", table_name="opening_figures")
    op.drop_table("opening_figures")
    op.drop_index("ix_opening_snapshots_id", table_name="opening_snapshots")
    op.drop_table("opening_snapshots")
