"""add in_progress to ingestion status check constraint

Revision ID: 0008_add_in_progress_ingestion_status
Revises: 0007_create_ingestion_tables
Create Date: 2026-05-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008_add_in_progress_ingestion_status"
down_revision: str | None = "0007_create_ingestion_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ingestion_runs") as batch_op:
        batch_op.drop_constraint("ck_ingestion_runs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_ingestion_runs_status",
            "status IN ('in_progress', 'completed', 'failed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("ingestion_runs") as batch_op:
        batch_op.drop_constraint("ck_ingestion_runs_status", type_="check")
        batch_op.create_check_constraint(
            "ck_ingestion_runs_status",
            "status IN ('completed', 'failed')",
        )
