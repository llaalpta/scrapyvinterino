"""move proxy selection to global pool

Revision ID: 0005_global_proxy_pool
Revises: 0004_backfill_run_sessions
Create Date: 2026-07-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_global_proxy_pool"
down_revision: str | None = "0004_backfill_run_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("proxy_profiles", sa.Column("kind", sa.String(length=32), nullable=False, server_default="own"))
    op.add_column("proxy_profiles", sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("proxy_profiles", sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proxy_profiles", sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("proxy_profiles", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint("search_sources_proxy_profile_id_fkey", "search_sources", type_="foreignkey")
    op.drop_column("search_sources", "proxy_profile_id")


def downgrade() -> None:
    op.add_column("search_sources", sa.Column("proxy_profile_id", sa.Integer(), nullable=True))
    op.create_foreign_key("search_sources_proxy_profile_id_fkey", "search_sources", "proxy_profiles", ["proxy_profile_id"], ["id"])
    op.drop_column("proxy_profiles", "last_used_at")
    op.drop_column("proxy_profiles", "failure_count")
    op.drop_column("proxy_profiles", "cooldown_until")
    op.drop_column("proxy_profiles", "max_concurrent_runs")
    op.drop_column("proxy_profiles", "kind")
