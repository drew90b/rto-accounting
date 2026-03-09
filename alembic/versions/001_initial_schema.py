"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-07

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_id", sa.String(20), unique=True, nullable=False),
        sa.Column("full_name", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(100)),
        sa.Column("address", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "vendors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vendor_id", sa.String(20), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("phone", sa.String(20)),
        sa.Column("email", sa.String(100)),
        sa.Column("address", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.String(20), unique=True, nullable=False),
        sa.Column("unit_type", sa.String(20), nullable=False),
        sa.Column("business_line", sa.String(20), nullable=False),
        sa.Column("vin_serial", sa.String(50)),
        sa.Column("year", sa.Integer()),
        sa.Column("make", sa.String(50)),
        sa.Column("model", sa.String(100)),
        sa.Column("purchase_date", sa.Date()),
        sa.Column("purchase_source", sa.String(100)),
        sa.Column("acquisition_cost", sa.Numeric(10, 2)),
        sa.Column("status", sa.String(30), nullable=False, server_default="acquired"),
        sa.Column("repair_status", sa.String(50)),
        sa.Column("sales_status", sa.String(50)),
        sa.Column("linked_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "repair_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.String(20), unique=True, nullable=False),
        sa.Column("business_line", sa.String(20), nullable=False),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("job_type", sa.String(30), nullable=False),
        sa.Column("open_date", sa.Date(), nullable=False),
        sa.Column("close_date", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("labor_amount", sa.Numeric(10, 2), server_default="0"),
        sa.Column("materials_amount", sa.Numeric(10, 2), server_default="0"),
        sa.Column("total_billed_amount", sa.Numeric(10, 2), server_default="0"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "sales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sale_id", sa.String(20), unique=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id"), nullable=False),
        sa.Column("sale_date", sa.Date(), nullable=False),
        sa.Column("business_line", sa.String(20), nullable=False),
        sa.Column("sale_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("down_payment", sa.Numeric(10, 2), server_default="0"),
        sa.Column("fees", sa.Numeric(10, 2), server_default="0"),
        sa.Column("total_contract_amount", sa.Numeric(10, 2)),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "lease_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lease_id", sa.String(20), unique=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id"), nullable=False),
        sa.Column("deal_date", sa.Date(), nullable=False),
        sa.Column("original_agreed_amount", sa.Numeric(10, 2)),
        sa.Column("down_payment", sa.Numeric(10, 2), server_default="0"),
        sa.Column("financed_balance", sa.Numeric(10, 2)),
        sa.Column("scheduled_payment_amount", sa.Numeric(10, 2)),
        sa.Column("payment_frequency", sa.String(20), server_default="monthly"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("outstanding_balance", sa.Numeric(10, 2)),
        sa.Column("delinquency_status", sa.String(20), server_default="current"),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.String(20), unique=True, nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("lease_account_id", sa.Integer(), sa.ForeignKey("lease_accounts.id"), nullable=True),
        sa.Column("repair_job_id", sa.Integer(), sa.ForeignKey("repair_jobs.id"), nullable=True),
        sa.Column("notes", sa.Text()),
        sa.Column("entered_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("transaction_id", sa.String(20), unique=True, nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("entry_date", sa.Date()),
        sa.Column("transaction_type", sa.String(30), nullable=False),
        sa.Column("business_line", sa.String(20), nullable=False),
        sa.Column("revenue_stream", sa.String(30)),
        sa.Column("vendor_id", sa.Integer(), sa.ForeignKey("vendors.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("unit_id", sa.Integer(), sa.ForeignKey("units.id"), nullable=True),
        sa.Column("repair_job_id", sa.Integer(), sa.ForeignKey("repair_jobs.id"), nullable=True),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=True),
        sa.Column("lease_account_id", sa.Integer(), sa.ForeignKey("lease_accounts.id"), nullable=True),
        sa.Column("category", sa.String(50)),
        sa.Column("payment_method", sa.String(20)),
        sa.Column("receipt_attached", sa.Boolean(), server_default="false"),
        sa.Column("coding_complete", sa.Boolean(), server_default="false"),
        sa.Column("review_status", sa.String(20), server_default="pending"),
        sa.Column("exception_status", sa.String(20), server_default="none"),
        sa.Column("entered_by", sa.String(50)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.String(20), unique=True, nullable=False),
        sa.Column("linked_record_type", sa.String(30), nullable=False),
        sa.Column("linked_record_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(200), nullable=False),
        sa.Column("file_type", sa.String(50)),
        sa.Column("upload_timestamp", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("uploaded_by", sa.String(50)),
        sa.Column("notes", sa.Text()),
    )

    op.create_table(
        "exceptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("exception_id", sa.String(20), unique=True, nullable=False),
        sa.Column("exception_type", sa.String(50), nullable=False),
        sa.Column("linked_record_type", sa.String(30)),
        sa.Column("linked_record_id", sa.Integer()),
        sa.Column("opened_date", sa.Date(), nullable=False),
        sa.Column("owner", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("notes", sa.Text()),
        sa.Column("target_resolution_date", sa.Date()),
        sa.Column("resolution_action", sa.Text()),
        sa.Column("audit_history", sa.Text()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()")),
    )

    # Indexes
    op.create_index("ix_units_status", "units", ["status"])
    op.create_index("ix_units_business_line", "units", ["business_line"])
    op.create_index("ix_transactions_transaction_date", "transactions", ["transaction_date"])
    op.create_index("ix_transactions_business_line", "transactions", ["business_line"])
    op.create_index("ix_transactions_revenue_stream", "transactions", ["revenue_stream"])
    op.create_index("ix_exceptions_status", "exceptions", ["status"])
    op.create_index("ix_lease_accounts_delinquency", "lease_accounts", ["delinquency_status"])


def downgrade() -> None:
    op.drop_table("exceptions")
    op.drop_table("documents")
    op.drop_table("transactions")
    op.drop_table("payments")
    op.drop_table("lease_accounts")
    op.drop_table("sales")
    op.drop_table("repair_jobs")
    op.drop_table("units")
    op.drop_table("vendors")
    op.drop_table("customers")
