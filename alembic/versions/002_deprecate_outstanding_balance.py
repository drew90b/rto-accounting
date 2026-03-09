"""Rename outstanding_balance to outstanding_balance_deprecated on lease_accounts.

Revision ID: 002
Revises: 001
Create Date: 2026-03-08

Rationale:
    outstanding_balance was previously a manually maintained mutable column.
    Remaining balance is now computed at runtime:
        financed_balance - SUM(payments.amount WHERE lease_account_id = lease.id)
    The column is renamed rather than dropped to preserve any historical data that
    may have been entered before this migration was applied.
    Do not read or write outstanding_balance_deprecated from application code.
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "lease_accounts",
        "outstanding_balance",
        new_column_name="outstanding_balance_deprecated",
        existing_type=sa.Numeric(10, 2),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "lease_accounts",
        "outstanding_balance_deprecated",
        new_column_name="outstanding_balance",
        existing_type=sa.Numeric(10, 2),
        existing_nullable=True,
    )
