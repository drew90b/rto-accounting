"""Make human-readable ID columns nullable to support flush-then-set-id pattern.

Revision ID: 003
Revises: 002
Create Date: 2026-03-10

Rationale:
    All human-readable ID columns (customer_id, unit_id, job_id, sale_id, etc.)
    are set in application code AFTER db.flush() so the auto-increment integer
    primary key is available. For example:

        db.add(c)
        db.flush()           # get c.id (auto-increment)
        c.customer_id = f"C-{c.id:04d}"
        db.commit()

    The NOT NULL constraint on these columns was intended to document that the
    field is always populated by the time a transaction commits — but it also
    causes PostgreSQL to reject the INSERT during flush() because no value has
    been provided yet.

    Dropping NOT NULL from these columns allows flush() to succeed. The
    application code always sets the human-readable ID before commit(), so the
    column will never be null in practice after a successful commit.
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

# (table, column, type) tuples for all human-readable ID columns
_COLUMNS = [
    ("customers",      "customer_id",    sa.String(20)),
    ("vendors",        "vendor_id",      sa.String(20)),
    ("units",          "unit_id",        sa.String(20)),
    ("repair_jobs",    "job_id",         sa.String(20)),
    ("sales",          "sale_id",        sa.String(20)),
    ("lease_accounts", "lease_id",       sa.String(20)),
    ("payments",       "payment_id",     sa.String(20)),
    ("transactions",   "transaction_id", sa.String(20)),
    ("documents",      "document_id",    sa.String(20)),
    ("exceptions",     "exception_id",   sa.String(20)),
]


def upgrade():
    for table, column, col_type in _COLUMNS:
        op.alter_column(table, column, existing_type=col_type, nullable=True)


def downgrade():
    for table, column, col_type in _COLUMNS:
        op.alter_column(table, column, existing_type=col_type, nullable=False)
