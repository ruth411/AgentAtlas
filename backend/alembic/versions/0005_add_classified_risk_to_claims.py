"""add classified risk to claims

Revision ID: 0005_add_classified_risk_to_claims
Revises: 0004_add_risk_assessment_to_verification_results
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_add_classified_risk_to_claims"
down_revision: str | None = "0004_add_risk_assessment_to_verification_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_RISK_LEVEL_VALUES = "'none', 'low', 'medium', 'high', 'critical'"


def upgrade() -> None:
    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.add_column(
            sa.Column("risk_level_classified", sa.String(length=32), nullable=True)
        )
        batch_op.add_column(sa.Column("risk_assessment", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_knowledge_claims_risk_level_classified",
            f"risk_level_classified IS NULL OR risk_level_classified IN ({_RISK_LEVEL_VALUES})",
        )
        batch_op.create_index(
            "ix_knowledge_claims_risk_level_classified",
            ["risk_level_classified"],
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.drop_index("ix_knowledge_claims_risk_level_classified")
        batch_op.drop_constraint(
            "ck_knowledge_claims_risk_level_classified", type_="check"
        )
        batch_op.drop_column("risk_assessment")
        batch_op.drop_column("risk_level_classified")
