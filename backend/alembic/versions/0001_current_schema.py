"""current development schema

Revision ID: 0001_current_schema
Revises:
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_current_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
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
        "search_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_query", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scheduler_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("monitor_mode", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("duration_minutes", sa.Integer()),
        sa.Column("filter_rule_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("proxy_profile_id", sa.Integer(), sa.ForeignKey("proxy_profiles.id")),
        sa.Column("monitor_started_at", sa.DateTime(timezone=True)),
        sa.Column("monitor_until", sa.DateTime(timezone=True)),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vinted_item_id", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("brand", sa.String(length=160)),
        sa.Column("price_amount", sa.Numeric(10, 2)),
        sa.Column("currency", sa.String(length=8)),
        sa.Column("size", sa.String(length=80)),
        sa.Column("status", sa.String(length=120)),
        sa.Column("seller_login", sa.String(length=160)),
        sa.Column("seller_country", sa.String(length=80)),
        sa.Column("favorite_count", sa.Integer()),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("image_url", sa.Text()),
        sa.Column("description", sa.Text()),
        sa.Column("color", sa.String(length=120)),
        sa.Column("category", sa.Text()),
        sa.Column("shipping_price_amount", sa.Numeric(10, 2)),
        sa.Column("buyer_protection_fee_amount", sa.Numeric(10, 2)),
        sa.Column("total_price_amount", sa.Numeric(10, 2)),
        sa.Column("photos", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("seller_rating", sa.Numeric(5, 2)),
        sa.Column("seller_badges", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("availability_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("detail_raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("detail_last_fetched_at", sa.DateTime(timezone=True)),
        sa.Column("detail_error", sa.Text()),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("vinted_item_id", name="uq_items_vinted_item_id"),
    )
    op.create_index("ix_items_price_amount", "items", ["price_amount"])
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False, server_default="manual"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("items_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_filter_passed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_discarded_by_filters", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_filter_pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opportunities_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("runtime_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index(
        "uq_runs_running_monitor",
        "runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND finished_at IS NULL"),
    )
    op.create_table(
        "filter_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id")),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("filter_rules.id")),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="new"),
        sa.Column("evaluation_status", sa.String(length=40), nullable=False, server_default="passed"),
        sa.Column("filter_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("score", sa.Numeric(5, 2)),
        sa.Column("last_scraped_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_id", sa.Integer(), sa.ForeignKey("runs.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "item_id", name="uq_opportunity_monitor_item"),
    )
    op.create_index("ix_opportunities_last_scraped_at", "opportunities", ["last_scraped_at"])
    op.create_index("ix_opportunities_source_last_scraped", "opportunities", ["source_id", "last_scraped_at"])
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id")),
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
    op.create_table(
        "action_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("action_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "action_executions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("action_request_id", sa.Integer(), sa.ForeignKey("action_requests.id"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("redacted_request", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("redacted_response", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "checkout_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "errors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("runs.id")),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id")),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("errors")
    op.drop_table("checkout_snapshots")
    op.drop_table("action_executions")
    op.drop_table("action_requests")
    op.drop_table("run_events")
    op.drop_index("ix_opportunities_source_last_scraped", table_name="opportunities")
    op.drop_index("ix_opportunities_last_scraped_at", table_name="opportunities")
    op.drop_table("opportunities")
    op.drop_table("filter_rules")
    op.drop_index("uq_runs_running_monitor", table_name="runs", postgresql_where=sa.text("status = 'running' AND finished_at IS NULL"))
    op.drop_table("runs")
    op.drop_index("ix_items_price_amount", table_name="items")
    op.drop_table("items")
    op.drop_table("app_settings")
    op.drop_table("search_sources")
    op.drop_table("proxy_profiles")
    op.drop_table("users")
