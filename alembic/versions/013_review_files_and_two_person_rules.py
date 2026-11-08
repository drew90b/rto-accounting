"""Add review issue files; widen review_source_records.status.

Revision ID: 013
Revises: 012
Create Date: 2026-11-08

Rationale:
    Monthly Review refinements (docs/specs/cash-financial-control-spec.md, section 20):
      * review_issue_files: supporting files that an issue's owner (for example Belinda) or an administrator adds to an
        issue, stored outside the shared Documents list and served only through permission-checked routes.
      * review_source_records.status widened so a "not required" proposal can be confirmed by a second administrator
        ("not_required_confirmed"). A proposal alone never counts.

    Additive apart from the column widening. Downgrade removes the files table (and the confirmation rows it could not
    represent in the narrower column); stored files on disk are not touched.
"""

from alembic import op
import sqlalchemy as sa

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("review_source_records", "status", existing_type=sa.String(15), type_=sa.String(30), existing_nullable=False)
    op.create_table(
        "review_issue_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_pk", sa.Integer(), sa.ForeignKey("review_issues.id"), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("stored_path", sa.String(500), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_review_issue_files_id", "review_issue_files", ["id"])
    op.create_index("ix_review_issue_files_issue_pk", "review_issue_files", ["issue_pk"])


def downgrade() -> None:
    op.drop_table("review_issue_files")
    op.execute("DELETE FROM review_source_records WHERE length(status) > 15")
    op.alter_column("review_source_records", "status", existing_type=sa.String(30), type_=sa.String(15), existing_nullable=False)
