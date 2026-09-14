"""Add purchases.source_reference for historical-import idempotence.

Revision ID: 007
Revises: 006
Create Date: 2026-09-14

Rationale:
    The one-time historical purchase importer (app/scripts/import_historical_purchases.py)
    needs a way to guarantee it can never create duplicate Purchases if run
    twice. No existing Purchase field combination is a safe natural key —
    the historical CSV has genuine same-day/same-vendor/same-amount repeat
    purchases (e.g. recurring small hardware buys) that a composite key
    would either wrongly treat as duplicates or wrongly let through.

    source_reference is a nullable, unique String set ONLY by the importer
    (e.g. "historical-import:<source_row>"). Every purchase created through
    the normal UI/route leaves it NULL — nothing about native entry changes.
    The unique index is the real guarantee: a second import attempt hitting
    the same reference fails at the database level, not just an
    application-level heuristic.
"""

from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("purchases", sa.Column("source_reference", sa.String(100), nullable=True))
    op.create_index(
        "ix_purchases_source_reference", "purchases", ["source_reference"], unique=True
    )


def downgrade():
    op.drop_index("ix_purchases_source_reference", table_name="purchases")
    op.drop_column("purchases", "source_reference")
