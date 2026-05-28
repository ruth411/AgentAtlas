"""add embedding column to knowledge_claims for semantic re-ranking

Revision ID: 0017_add_claim_embedding
Revises: 0016_add_query_served_event_type
Create Date: 2026-05-28

Stage 22-stretch — the lexical token-overlap matcher returns correct
claims for ansible / kubectl / helm questions but at ~0.45 confidence,
below the SDK's ``Answer.is_useful`` 0.6 threshold. Agents see "right
claim, low confidence" and escalate to web_search anyway.

The fix: store a 384-dim embedding (BAAI/bge-small-en-v1.5) of
``subject + statement`` per claim, then re-rank the lexical top-50 by
cosine similarity to the question embedding. Verified locally: cosine
similarity of "how do I copy a file with ansible" vs
"ansible.builtin.copy module documentation" = 0.845, vs the lexical
score's 0.45 for the same pairing.

The column is nullable so existing claims continue to load; the
``ayiru reindex`` CLI subcommand backfills embeddings, and the query
engine falls back to pure-lexical ranking when a candidate is
missing its embedding.

Embedding storage: JSON-encoded list[float] in a TEXT column. With
~600 claims and 384 floats each (~3KB JSON per row), the column is
~1.8 MB total — small enough for in-memory cosine, no sqlite-vec
needed at this scale.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_add_claim_embedding"
down_revision: str | None = "0016_add_query_served_event_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.add_column(
            sa.Column("embedding", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_claims") as batch_op:
        batch_op.drop_column("embedding")
