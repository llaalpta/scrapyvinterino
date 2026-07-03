"""add monitor sessions filters proxies

Revision ID: 0006_monitor_sessions
Revises: 0005_result_browser_idx
Create Date: 2026-07-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_monitor_sessions"
down_revision: str | None = "0005_result_browser_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proxy_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("scheme", sa.String(length=16), nullable=False, server_default="http"),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255)),
        sa.Column("password_encrypted", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_test_status", sa.String(length=40)),
        sa.Column("last_test_ip", sa.String(length=80)),
        sa.Column("last_test_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_proxy_profiles_name"),
    )
    op.create_table(
        "monitor_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("proxy_profile_id", sa.Integer(), sa.ForeignKey("proxy_profiles.id")),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("filter_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("filter_hash", sa.String(length=64), nullable=False),
        sa.Column("cadence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("runtime_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
    )
    op.add_column("runs", sa.Column("session_id", sa.Integer(), sa.ForeignKey("monitor_sessions.id")))
    op.add_column("runs", sa.Column("items_filter_passed", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("items_discarded_by_filters", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("items_filter_pending", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "runs",
        sa.Column("runtime_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.alter_column("filter_rules", "source_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("filter_rules", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.drop_constraint("uq_opportunity_item_rule", "opportunities", type_="unique")
    op.alter_column("opportunities", "rule_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("opportunities", sa.Column("session_id", sa.Integer(), sa.ForeignKey("monitor_sessions.id")))
    op.add_column("opportunities", sa.Column("evaluation_status", sa.String(length=40), nullable=False, server_default="passed"))
    op.add_column(
        "opportunities",
        sa.Column("filter_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_unique_constraint("uq_opportunity_session_item", "opportunities", ["session_id", "item_id"])
    op.create_table(
        "session_item_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("monitor_sessions.id"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("filter_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), sa.ForeignKey("opportunities.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("session_id", "item_id", name="uq_session_item_state_session_item"),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id")),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("monitor_sessions.id")),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id")),
        sa.Column("phase", sa.String(length=80), nullable=False),
        sa.Column("method", sa.String(length=12)),
        sa.Column("url", sa.Text()),
        sa.Column("status_code", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("proxy_profile_id", sa.Integer(), sa.ForeignKey("proxy_profiles.id")),
        sa.Column("egress_ip", sa.String(length=80)),
        sa.Column("user_agent", sa.Text()),
        sa.Column("auth_mode", sa.String(length=80)),
        sa.Column("message", sa.Text()),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("session_item_state")
    op.drop_constraint("uq_opportunity_session_item", "opportunities", type_="unique")
    op.drop_column("opportunities", "filter_snapshot")
    op.drop_column("opportunities", "evaluation_status")
    op.drop_column("opportunities", "session_id")
    op.alter_column("opportunities", "rule_id", existing_type=sa.Integer(), nullable=False)
    op.create_unique_constraint("uq_opportunity_item_rule", "opportunities", ["item_id", "rule_id"])
    op.drop_column("filter_rules", "updated_at")
    op.alter_column("filter_rules", "source_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("runs", "runtime_metadata")
    op.drop_column("runs", "items_filter_pending")
    op.drop_column("runs", "items_discarded_by_filters")
    op.drop_column("runs", "items_filter_passed")
    op.drop_column("runs", "session_id")
    op.drop_table("monitor_sessions")
    op.drop_table("proxy_profiles")
