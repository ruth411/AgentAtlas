"""extend claim_type and artifact_type CHECKs for JSON Schema ingestion

Revision ID: 0011_extend_checks_for_json_schema
Revises: 0010_extend_artifact_type_for_openapi
Create Date: 2026-05-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_extend_checks_for_json_schema"
down_revision: str | None = "0010_extend_artifact_type_for_openapi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CLAIM_TYPES_BEFORE = (
    "'tool_exists', 'cli_command_exists', 'cli_flag_exists', "
    "'api_endpoint_exists', 'mcp_tool_exists', 'auth_requirement', 'side_effect', "
    "'destructive_action', 'environment_requirement', 'feature_deprecated', "
    "'workflow_step'"
)
_CLAIM_TYPES_AFTER = _CLAIM_TYPES_BEFORE + ", 'config_field_exists'"


def upgrade() -> None:
    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.drop_constraint("ck_knowledge_claims_claim_type", type_="check")
        batch_op.create_check_constraint(
            "ck_knowledge_claims_claim_type",
            f"claim_type IN ({_CLAIM_TYPES_AFTER})",
        )
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output', 'docs_content', 'openapi_spec', 'json_schema_doc')",
        )


def downgrade() -> None:
    with op.batch_alter_table("raw_ingestion_artifacts") as batch_op:
        batch_op.drop_constraint("ck_raw_ingestion_artifacts_type", type_="check")
        batch_op.create_check_constraint(
            "ck_raw_ingestion_artifacts_type",
            "artifact_type IN ('cli_output', 'docs_content', 'openapi_spec')",
        )
    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.drop_constraint("ck_knowledge_claims_claim_type", type_="check")
        batch_op.create_check_constraint(
            "ck_knowledge_claims_claim_type",
            f"claim_type IN ({_CLAIM_TYPES_BEFORE})",
        )
