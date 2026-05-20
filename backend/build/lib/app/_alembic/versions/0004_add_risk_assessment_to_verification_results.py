"""add risk assessment to verification results

Revision ID: 0004_add_risk_assessment_to_verification_results
Revises: 0003_add_confidence_band_and_breakdown
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_risk_assessment_to_verification_results"
down_revision: str | None = "0003_add_confidence_band_and_breakdown"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_LEGACY_RISK = (
    '{"risk_level": "medium", "reasons": ["legacy_row_pre_stage_5"], '
    '"requires_confirmation": true, "safe_to_auto_execute": false, '
    '"dimensions": [], "matched_rule_ids": [], "matches": []}'
)


def upgrade() -> None:
    with op.batch_alter_table("verification_results") as batch_op:
        batch_op.add_column(
            sa.Column(
                "risk_assessment",
                sa.Text(),
                nullable=False,
                server_default=_LEGACY_RISK,
            )
        )

    with op.batch_alter_table("verification_results") as batch_op:
        batch_op.alter_column("risk_assessment", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("verification_results") as batch_op:
        batch_op.drop_column("risk_assessment")
