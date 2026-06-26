"""extend effect_kind check with local-only kinds (filesystem, compute)

Revision ID: 0020_extend_effect_kinds
Revises: 0019_structured_effect_constraint_levels
Create Date: 2026-06-26

The original effect_kind CHECK (0018) only allowed network/remote-shaped
kinds: mutation, destructive, cost, secret_exposure, network. Structurally
ingesting local tools (jq, sed, sqlite3, awk, …) needs honest categories for
effects that touch neither the network nor remote state:

- ``filesystem`` — reads/writes local files (e.g. ``sed -i``, ``sqlite3``).
- ``compute``    — a pure transform with no side effects (e.g. ``jq``).

Labelling these "network" (the previous benign bucket) would be inaccurate,
so the allowed set is widened. Backward-compatible: every previously valid
value remains valid.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020_extend_effect_kinds"
down_revision: str | None = "0019_structured_effect_constraint_levels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "effect_kind IN ('mutation', 'destructive', 'cost', 'secret_exposure', 'network')"
_NEW = (
    "effect_kind IN ('mutation', 'destructive', 'cost', 'secret_exposure', "
    "'network', 'filesystem', 'compute')"
)


def upgrade() -> None:
    with op.batch_alter_table("effects") as batch_op:
        batch_op.drop_constraint("ck_effects_effect_kind", type_="check")
        batch_op.create_check_constraint("ck_effects_effect_kind", _NEW)


def downgrade() -> None:
    with op.batch_alter_table("effects") as batch_op:
        batch_op.drop_constraint("ck_effects_effect_kind", type_="check")
        batch_op.create_check_constraint("ck_effects_effect_kind", _OLD)
