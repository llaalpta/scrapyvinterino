"""fast opportunity pipeline cleanup

Revision ID: 0011_fast_opportunity_pipeline
Revises: 0010_monitor_run_single_flight
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_fast_opportunity_pipeline"
down_revision: str | None = "0010_monitor_run_single_flight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("opportunities", sa.Column("last_scraped_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("opportunities", sa.Column("last_run_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_opportunities_last_run_id", "opportunities", "runs", ["last_run_id"], ["id"])
    op.execute(
        """
        UPDATE opportunities AS opportunity
        SET
            last_scraped_at = COALESCE(source_seen_items.last_seen_at, opportunity.created_at),
            last_run_id = source_seen_items.last_run_id
        FROM source_seen_items
        WHERE
            source_seen_items.source_id = opportunity.source_id
            AND source_seen_items.item_id = opportunity.item_id
        """
    )
    op.execute("UPDATE opportunities SET last_scraped_at = created_at WHERE last_scraped_at IS NULL")

    op.drop_index(
        "uq_runs_running_monitor",
        table_name="runs",
        postgresql_where=sa.text("session_id IS NULL AND status = 'running' AND finished_at IS NULL"),
    )
    op.drop_index(
        "uq_runs_running_session",
        table_name="runs",
        postgresql_where=sa.text("session_id IS NOT NULL AND status = 'running' AND finished_at IS NULL"),
    )
    op.drop_index("uq_monitor_sessions_active_source", table_name="monitor_sessions", postgresql_where=sa.text("status = 'active'"))

    op.drop_table("session_item_state")
    op.drop_constraint("uq_opportunity_session_item", "opportunities", type_="unique")
    op.drop_index("uq_opportunities_monitor_item", table_name="opportunities", postgresql_where=sa.text("session_id IS NULL"))
    op.drop_column("run_events", "session_id")
    op.drop_column("runs", "session_id")
    op.drop_column("opportunities", "session_id")
    op.drop_table("monitor_sessions")

    op.drop_index("ix_source_seen_items_source_last_seen", table_name="source_seen_items")
    op.drop_index("ix_source_seen_items_last_seen_at", table_name="source_seen_items")
    op.drop_table("source_seen_items")

    op.create_unique_constraint("uq_opportunity_monitor_item", "opportunities", ["source_id", "item_id"])
    op.create_index(
        "uq_runs_running_monitor",
        "runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND finished_at IS NULL"),
    )
    op.create_index("ix_opportunities_last_scraped_at", "opportunities", ["last_scraped_at"])
    op.create_index("ix_opportunities_source_last_scraped", "opportunities", ["source_id", "last_scraped_at"])


def downgrade() -> None:
    op.drop_index("ix_opportunities_source_last_scraped", table_name="opportunities")
    op.drop_index("ix_opportunities_last_scraped_at", table_name="opportunities")
    op.drop_index(
        "uq_runs_running_monitor",
        table_name="runs",
        postgresql_where=sa.text("status = 'running' AND finished_at IS NULL"),
    )
    op.drop_constraint("uq_opportunity_monitor_item", "opportunities", type_="unique")

    op.create_table(
        "source_seen_items",
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), primary_key=True),
        sa.Column("item_id", sa.Integer(), sa.ForeignKey("items.id"), primary_key=True),
        sa.Column("first_run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("last_run_id", sa.Integer(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_source_seen_items_last_seen_at", "source_seen_items", ["last_seen_at"])
    op.create_index("ix_source_seen_items_source_last_seen", "source_seen_items", ["source_id", "last_seen_at"])

    op.create_table(
        "monitor_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("search_sources.id"), nullable=False),
        sa.Column("proxy_profile_id", sa.Integer(), sa.ForeignKey("proxy_profiles.id")),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="active"),
        sa.Column("filter_snapshot", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("filter_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("cadence_snapshot", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("runtime_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("auto_stop_at", sa.DateTime(timezone=True)),
    )
    op.add_column("opportunities", sa.Column("session_id", sa.Integer(), sa.ForeignKey("monitor_sessions.id"), nullable=True))
    op.add_column("runs", sa.Column("session_id", sa.Integer(), sa.ForeignKey("monitor_sessions.id"), nullable=True))
    op.add_column("run_events", sa.Column("session_id", sa.Integer(), sa.ForeignKey("monitor_sessions.id"), nullable=True))
    op.create_unique_constraint("uq_opportunity_session_item", "opportunities", ["session_id", "item_id"])
    op.create_index(
        "uq_opportunities_monitor_item",
        "opportunities",
        ["source_id", "item_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NULL"),
    )
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
    op.create_index(
        "uq_runs_running_monitor",
        "runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NULL AND status = 'running' AND finished_at IS NULL"),
    )

    op.drop_constraint("fk_opportunities_last_run_id", "opportunities", type_="foreignkey")
    op.drop_column("opportunities", "last_run_id")
    op.drop_column("opportunities", "last_scraped_at")
