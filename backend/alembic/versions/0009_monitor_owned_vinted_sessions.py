"""Bind Vinted sessions to monitors.

Revision ID: 0009_monitor_vinted_sessions
Revises: 0008_vinted_sessions
Create Date: 2026-07-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_monitor_vinted_sessions"
down_revision: str | None = "0008_vinted_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM vinted_sessions")
    op.add_column("vinted_sessions", sa.Column("source_id", sa.Integer(), nullable=False))
    op.create_foreign_key("fk_vinted_sessions_source_id", "vinted_sessions", "search_sources", ["source_id"], ["id"])
    op.drop_index("ix_vinted_sessions_proxy_status", table_name="vinted_sessions")
    op.drop_index("ix_vinted_sessions_ready", table_name="vinted_sessions")
    op.create_index(
        "ix_vinted_sessions_source_proxy_status",
        "vinted_sessions",
        ["source_id", "proxy_profile_id", "status"],
    )
    op.create_index("ix_vinted_sessions_source_ready", "vinted_sessions", ["source_id", "status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_vinted_sessions_source_ready", table_name="vinted_sessions")
    op.drop_index("ix_vinted_sessions_source_proxy_status", table_name="vinted_sessions")
    op.create_index("ix_vinted_sessions_ready", "vinted_sessions", ["status", "expires_at"])
    op.create_index("ix_vinted_sessions_proxy_status", "vinted_sessions", ["proxy_profile_id", "status"])
    op.drop_constraint("fk_vinted_sessions_source_id", "vinted_sessions", type_="foreignkey")
    op.drop_column("vinted_sessions", "source_id")
