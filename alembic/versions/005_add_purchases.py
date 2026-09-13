"""Add purchases table.

Revision ID: 005
Revises: 004
Create Date: 2026-09-12

Rationale:
    The Purchases module replaces the business's Google Purchase Order Form.
    A Purchase is the durable, employee-facing record of what was submitted
    (who bought it, from whom, for how much, in which of the legacy expense
    categories); it always creates a linked Transaction, which remains the
    accounting ledger entry. Vendor is always resolved (found-or-created) and
    linked; Unit is linked only on a clean match against an existing unit's
    ID or VIN — never fabricated.

    purchases
        id                — integer PK
        purchase_id       — human-readable ID (PO-0001), set post-flush; nullable
        purchase_date     — date of the purchase
        purchaser         — free text, "Who Made the Purchase"
        vendor_id         — FK -> vendors.id (always set)
        amount            — dollar amount
        category          — one of the 9 legacy expense-category strings
        site_location     — free text (e.g. "Eunice")
        stock_number      — free text as submitted; never corrupts the Unit table
        unit_id           — FK -> units.id, nullable, best-effort match on stock_number
        mileage           — nullable integer
        description       — free text notes
        review_status     — 'pending' ("Needs Review") or 'reviewed'
        receipt_attached  — denormalized flag, mirrors Transaction.receipt_attached
        transaction_id    — FK -> transactions.id (the created ledger entry)
        entered_by, created_at, updated_at
"""

from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "purchases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("purchase_id", sa.String(20), nullable=True),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("purchaser", sa.String(100), nullable=False),
        sa.Column("vendor_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("site_location", sa.String(100), nullable=True),
        sa.Column("stock_number", sa.String(50), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("mileage", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("receipt_attached", sa.Boolean(), nullable=True, server_default=sa.false()),
        sa.Column("transaction_id", sa.Integer(), nullable=True),
        sa.Column("entered_by", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["vendor_id"], ["vendors.id"]),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_purchases_id", "purchases", ["id"])
    op.create_index("ix_purchases_purchase_id", "purchases", ["purchase_id"], unique=True)
    op.create_index("ix_purchases_vendor_id", "purchases", ["vendor_id"])
    op.create_index("ix_purchases_unit_id", "purchases", ["unit_id"])
    op.create_index("ix_purchases_review_status", "purchases", ["review_status"])


def downgrade():
    op.drop_index("ix_purchases_review_status", table_name="purchases")
    op.drop_index("ix_purchases_unit_id", table_name="purchases")
    op.drop_index("ix_purchases_vendor_id", table_name="purchases")
    op.drop_index("ix_purchases_purchase_id", table_name="purchases")
    op.drop_index("ix_purchases_id", table_name="purchases")
    op.drop_table("purchases")
