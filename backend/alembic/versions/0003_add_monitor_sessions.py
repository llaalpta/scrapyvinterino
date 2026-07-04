"""add monitor sessions

Revision ID: 0003_add_monitor_sessions
Revises: 0002_add_run_event_level
Create Date: 2026-07-04 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_add_monitor_sessions"
down_revision: str | None = "0002_add_run_event_level"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "monitor_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=80), nullable=True),
    )
    op.add_column("runs", sa.Column("monitor_session_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_runs_monitor_session_id_monitor_sessions",
        "runs",
        "monitor_sessions",
        ["monitor_session_id"],
        ["id"],
    )
    op.execute(
        """
        INSERT INTO monitor_sessions (source_id, started_at)
        SELECT id, COALESCE(monitor_started_at, now())
        FROM search_sources
        WHERE is_active IS TRUE
          AND archived_at IS NULL
          AND monitor_mode <> 'manual'
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_runs_monitor_session_id_monitor_sessions", "runs", type_="foreignkey")
    op.drop_column("runs", "monitor_session_id")
    op.drop_table("monitor_sessions")
