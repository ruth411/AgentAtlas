"""create ingestion tables

Revision ID: 0007_create_ingestion_tables
Revises: 0006_create_canonical_specs
Create Date: 2026-05-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_create_ingestion_tables"
down_revision: str | None = "0006_create_canonical_specs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("tool_id", sa.String(length=128), nullable=False),
        sa.Column("command", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ck_ingestion_runs_status",
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_ingestion_runs_tool_id", "ingestion_runs", ["tool_id"])
    op.create_index("ix_ingestion_runs_command", "ingestion_runs", ["command"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])
    op.create_index("ix_ingestion_runs_submitted_by", "ingestion_runs", ["submitted_by"])
    op.create_index("ix_ingestion_runs_created_at", "ingestion_runs", ["created_at"])

    op.create_table(
        "raw_ingestion_artifacts",
        sa.Column("artifact_id", sa.String(length=128), nullable=False),
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("artifact_type", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("hash", sa.String(length=128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "artifact_type IN ('cli_output')",
            name="ck_raw_ingestion_artifacts_type",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["ingestion_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index("ix_raw_ingestion_artifacts_run_id", "raw_ingestion_artifacts", ["run_id"])
    op.create_index(
        "ix_raw_ingestion_artifacts_artifact_type",
        "raw_ingestion_artifacts",
        ["artifact_type"],
    )
    op.create_index("ix_raw_ingestion_artifacts_hash", "raw_ingestion_artifacts", ["hash"])
    op.create_index(
        "ix_raw_ingestion_artifacts_captured_at",
        "raw_ingestion_artifacts",
        ["captured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_ingestion_artifacts_captured_at", table_name="raw_ingestion_artifacts")
    op.drop_index("ix_raw_ingestion_artifacts_hash", table_name="raw_ingestion_artifacts")
    op.drop_index(
        "ix_raw_ingestion_artifacts_artifact_type",
        table_name="raw_ingestion_artifacts",
    )
    op.drop_index("ix_raw_ingestion_artifacts_run_id", table_name="raw_ingestion_artifacts")
    op.drop_table("raw_ingestion_artifacts")

    op.drop_index("ix_ingestion_runs_created_at", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_submitted_by", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_status", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_command", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_tool_id", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
