"""Add transactions.payment_source for general-expense / purchase tracking.

Revision ID: 008
Revises: 007
Create Date: 2026-10-23

Rationale:
    Staff need to record which card, account, cash source, or person paid
    for a purchase or general expense. The existing payment_method enum
    (cash/check/card/transfer/other) cannot say which card or whose money.

    payment_source is a nullable free-text String. It is NOT backfilled:
    historical rows stay NULL because the real source was never recorded and
    must not be guessed. Required-ness is enforced by the Purchases form for
    new entries only, not by the schema.

    The general_expense_* purchase categories and the shared / unallocated
    business lines are new enum values. Enums are stored as plain strings
    with no check constraint (see docs/progress.md decisions log), so they
    need no schema change.
"""

from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("transactions", sa.Column("payment_source", sa.String(100), nullable=True))


def downgrade():
    op.drop_column("transactions", "payment_source")
