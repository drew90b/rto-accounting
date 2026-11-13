"""Add reviewer decisions about Frazer entries that a later export no longer lists.

Revision ID: fh02
Revises: fh01
Create Date: 2026-11-13

Rationale:
    One additive, append-only table. A reviewer records whether Monthly Review should keep or exclude, in its totals, a Frazer
    transaction entry that an earlier upload holds and a later export covering its date does not list (docs/specs/frazer-export-helper-spec.md
    s7, REQ-FX-18/19). The entry and its upload are never changed; the table holds the decision, its reason, who and when, and what
    the reviewer was shown. Nothing is backfilled and no existing table changes.

    Downgrade drops the table. The decisions are lost, and Monthly Review (without this code) counts such entries again.
    Re-point down_revision if the base head changes.
"""
from alembic import op
import sqlalchemy as sa

revision = "fh02"
down_revision = "fh01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "review_entry_decisions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("identity", sa.String(40), nullable=False),
        sa.Column("choice", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("last_held_upload_id", sa.Integer, sa.ForeignKey("frazer_uploads.id"), nullable=False),
        sa.Column("held_in_json", sa.JSON, nullable=False),
        sa.Column("not_listed_in_json", sa.JSON, nullable=False),
        sa.Column("entry_date", sa.Date, nullable=False),
        sa.Column("amount", sa.String(20), nullable=False),
        sa.Column("description", sa.String(120), nullable=False),
        sa.Column("entry_class", sa.String(30), nullable=False),
        sa.Column("basis", sa.Text, nullable=False),
        sa.CheckConstraint("choice in ('keep', 'exclude')", name="ck_review_entry_decision_choice"),
    )
    op.create_index("ix_review_entry_decisions_id", "review_entry_decisions", ["id"])
    op.create_index("ix_review_entry_decisions_identity", "review_entry_decisions", ["identity"])


def downgrade():
    op.drop_index("ix_review_entry_decisions_identity", table_name="review_entry_decisions")
    op.drop_index("ix_review_entry_decisions_id", table_name="review_entry_decisions")
    op.drop_table("review_entry_decisions")
