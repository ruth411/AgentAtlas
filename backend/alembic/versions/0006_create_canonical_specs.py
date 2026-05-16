"""create canonical spec tables

Revision ID: 0006_create_canonical_specs
Revises: 0005_add_classified_risk_to_claims
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_create_canonical_specs"
down_revision: str | None = "0005_add_classified_risk_to_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_VERIFICATION_LEVEL_VALUES = (
    "'L0_unverified', 'L1_schema_valid', 'L2_source_verified', "
    "'L3_runtime_verified', 'L4_cross_agent_verified', 'L5_human_audited'"
)


def upgrade() -> None:
    op.create_table(
        "canonical_tool_specs",
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("spec_hash", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("compiled_by", sa.String(length=128), nullable=False),
        sa.Column("source_claim_ids_json", sa.Text(), nullable=False),
        sa.Column("verification_level", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            f"verification_level IN ({_VERIFICATION_LEVEL_VALUES})",
            name="ck_canonical_tool_specs_verification_level",
        ),
        sa.PrimaryKeyConstraint("tool_id"),
    )
    op.create_index("ix_canonical_tool_specs_spec_hash", "canonical_tool_specs", ["spec_hash"])
    op.create_index("ix_canonical_tool_specs_content_hash", "canonical_tool_specs", ["content_hash"])
    op.create_index(
        "ix_canonical_tool_specs_compiled_at",
        "canonical_tool_specs",
        ["compiled_at"],
    )
    op.create_index(
        "ix_canonical_tool_specs_compiled_by",
        "canonical_tool_specs",
        ["compiled_by"],
    )
    op.create_index(
        "ix_canonical_tool_specs_verification_level",
        "canonical_tool_specs",
        ["verification_level"],
    )

    op.create_table(
        "canonical_workflow_specs",
        sa.Column("workflow_id", sa.String(length=128), nullable=False),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("spec_hash", sa.String(length=128), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("compiled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("compiled_by", sa.String(length=128), nullable=False),
        sa.Column("source_claim_ids_json", sa.Text(), nullable=False),
        sa.Column("verification_level", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            f"verification_level IN ({_VERIFICATION_LEVEL_VALUES})",
            name="ck_canonical_workflow_specs_verification_level",
        ),
        sa.PrimaryKeyConstraint("workflow_id"),
    )
    op.create_index(
        "ix_canonical_workflow_specs_spec_hash",
        "canonical_workflow_specs",
        ["spec_hash"],
    )
    op.create_index(
        "ix_canonical_workflow_specs_content_hash",
        "canonical_workflow_specs",
        ["content_hash"],
    )
    op.create_index(
        "ix_canonical_workflow_specs_compiled_at",
        "canonical_workflow_specs",
        ["compiled_at"],
    )
    op.create_index(
        "ix_canonical_workflow_specs_compiled_by",
        "canonical_workflow_specs",
        ["compiled_by"],
    )
    op.create_index(
        "ix_canonical_workflow_specs_verification_level",
        "canonical_workflow_specs",
        ["verification_level"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_canonical_workflow_specs_verification_level",
        table_name="canonical_workflow_specs",
    )
    op.drop_index("ix_canonical_workflow_specs_compiled_by", table_name="canonical_workflow_specs")
    op.drop_index("ix_canonical_workflow_specs_compiled_at", table_name="canonical_workflow_specs")
    op.drop_index("ix_canonical_workflow_specs_content_hash", table_name="canonical_workflow_specs")
    op.drop_index("ix_canonical_workflow_specs_spec_hash", table_name="canonical_workflow_specs")
    op.drop_table("canonical_workflow_specs")

    op.drop_index(
        "ix_canonical_tool_specs_verification_level",
        table_name="canonical_tool_specs",
    )
    op.drop_index("ix_canonical_tool_specs_compiled_by", table_name="canonical_tool_specs")
    op.drop_index("ix_canonical_tool_specs_compiled_at", table_name="canonical_tool_specs")
    op.drop_index("ix_canonical_tool_specs_content_hash", table_name="canonical_tool_specs")
    op.drop_index("ix_canonical_tool_specs_spec_hash", table_name="canonical_tool_specs")
    op.drop_table("canonical_tool_specs")
