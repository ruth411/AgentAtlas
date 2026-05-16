"""add confidence band and breakdown columns

Revision ID: 0003_add_confidence_band_and_breakdown
Revises: 0002_create_verification_results
Create Date: 2026-05-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_add_confidence_band_and_breakdown"
down_revision: str | None = "0002_create_verification_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CONFIDENCE_BAND_VALUES = "'none', 'low', 'moderate', 'high', 'strong'"
_LEGACY_BREAKDOWN = (
    '{"score": 0.0, "band": "none", "components": [], "caps_applied": ["legacy_row"], '
    '"penalties": ["legacy_row"]}'
)


def upgrade() -> None:
    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.add_column(
            sa.Column("confidence_band", sa.String(length=32), nullable=True),
        )
        batch_op.create_check_constraint(
            "ck_knowledge_claims_confidence_band",
            f"confidence_band IS NULL OR confidence_band IN ({_CONFIDENCE_BAND_VALUES})",
        )
        batch_op.create_index(
            "ix_knowledge_claims_confidence_band",
            ["confidence_band"],
        )

    with op.batch_alter_table("verification_results") as batch_op:
        batch_op.add_column(
            sa.Column(
                "confidence_band",
                sa.String(length=32),
                nullable=False,
                server_default="none",
            )
        )
        batch_op.add_column(
            sa.Column(
                "confidence_breakdown",
                sa.Text(),
                nullable=False,
                server_default=_LEGACY_BREAKDOWN,
            )
        )
        batch_op.create_check_constraint(
            "ck_verification_results_confidence_band",
            f"confidence_band IN ({_CONFIDENCE_BAND_VALUES})",
        )
        batch_op.create_index(
            "ix_verification_results_confidence_band",
            ["confidence_band"],
        )

    # Drop the server defaults so future inserts must supply a real value.
    with op.batch_alter_table("verification_results") as batch_op:
        batch_op.alter_column("confidence_band", server_default=None)
        batch_op.alter_column("confidence_breakdown", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("verification_results") as batch_op:
        batch_op.drop_index("ix_verification_results_confidence_band")
        batch_op.drop_constraint(
            "ck_verification_results_confidence_band", type_="check"
        )
        batch_op.drop_column("confidence_breakdown")
        batch_op.drop_column("confidence_band")

    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.drop_index("ix_knowledge_claims_confidence_band")
        batch_op.drop_constraint(
            "ck_knowledge_claims_confidence_band", type_="check"
        )
        batch_op.drop_column("confidence_band")
