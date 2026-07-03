"""add opportunity monitor runtime fields

Revision ID: 0009_opportunity_monitors
Revises: 0008_timed_sessions
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_opportunity_monitors"
down_revision: str | None = "0008_timed_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("search_sources", sa.Column("monitor_mode", sa.String(length=40), nullable=False, server_default="manual"))
    op.add_column("search_sources", sa.Column("duration_minutes", sa.Integer(), nullable=True))
    op.add_column(
        "search_sources",
        sa.Column("filter_rule_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
    )
    op.add_column("search_sources", sa.Column("proxy_profile_id", sa.Integer(), nullable=True))
    op.add_column("search_sources", sa.Column("monitor_started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_sources", sa.Column("monitor_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_sources", sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("search_sources", sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key("fk_search_sources_proxy_profile_id", "search_sources", "proxy_profiles", ["proxy_profile_id"], ["id"])
    op.create_index(
        "uq_opportunities_monitor_item",
        "opportunities",
        ["source_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_opportunities_monitor_item", table_name="opportunities", postgresql_where=sa.text("session_id IS NULL"))
    op.drop_constraint("fk_search_sources_proxy_profile_id", "search_sources", type_="foreignkey")
    op.drop_column("search_sources", "next_run_at")
    op.drop_column("search_sources", "last_run_at")
    op.drop_column("search_sources", "monitor_until")
    op.drop_column("search_sources", "monitor_started_at")
    op.drop_column("search_sources", "proxy_profile_id")
    op.drop_column("search_sources", "filter_rule_ids")
    op.drop_column("search_sources", "duration_minutes")
    op.drop_column("search_sources", "monitor_mode")
