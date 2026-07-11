"""Add indexed worker task identity to runs.

Revision ID: 0012_add_run_task_id
Revises: 0011_vinted_status_invalid
Create Date: 2026-07-11 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_add_run_task_id"
down_revision: str | None = "0011_vinted_status_invalid"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("task_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_runs_task_id"), "runs", ["task_id"], unique=False)
    op.execute(
        """
        UPDATE runs
        SET task_id = runtime_metadata ->> 'task_id'
        WHERE runtime_metadata ? 'task_id'
          AND length(runtime_metadata ->> 'task_id') BETWEEN 1 AND 64
        """
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_runs_task_id"), table_name="runs")
    op.drop_column("runs", "task_id")
