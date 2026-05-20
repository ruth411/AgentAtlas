"""create claims and evidence tables

Revision ID: 0001_create_claims_and_evidence
Revises:
Create Date: 2026-05-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_create_claims_and_evidence"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_claims",
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("claim_type", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("submitted_by", sa.String(length=128), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=False),
        sa.Column("verification_status", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "claim_type IN ('tool_exists', 'cli_command_exists', 'cli_flag_exists', "
            "'api_endpoint_exists', 'mcp_tool_exists', 'auth_requirement', 'side_effect', "
            "'destructive_action', 'environment_requirement', 'feature_deprecated', "
            "'workflow_step')",
            name="ck_knowledge_claims_claim_type",
        ),
        sa.CheckConstraint(
            "risk_level IN ('none', 'low', 'medium', 'high', 'critical')",
            name="ck_knowledge_claims_risk_level",
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'accepted', 'rejected', 'conflict_detected', "
            "'requires_human_review')",
            name="ck_knowledge_claims_verification_status",
        ),
        sa.PrimaryKeyConstraint("claim_id"),
    )
    op.create_index("ix_knowledge_claims_claim_type", "knowledge_claims", ["claim_type"])
    op.create_index("ix_knowledge_claims_risk_level", "knowledge_claims", ["risk_level"])
    op.create_index("ix_knowledge_claims_submitted_by", "knowledge_claims", ["submitted_by"])
    op.create_index("ix_knowledge_claims_tool_id", "knowledge_claims", ["tool_id"])
    op.create_index(
        "ix_knowledge_claims_verification_status",
        "knowledge_claims",
        ["verification_status"],
    )

    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("evidence_type", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("hash", sa.String(length=256), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trust_level", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "evidence_type IN ('official_docs', 'cli_help_output', 'man_page', "
            "'openapi_schema', 'json_schema', 'graphql_schema', 'mcp_tool_schema', "
            "'source_code', 'package_metadata', 'sandbox_execution', 'release_notes', "
            "'maintainer_review')",
            name="ck_evidence_evidence_type",
        ),
        sa.CheckConstraint(
            "trust_level IN ('low', 'medium', 'high')",
            name="ck_evidence_trust_level",
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["knowledge_claims.claim_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id"),
    )
    op.create_index("ix_evidence_claim_id", "evidence", ["claim_id"])
    op.create_index("ix_evidence_evidence_type", "evidence", ["evidence_type"])
    op.create_index("ix_evidence_hash", "evidence", ["hash"])
    op.create_index("ix_evidence_trust_level", "evidence", ["trust_level"])


def downgrade() -> None:
    op.drop_index("ix_evidence_trust_level", table_name="evidence")
    op.drop_index("ix_evidence_hash", table_name="evidence")
    op.drop_index("ix_evidence_evidence_type", table_name="evidence")
    op.drop_index("ix_evidence_claim_id", table_name="evidence")
    op.drop_table("evidence")
    op.drop_index("ix_knowledge_claims_verification_status", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_tool_id", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_submitted_by", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_risk_level", table_name="knowledge_claims")
    op.drop_index("ix_knowledge_claims_claim_type", table_name="knowledge_claims")
    op.drop_table("knowledge_claims")
