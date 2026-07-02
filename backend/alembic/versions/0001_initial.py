"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "search_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("normalized_query", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("scheduler_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vinted_item_id", sa.String(length=64), nullable=False, unique=True),
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
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("items_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opportunities_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text()),
    )
    op.create_table(
        "filter_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "source_seen_items",
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("first_run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("last_run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "opportunities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), nullable=False),
        sa.Column("rule_id", sa.Integer(), sa.ForeignKey("filter_rules.id")),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="new"),
        sa.Column("score", sa.Numeric(5, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("source_id", "item_id", "rule_id", name="uq_opportunity_source_item_rule"),
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
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
    op.drop_table("opportunities")
    op.drop_table("source_seen_items")
    op.drop_table("filter_rules")
    op.drop_table("runs")
    op.drop_table("items")
    op.drop_table("search_sources")
    op.drop_table("users")
