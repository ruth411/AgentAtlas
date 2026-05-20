"""create verification results table

Revision ID: 0002_create_verification_results
Revises: 0001_create_claims_and_evidence
Create Date: 2026-05-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_create_verification_results"
down_revision: str | None = "0001_create_claims_and_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "verification_results",
        sa.Column("verification_id", sa.String(length=128), nullable=False),
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=64), nullable=False),
        sa.Column("verification_status", sa.String(length=64), nullable=False),
        sa.Column("verification_level", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason_codes", sa.Text(), nullable=False),
        sa.Column("reasons", sa.Text(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('accepted', 'rejected', 'pending_more_evidence', 'duplicate', "
            "'conflict_detected', 'requires_human_review')",
            name="ck_verification_results_decision",
        ),
        sa.CheckConstraint(
            "verification_status IN ('pending', 'accepted', 'rejected', 'conflict_detected', "
            "'requires_human_review')",
            name="ck_verification_results_status",
        ),
        sa.CheckConstraint(
            "verification_level IN ('L0_unverified', 'L1_schema_valid', 'L2_source_verified', "
            "'L3_runtime_verified', 'L4_cross_agent_verified', 'L5_human_audited')",
            name="ck_verification_results_level",
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["knowledge_claims.claim_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("verification_id"),
    )
    op.create_index("ix_verification_results_claim_id", "verification_results", ["claim_id"])
    op.create_index("ix_verification_results_decision", "verification_results", ["decision"])
    op.create_index(
        "ix_verification_results_verification_status",
        "verification_results",
        ["verification_status"],
    )
    op.create_index(
        "ix_verification_results_verification_level",
        "verification_results",
        ["verification_level"],
    )
    op.create_index("ix_verification_results_verified_at", "verification_results", ["verified_at"])


def downgrade() -> None:
    op.drop_index("ix_verification_results_verified_at", table_name="verification_results")
    op.drop_index("ix_verification_results_verification_level", table_name="verification_results")
    op.drop_index("ix_verification_results_verification_status", table_name="verification_results")
    op.drop_index("ix_verification_results_decision", table_name="verification_results")
    op.drop_index("ix_verification_results_claim_id", table_name="verification_results")
    op.drop_table("verification_results")
