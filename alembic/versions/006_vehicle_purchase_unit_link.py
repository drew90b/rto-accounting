"""Vehicle purchases create inventory: VIN uniqueness + purchase cost detail.

Revision ID: 006
Revises: 005
Create Date: 2026-09-13

Rationale:
    A vehicle purchase (the 3 "AUCTION - Purchased ..." expense categories)
    now automatically creates the Unit it represents instead of requiring a
    second manual entry in Inventory. Two schema changes support this:

    1. units.vin_serial gets a unique index. VIN is the reliable key for
       preventing duplicate vehicle records (stock numbers are free text and
       can collide across unrelated purchases). A plain unique index already
       permits multiple NULLs in Postgres, so units without a VIN on file
       are unaffected — matches how unit_id/purchase_id/vendor_id etc. are
       already declared unique=True elsewhere in this schema.

    2. purchases gains shipping_cost and other_fees — both nullable, both
       only meaningful for vehicle purchases. Acquisition Cost is computed
       as purchase.amount + shipping_cost + other_fees at the moment the
       Unit is created (see app/services/purchase_service.py); it is not a
       separately-entered total.
"""

from alembic import op
import sqlalchemy as sa

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_units_vin_serial", "units", ["vin_serial"], unique=True)
    op.add_column("purchases", sa.Column("shipping_cost", sa.Numeric(10, 2), nullable=True))
    op.add_column("purchases", sa.Column("other_fees", sa.Numeric(10, 2), nullable=True))


def downgrade():
    op.drop_column("purchases", "other_fees")
    op.drop_column("purchases", "shipping_cost")
    op.drop_index("ix_units_vin_serial", table_name="units")
