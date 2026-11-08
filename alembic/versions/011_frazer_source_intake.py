"""Add Frazer source intake tables.

Revision ID: 011
Revises: 010
Create Date: 2026-11-08

Rationale:
    Frazer export intake and import preview (docs/specs/cash-financial-control-spec.md, section 19).
    Four new tables hold uploaded Frazer files and what was read from them: SOURCE EVIDENCE only.
    Nothing here touches purchases, units, payments, transactions, customers, vendors or documents.

    Additive only: no existing table or column changes, nothing is backfilled.

    Revision numbering: in the combined monthly-review branch this follows 010 (Opening Snapshot). Standing alone on main it followed 009. The Opening Snapshot branch adds a
    migration after 009 (revision 010). Whichever merges second must re-point its down_revision (or
    add an `alembic merge` revision) so there is a single head; see docs/frazer_intake.md.

    Downgrade drops the four tables and therefore every uploaded record in them. The original
    files under storage/receipts/frazer_intake/ are not touched. To undo a bad rollout after real
    uploads, restore from backup instead.
"""

from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "frazer_uploads",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("upload_id", sa.String(20), nullable=True, unique=True),
        sa.Column("export_type", sa.String(30), nullable=False),
        sa.Column("layout", sa.String(60), nullable=True),
        sa.Column("parser_version", sa.String(30), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("stored_path", sa.String(500), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("encoding", sa.String(20), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("declared_export_date", sa.Date, nullable=True),
        sa.Column("declared_coverage_start", sa.Date, nullable=True),
        sa.Column("declared_coverage_end", sa.Date, nullable=True),
        sa.Column("observed_coverage_start", sa.Date, nullable=True),
        sa.Column("observed_coverage_end", sa.Date, nullable=True),
        sa.Column("actual_cutoff", sa.Text, nullable=False),
        sa.Column("row_count", sa.Integer, nullable=False),
        sa.Column("headers_json", sa.JSON, nullable=True),
        sa.Column("summary_json", sa.JSON, nullable=True),
        sa.Column("uploaded_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("uploaded_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_frazer_uploads_id", "frazer_uploads", ["id"])
    op.create_index("ix_frazer_uploads_sha256", "frazer_uploads", ["sha256"])

    op.create_table(
        "frazer_source_rows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("upload_id", sa.Integer, sa.ForeignKey("frazer_uploads.id"), nullable=False),
        sa.Column("row_number", sa.Integer, nullable=False),
        sa.Column("record_type", sa.String(30), nullable=False),
        sa.Column("identity", sa.String(100), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("entry_class", sa.String(30), nullable=True),
        sa.Column("raw_json", sa.JSON, nullable=False),
        sa.Column("fields_json", sa.JSON, nullable=False),
    )
    op.create_index("ix_frazer_source_rows_id", "frazer_source_rows", ["id"])
    op.create_index("ix_frazer_source_rows_upload_id", "frazer_source_rows", ["upload_id"])
    op.create_index("ix_frazer_source_rows_identity", "frazer_source_rows", ["identity"])

    op.create_table(
        "frazer_upload_exceptions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("upload_id", sa.Integer, sa.ForeignKey("frazer_uploads.id"), nullable=False),
        sa.Column("row_number", sa.Integer, nullable=True),
        sa.Column("identity", sa.String(100), nullable=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("field", sa.String(60), nullable=True),
        sa.Column("message", sa.Text, nullable=False),
    )
    op.create_index("ix_frazer_upload_exceptions_id", "frazer_upload_exceptions", ["id"])
    op.create_index("ix_frazer_upload_exceptions_upload_id", "frazer_upload_exceptions", ["upload_id"])

    op.create_table(
        "frazer_upload_notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("upload_id", sa.Integer, sa.ForeignKey("frazer_uploads.id"), nullable=False),
        sa.Column("note", sa.Text, nullable=False),
        sa.Column("actual_cutoff", sa.Text, nullable=True),
        sa.Column("created_by_user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_frazer_upload_notes_id", "frazer_upload_notes", ["id"])
    op.create_index("ix_frazer_upload_notes_upload_id", "frazer_upload_notes", ["upload_id"])


def downgrade():
    op.drop_index("ix_frazer_upload_notes_upload_id", table_name="frazer_upload_notes")
    op.drop_index("ix_frazer_upload_notes_id", table_name="frazer_upload_notes")
    op.drop_table("frazer_upload_notes")
    op.drop_index("ix_frazer_upload_exceptions_upload_id", table_name="frazer_upload_exceptions")
    op.drop_index("ix_frazer_upload_exceptions_id", table_name="frazer_upload_exceptions")
    op.drop_table("frazer_upload_exceptions")
    op.drop_index("ix_frazer_source_rows_identity", table_name="frazer_source_rows")
    op.drop_index("ix_frazer_source_rows_upload_id", table_name="frazer_source_rows")
    op.drop_index("ix_frazer_source_rows_id", table_name="frazer_source_rows")
    op.drop_table("frazer_source_rows")
    op.drop_index("ix_frazer_uploads_sha256", table_name="frazer_uploads")
    op.drop_index("ix_frazer_uploads_id", table_name="frazer_uploads")
    op.drop_table("frazer_uploads")
