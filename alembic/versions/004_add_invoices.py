"""Add invoices and invoice_items tables.

Revision ID: 004
Revises: 003
Create Date: 2026-03-12

Rationale:
    Invoices are the persisted documentary proof that a revenue event occurred.
    Every sale, closed customer repair job, and RTO payment automatically
    generates an invoice record.

    Two tables are added:

    invoices
        id                  — integer PK
        invoice_number      — human-readable ID (INV-00001), set post-flush; nullable
        invoice_type        — 'sale', 'repair', 'rto_payment'
        status              — 'open', 'paid'
        customer_id         — FK → customers.id
        sale_id             — FK → sales.id (nullable)
        repair_job_id       — FK → repair_jobs.id (nullable)
        lease_account_id    — FK → lease_accounts.id (nullable)
        payment_id          — FK → payments.id (nullable; links RTO receipt to payment)
        invoice_date        — date of the business event
        subtotal, tax_rate, tax_amount, total — financial totals
        amount_paid, balance — running payment state
        notes, created_at, updated_at

    invoice_items
        id, invoice_id, description, quantity, unit_price, line_total, sort_order
"""

from alembic import op
import sqlalchemy as sa


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_number", sa.String(20), nullable=True),
        sa.Column("invoice_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("sale_id", sa.Integer(), nullable=True),
        sa.Column("repair_job_id", sa.Integer(), nullable=True),
        sa.Column("lease_account_id", sa.Integer(), nullable=True),
        sa.Column("payment_id", sa.Integer(), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("subtotal", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("tax_rate", sa.Numeric(5, 4), nullable=False, server_default="0"),
        sa.Column("tax_amount", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("total", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("amount_paid", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("balance", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["sale_id"], ["sales.id"]),
        sa.ForeignKeyConstraint(["repair_job_id"], ["repair_jobs.id"]),
        sa.ForeignKeyConstraint(["lease_account_id"], ["lease_accounts.id"]),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoices_id", "invoices", ["id"])
    op.create_index("ix_invoices_invoice_number", "invoices", ["invoice_number"], unique=True)
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"])
    op.create_index("ix_invoices_status", "invoices", ["status"])

    op.create_table(
        "invoice_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("quantity", sa.Numeric(10, 3), nullable=False, server_default="1"),
        sa.Column("unit_price", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("line_total", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=True, server_default="0"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoice_items_id", "invoice_items", ["id"])
    op.create_index("ix_invoice_items_invoice_id", "invoice_items", ["invoice_id"])


def downgrade():
    op.drop_index("ix_invoice_items_invoice_id", table_name="invoice_items")
    op.drop_index("ix_invoice_items_id", table_name="invoice_items")
    op.drop_table("invoice_items")

    op.drop_index("ix_invoices_status", table_name="invoices")
    op.drop_index("ix_invoices_customer_id", table_name="invoices")
    op.drop_index("ix_invoices_invoice_number", table_name="invoices")
    op.drop_index("ix_invoices_id", table_name="invoices")
    op.drop_table("invoices")
