"""extend raw_ingestion_artifacts artifact_type to include sandbox_execution_log

Revision ID: 0014_extend_artifact_type_for_sandbox
Revises: 0013_extend_artifact_type_for_mcp
Create Date: 2026-05-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_extend_artifact_type_for_sandbox"
down_revision: str | None = "0013_extend_artifact_type_for_mcp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output', 'docs_content', 'openapi_spec', "
            "'json_schema_doc', 'graphql_sdl', 'mcp_tool_list', "
            "'sandbox_execution_log')",
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output', 'docs_content', 'openapi_spec', "
            "'json_schema_doc', 'graphql_sdl', 'mcp_tool_list')",
        )
