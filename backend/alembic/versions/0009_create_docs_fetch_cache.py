"""create docs fetch cache table and extend artifact_type constraint

Revision ID: 0009_create_docs_fetch_cache
Revises: 0008_add_in_progress_ingestion_status
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_create_docs_fetch_cache"
down_revision: str | None = "0008_add_in_progress_ingestion_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "docs_fetch_cache",
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("etag", sa.String(length=512), nullable=True),
        sa.Column("last_modified", sa.String(length=128), nullable=True),
        sa.Column("body_hash", sa.String(length=128), nullable=False),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_artifact_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("url"),
    )
    op.create_index(
        "ix_docs_fetch_cache_last_fetched_at",
        "docs_fetch_cache",
        ["last_fetched_at"],
    )
    op.create_index(
        "ix_docs_fetch_cache_body_hash",
        "docs_fetch_cache",
        ["body_hash"],
    )

    # Extend raw_ingestion_artifacts.artifact_type to allow docs_content.
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output', 'docs_content')",
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output')",
        )

    op.drop_index("ix_docs_fetch_cache_body_hash", table_name="docs_fetch_cache")
    op.drop_index("ix_docs_fetch_cache_last_fetched_at", table_name="docs_fetch_cache")
    op.drop_table("docs_fetch_cache")
