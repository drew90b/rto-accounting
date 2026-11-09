"""Add the Frazer export helper tables.

Revision ID: fh01
Revises: vr01
Create Date: 2026-11-09

Rationale:
    A user-started helper uploads the three configured Frazer exports through the existing intake service.
    Three small additive tables: revocable upload tokens (hash only), one row per helper run with per-file results,
    and the approved column layout of each saved export. No existing table or column changes; nothing is backfilled;
    nothing here holds customer data.

    Downgrade drops the three tables (tokens stop working, run history is lost; uploads and their rows are untouched).
    Re-point down_revision if the base head changes.
"""
from alembic import op
import sqlalchemy as sa

revision = "fh01"
down_revision = "vr01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "frazer_helper_tokens",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("token_prefix", sa.String(12), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_frazer_helper_tokens_id", "frazer_helper_tokens", ["id"])
    op.create_table(
        "frazer_helper_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.String(20), nullable=True, unique=True),
        sa.Column("token_id", sa.Integer, sa.ForeignKey("frazer_helper_tokens.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("lease_expires_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("retry_of_run_id", sa.Integer, sa.ForeignKey("frazer_helper_runs.id"), nullable=True),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("params_json", sa.JSON, nullable=True),
        sa.Column("results_json", sa.JSON, nullable=False),
        sa.Column("sync_json", sa.JSON, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
    )
    op.create_index("ix_frazer_helper_runs_id", "frazer_helper_runs", ["id"])
    op.create_index("uq_one_running_helper_run", "frazer_helper_runs", ["status"], unique=True,
                    postgresql_where=sa.text("status = 'running'"), sqlite_where=sa.text("status = 'running'"))
    op.create_table(
        "frazer_helper_layouts",
        sa.Column("export_type", sa.String(30), primary_key=True),
        sa.Column("headers_json", sa.JSON, nullable=False),
        sa.Column("source_upload_id", sa.Integer, sa.ForeignKey("frazer_uploads.id"), nullable=True),
        sa.Column("set_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("set_at", sa.DateTime, nullable=False),
    )


def downgrade():
    op.drop_table("frazer_helper_layouts")
    op.drop_index("uq_one_running_helper_run", table_name="frazer_helper_runs")
    op.drop_index("ix_frazer_helper_runs_id", table_name="frazer_helper_runs")
    op.drop_table("frazer_helper_runs")
    op.drop_index("ix_frazer_helper_tokens_id", table_name="frazer_helper_tokens")
    op.drop_table("frazer_helper_tokens")
