"""add structured knowledge persistence tables

Revision ID: 0018_add_structured_knowledge_tables
Revises: 0017_add_claim_embedding
Create Date: 2026-06-12

Phase 1 of the structured-agent-substrate pivot adds first-class
storage for machine-readable subjects, capabilities, constraints, and
effects. QueryEngine still falls back to claim projection today; later
phases will migrate reads to these tables family by family, starting
with gh.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_add_structured_knowledge_tables"
down_revision: str | None = "0017_add_claim_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("subject_kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("family", sa.String(length=128), nullable=False),
        sa.Column("verification_level", sa.String(length=64), nullable=False),
        sa.Column("provenance_claim_ids_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subject_kind IN ('tool', 'api', 'sdk', 'adk', 'workflow', 'subject')",
            name="ck_subjects_subject_kind",
        ),
        sa.CheckConstraint(
            "verification_level IN ("
            "'L0_unverified', 'L1_schema_valid', 'L2_source_verified', "
            "'L3_runtime_verified', 'L4_cross_agent_verified', 'L5_human_audited'"
            ")",
            name="ck_subjects_verification_level",
        ),
        sa.PrimaryKeyConstraint("subject_id"),
    )
    op.create_index("ix_subjects_subject_kind", "subjects", ["subject_kind"], unique=False)
    op.create_index("ix_subjects_family", "subjects", ["family"], unique=False)
    op.create_index(
        "ix_subjects_family_subject_kind",
        "subjects",
        ["family", "subject_kind"],
        unique=False,
    )
    op.create_index("ix_subjects_verification_level", "subjects", ["verification_level"], unique=False)
    op.create_index("ix_subjects_created_at", "subjects", ["created_at"], unique=False)
    op.create_index("ix_subjects_updated_at", "subjects", ["updated_at"], unique=False)

    op.create_table(
        "capabilities",
        sa.Column("capability_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("capability_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("verification_status", sa.String(length=64), nullable=False),
        sa.Column("verification_level", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("confidence_band", sa.String(length=32), nullable=False),
        sa.Column("risk_level", sa.String(length=32), nullable=True),
        sa.Column("provenance_claim_ids_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "capability_type IN ("
            "'existence', 'invocation', 'configuration', 'constraint', "
            "'effect', 'environment', 'deprecation', 'workflow', 'metadata'"
            ")",
            name="ck_capabilities_capability_type",
        ),
        sa.CheckConstraint(
            "verification_status IN ("
            "'pending', 'accepted', 'rejected', 'conflict_detected', "
            "'requires_human_review'"
            ")",
            name="ck_capabilities_verification_status",
        ),
        sa.CheckConstraint(
            "verification_level IN ("
            "'L0_unverified', 'L1_schema_valid', 'L2_source_verified', "
            "'L3_runtime_verified', 'L4_cross_agent_verified', 'L5_human_audited'"
            ")",
            name="ck_capabilities_verification_level",
        ),
        sa.CheckConstraint(
            "confidence_band IN ('none', 'low', 'moderate', 'high', 'strong')",
            name="ck_capabilities_confidence_band",
        ),
        sa.CheckConstraint(
            "risk_level IS NULL OR risk_level IN ('none', 'low', 'medium', 'high', 'critical')",
            name="ck_capabilities_risk_level",
        ),
        sa.CheckConstraint(
            "source IN ('structured_ingestion', 'prose_projection')",
            name="ck_capabilities_source",
        ),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.subject_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("capability_id"),
    )
    op.create_index("ix_capabilities_subject_id", "capabilities", ["subject_id"], unique=False)
    op.create_index(
        "ix_capabilities_capability_type",
        "capabilities",
        ["capability_type"],
        unique=False,
    )
    op.create_index(
        "ix_capabilities_verification_status",
        "capabilities",
        ["verification_status"],
        unique=False,
    )
    op.create_index(
        "ix_capabilities_verification_level",
        "capabilities",
        ["verification_level"],
        unique=False,
    )
    op.create_index("ix_capabilities_confidence_band", "capabilities", ["confidence_band"], unique=False)
    op.create_index("ix_capabilities_risk_level", "capabilities", ["risk_level"], unique=False)
    op.create_index("ix_capabilities_source", "capabilities", ["source"], unique=False)
    op.create_index("ix_capabilities_created_at", "capabilities", ["created_at"], unique=False)
    op.create_index("ix_capabilities_updated_at", "capabilities", ["updated_at"], unique=False)

    op.create_table(
        "constraints",
        sa.Column("constraint_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("constraint_kind", sa.String(length=32), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "constraint_kind IN ('auth_scope', 'environment', 'precondition', 'deprecation')",
            name="ck_constraints_constraint_kind",
        ),
        sa.CheckConstraint(
            "source IN ('structured_ingestion', 'prose_projection')",
            name="ck_constraints_source",
        ),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.subject_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("constraint_id"),
    )
    op.create_index("ix_constraints_subject_id", "constraints", ["subject_id"], unique=False)
    op.create_index("ix_constraints_constraint_kind", "constraints", ["constraint_kind"], unique=False)
    op.create_index("ix_constraints_source", "constraints", ["source"], unique=False)
    op.create_index("ix_constraints_created_at", "constraints", ["created_at"], unique=False)
    op.create_index("ix_constraints_updated_at", "constraints", ["updated_at"], unique=False)

    op.create_table(
        "effects",
        sa.Column("effect_id", sa.String(length=128), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("effect_kind", sa.String(length=32), nullable=False),
        sa.Column("destructive", sa.Boolean(), nullable=False),
        sa.Column("reversible", sa.Boolean(), nullable=False),
        sa.Column("mutates_remote_state", sa.Boolean(), nullable=False),
        sa.Column("may_cost_money", sa.Boolean(), nullable=False),
        sa.Column("may_expose_secrets", sa.Boolean(), nullable=False),
        sa.Column("detail_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "effect_kind IN ('mutation', 'destructive', 'cost', 'secret_exposure', 'network')",
            name="ck_effects_effect_kind",
        ),
        sa.CheckConstraint(
            "source IN ('structured_ingestion', 'prose_projection')",
            name="ck_effects_source",
        ),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.subject_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("effect_id"),
    )
    op.create_index("ix_effects_subject_id", "effects", ["subject_id"], unique=False)
    op.create_index("ix_effects_effect_kind", "effects", ["effect_kind"], unique=False)
    op.create_index("ix_effects_source", "effects", ["source"], unique=False)
    op.create_index("ix_effects_created_at", "effects", ["created_at"], unique=False)
    op.create_index("ix_effects_updated_at", "effects", ["updated_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_effects_updated_at", table_name="effects")
    op.drop_index("ix_effects_created_at", table_name="effects")
    op.drop_index("ix_effects_source", table_name="effects")
    op.drop_index("ix_effects_effect_kind", table_name="effects")
    op.drop_index("ix_effects_subject_id", table_name="effects")
    op.drop_table("effects")

    op.drop_index("ix_constraints_updated_at", table_name="constraints")
    op.drop_index("ix_constraints_created_at", table_name="constraints")
    op.drop_index("ix_constraints_source", table_name="constraints")
    op.drop_index("ix_constraints_constraint_kind", table_name="constraints")
    op.drop_index("ix_constraints_subject_id", table_name="constraints")
    op.drop_table("constraints")

    op.drop_index("ix_capabilities_updated_at", table_name="capabilities")
    op.drop_index("ix_capabilities_created_at", table_name="capabilities")
    op.drop_index("ix_capabilities_source", table_name="capabilities")
    op.drop_index("ix_capabilities_risk_level", table_name="capabilities")
    op.drop_index("ix_capabilities_confidence_band", table_name="capabilities")
    op.drop_index("ix_capabilities_verification_level", table_name="capabilities")
    op.drop_index("ix_capabilities_verification_status", table_name="capabilities")
    op.drop_index("ix_capabilities_capability_type", table_name="capabilities")
    op.drop_index("ix_capabilities_subject_id", table_name="capabilities")
    op.drop_table("capabilities")

    op.drop_index("ix_subjects_updated_at", table_name="subjects")
    op.drop_index("ix_subjects_created_at", table_name="subjects")
    op.drop_index("ix_subjects_verification_level", table_name="subjects")
    op.drop_index("ix_subjects_family_subject_kind", table_name="subjects")
    op.drop_index("ix_subjects_family", table_name="subjects")
    op.drop_index("ix_subjects_subject_kind", table_name="subjects")
    op.drop_table("subjects")
