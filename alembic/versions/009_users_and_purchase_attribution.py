"""Add users, purchase attribution columns, and purchase_events.

Revision ID: 009
Revises: 008
Create Date: 2026-10-23

Rationale:
    Individual sign-in, and a record of who entered and who reviewed each
    purchase, separate from purchases.purchaser (who made the purchase).

    Additive only. Nothing is backfilled: the existing purchases keep their
    typed entered_by text and their review_status, and the new attribution
    columns stay NULL because the real entering/reviewing user was never
    recorded and must not be guessed.

    purchases.created_unit records which purchase established a unit's
    acquisition cost; see app/services/purchase_service.py.

    Downgrade drops these tables and columns and therefore destroys all
    attribution recorded since. To undo a bad rollout, restore from backup
    (the old code runs fine against the new schema).
"""

from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String(50), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("session_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime, nullable=True),
        sa.Column("last_login_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_users_id", "users", ["id"])

    op.add_column("purchases", sa.Column("entered_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True))
    op.add_column("purchases", sa.Column("reviewed_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=True))
    op.add_column("purchases", sa.Column("reviewed_at", sa.DateTime, nullable=True))
    # Provenance of a unit's acquisition cost: true only for a purchase that created
    # its unit. Existing rows get false ("unknown"), not a guess, so editing them can
    # never overwrite a unit's cost.
    op.add_column("purchases", sa.Column("created_unit", sa.Boolean, nullable=False, server_default=sa.false()))

    op.create_table(
        "purchase_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("purchase_id", sa.Integer, sa.ForeignKey("purchases.id"), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("changes", sa.JSON, nullable=True),
        sa.Column("note", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_purchase_events_id", "purchase_events", ["id"])
    op.create_index("ix_purchase_events_purchase_id", "purchase_events", ["purchase_id"])


def downgrade():
    op.drop_index("ix_purchase_events_purchase_id", table_name="purchase_events")
    op.drop_index("ix_purchase_events_id", table_name="purchase_events")
    op.drop_table("purchase_events")
    op.drop_column("purchases", "created_unit")
    op.drop_column("purchases", "reviewed_at")
    op.drop_column("purchases", "reviewed_by_user_id")
    op.drop_column("purchases", "entered_by_user_id")
    op.drop_index("ix_users_id", table_name="users")
    op.drop_table("users")
