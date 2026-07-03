"""add source archive and timed sessions

Revision ID: 0008_timed_sessions
Revises: 0007_session_guards
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_timed_sessions"
down_revision: str | None = "0007_session_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("search_sources", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("monitor_sessions", sa.Column("auto_stop_at", sa.DateTime(timezone=True)))
    op.create_index("ix_search_sources_archived_at", "search_sources", ["archived_at"])
    op.create_index(
        "ix_monitor_sessions_active_auto_stop",
        "monitor_sessions",
        ["auto_stop_at"],
        postgresql_where=sa.text("status = 'active' AND auto_stop_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_monitor_sessions_active_auto_stop",
        table_name="monitor_sessions",
        postgresql_where=sa.text("status = 'active' AND auto_stop_at IS NOT NULL"),
    )
    op.drop_index("ix_search_sources_archived_at", table_name="search_sources")
    op.drop_column("monitor_sessions", "auto_stop_at")
    op.drop_column("search_sources", "archived_at")
