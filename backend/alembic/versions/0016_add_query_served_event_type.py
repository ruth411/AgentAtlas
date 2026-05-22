"""add query_served event type + query entity type

Revision ID: 0016_add_query_served_event_type
Revises: 0015_human_review_and_audit_log
Create Date: 2026-05-22

Stage 18.1 — extend the audit_events CHECK constraints so the QueryEngine
can emit one row per successful ``ask()`` call. ``event_type='query_served'``
identifies the row; ``entity_type='query'`` lets the same audit-log query
infrastructure (``GET /audit/events``, ``GET /audit/claims/{id}``) treat
queries as first-class entities alongside claims and specs.

Why a new entity_type rather than re-using ``'claim'`` keyed to the top
match: a fallback ``ask()`` returns no match, so there is no claim to key
on. Using ``entity_type='query'`` with a synthesised ``entity_id`` (a
``query_id`` minted per request) keeps the schema honest in both the hit
and miss cases.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_add_query_served_event_type"
down_revision: str | None = "0015_human_review_and_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_constraint("ck_audit_events_event_type", type_="check")
        batch_op.create_check_constraint(
            "ck_audit_events_event_type",
            "event_type IN ('claim_submitted', 'verification_recorded', "
            "'human_review_recorded', 'tool_spec_published', "
            "'workflow_spec_published', 'query_served')",
        )
        batch_op.drop_constraint("ck_audit_events_entity_type", type_="check")
        batch_op.create_check_constraint(
            "ck_audit_events_entity_type",
            "entity_type IN ('claim', 'tool_spec', 'workflow_spec', 'query')",
        )


def downgrade() -> None:
    with op.batch_alter_table("audit_events") as batch_op:
        batch_op.drop_constraint("ck_audit_events_entity_type", type_="check")
        batch_op.create_check_constraint(
            "ck_audit_events_entity_type",
            "entity_type IN ('claim', 'tool_spec', 'workflow_spec')",
        )
        batch_op.drop_constraint("ck_audit_events_event_type", type_="check")
        batch_op.create_check_constraint(
            "ck_audit_events_event_type",
            "event_type IN ('claim_submitted', 'verification_recorded', "
            "'human_review_recorded', 'tool_spec_published', "
            "'workflow_spec_published')",
        )
