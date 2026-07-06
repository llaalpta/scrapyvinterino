"""move filters onto monitor configuration

Revision ID: 0006_monitor_filter_definition
Revises: 0005_global_proxy_pool
Create Date: 2026-07-06 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_monitor_filter_definition"
down_revision: str | None = "0005_global_proxy_pool"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_sources",
        sa.Column(
            "filter_definition",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{\"blacklist_terms\": []}'::jsonb"),
        ),
    )
    op.drop_column("opportunities", "rule_id")
    op.drop_column("search_sources", "filter_rule_ids")
    op.drop_table("filter_rules")


def downgrade() -> None:
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
    op.add_column(
        "search_sources",
        sa.Column("filter_rule_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column("opportunities", sa.Column("rule_id", sa.Integer(), sa.ForeignKey("filter_rules.id")))
    op.drop_column("search_sources", "filter_definition")
