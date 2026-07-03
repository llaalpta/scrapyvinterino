"""add session concurrency guards

Revision ID: 0007_session_guards
Revises: 0006_monitor_sessions
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_session_guards"
down_revision: str | None = "0006_monitor_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_monitor_sessions_active_source",
        "monitor_sessions",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_runs_running_session",
        "runs",
        ["session_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL AND status = 'running' AND finished_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_runs_running_session",
        table_name="runs",
        postgresql_where=sa.text("session_id IS NOT NULL AND status = 'running' AND finished_at IS NULL"),
    )
    op.drop_index("uq_monitor_sessions_active_source", table_name="monitor_sessions", postgresql_where=sa.text("status = 'active'"))
