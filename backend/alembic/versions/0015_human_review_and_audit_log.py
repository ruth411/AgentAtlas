"""human review and audit log

Revision ID: 0015_human_review_and_audit_log
Revises: 0014_extend_artifact_type_for_sandbox
Create Date: 2026-05-18

Stage 13 introduces two tables:

- ``human_reviews``  — one row per reviewer decision against a claim
- ``audit_events``  — append-only event log covering every state-changing
  operation in Ayiru (claim submissions, verification results,
  human reviews, canonical spec publications)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_human_review_and_audit_log"
down_revision: str | None = "0014_extend_artifact_type_for_sandbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "human_reviews",
        sa.Column("review_id", sa.String(length=128), nullable=False),
        sa.Column("claim_id", sa.String(length=128), nullable=False),
        sa.Column("reviewer_id", sa.String(length=128), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected', 'needs_changes')",
            name="ck_human_reviews_decision",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["knowledge_claims.claim_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("review_id"),
    )
    op.create_index("ix_human_reviews_claim_id", "human_reviews", ["claim_id"])
    op.create_index("ix_human_reviews_reviewer_id", "human_reviews", ["reviewer_id"])
    op.create_index("ix_human_reviews_decision", "human_reviews", ["decision"])
    op.create_index("ix_human_reviews_reviewed_at", "human_reviews", ["reviewed_at"])

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.String(length=128), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "event_type IN ('claim_submitted', 'verification_recorded', "
            "'human_review_recorded', 'tool_spec_published', 'workflow_spec_published')",
            name="ck_audit_events_event_type",
        ),
        sa.CheckConstraint(
            "entity_type IN ('claim', 'tool_spec', 'workflow_spec')",
            name="ck_audit_events_entity_type",
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"])
    op.create_index("ix_audit_events_entity_type", "audit_events", ["entity_type"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])
    # Composite index for "every event for entity X" — the dominant
    # access pattern on the claim-history endpoint.
    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["entity_type", "entity_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_index("ix_audit_events_created_at", table_name="audit_events")
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_entity_id", table_name="audit_events")
    op.drop_index("ix_audit_events_entity_type", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_human_reviews_reviewed_at", table_name="human_reviews")
    op.drop_index("ix_human_reviews_decision", table_name="human_reviews")
    op.drop_index("ix_human_reviews_reviewer_id", table_name="human_reviews")
    op.drop_index("ix_human_reviews_claim_id", table_name="human_reviews")
    op.drop_table("human_reviews")
