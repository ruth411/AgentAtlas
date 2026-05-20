"""extend raw_ingestion_artifacts artifact_type to include openapi_spec

Revision ID: 0010_extend_artifact_type_for_openapi
Revises: 0009_create_docs_fetch_cache
Create Date: 2026-05-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_extend_artifact_type_for_openapi"
down_revision: str | None = "0009_create_docs_fetch_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output', 'docs_content', 'openapi_spec')",
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output', 'docs_content')",
        )
