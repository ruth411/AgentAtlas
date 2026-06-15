"""add verification_level to constraints and effects

Revision ID: 0019_structured_effect_constraint_levels
Revises: 0018_add_structured_knowledge_tables
Create Date: 2026-06-14

WS2 of the honesty pass. The structured `gh` lane previously presented
every constraint and effect row to agents as ``L3_runtime_verified``
(hard-coded in the query mappers), even though the destructive /
reversible / mutates booleans are *inferred* from help text rather than
asserted by running an experiment. That is silent inflation of the very
signal the README sells as "no silent inflation".

This migration gives constraints and effects their own
``verification_level`` so the substrate records how each row was derived:

- environment constraints (the binary requirement, proven by actually
  running ``gh``) -> ``L3_runtime_verified``
- auth-scope / precondition constraints (parsed from help text) and all
  effect rows (heuristic safety classification) -> ``L2_source_verified``

Existing rows are backfilled to ``L2_source_verified`` and then the
environment constraints are promoted to ``L3``; a structured re-ingest
rewrites every row with its precise level.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_structured_effect_constraint_levels"
down_revision: str | None = "0018_add_structured_knowledge_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ADD COLUMN (no batch recreate) so the named CHECK constraints the
    # tables were created with in 0018 are preserved. The server_default
    # backfills existing rows; ingestion always sets the column explicitly.
    for table in ("constraints", "effects"):
        op.add_column(
            table,
            sa.Column(
                "verification_level",
                sa.String(length=64),
                nullable=False,
                server_default="L2_source_verified",
            ),
        )
    # Environment constraints are proven by executing the binary.
    op.execute(
        "UPDATE constraints SET verification_level = 'L3_runtime_verified' "
        "WHERE constraint_kind = 'environment'"
    )


def downgrade() -> None:
    for table in ("constraints", "effects"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("verification_level")
