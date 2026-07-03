"""guard concurrent monitor runs

Revision ID: 0010_monitor_run_single_flight
Revises: 0009_opportunity_monitors
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_monitor_run_single_flight"
down_revision: str | None = "0009_opportunity_monitors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_runs_running_monitor",
        "runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NULL AND status = 'running' AND finished_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_runs_running_monitor",
        table_name="runs",
        postgresql_where=sa.text("session_id IS NULL AND status = 'running' AND finished_at IS NULL"),
    )
