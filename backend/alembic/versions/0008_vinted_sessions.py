"""Add persistent Vinted catalog sessions.

Revision ID: 0008_vinted_sessions
Revises: 0007_proxy_geo_context
Create Date: 2026-07-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_vinted_sessions"
down_revision: str | None = "0007_proxy_geo_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proxy_profiles",
        sa.Column("vinted_screen", sa.String(length=40), nullable=False, server_default="catalog"),
    )
    op.create_table(
        "vinted_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proxy_profile_id", sa.Integer(), nullable=False),
        sa.Column("proxy_session_id", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ready"),
        sa.Column("browser_profile", sa.String(length=80), nullable=False),
        sa.Column("impersonate", sa.String(length=40), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("locale", sa.String(length=20), nullable=False),
        sa.Column("accept_language", sa.String(length=120), nullable=False),
        sa.Column("viewport_size", sa.String(length=40), nullable=False, server_default="1920x1080"),
        sa.Column("vinted_screen", sa.String(length=40), nullable=False, server_default="catalog"),
        sa.Column("egress_ip", sa.String(length=80), nullable=True),
        sa.Column("egress_country_code", sa.String(length=2), nullable=True),
        sa.Column("context_encrypted", sa.Text(), nullable=False),
        sa.Column("context_fingerprint", sa.String(length=40), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_requests", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prepared_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["proxy_profile_id"], ["proxy_profiles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vinted_sessions_proxy_status", "vinted_sessions", ["proxy_profile_id", "status"])
    op.create_index("ix_vinted_sessions_ready", "vinted_sessions", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_vinted_sessions_ready", table_name="vinted_sessions")
    op.drop_index("ix_vinted_sessions_proxy_status", table_name="vinted_sessions")
    op.drop_table("vinted_sessions")
    op.drop_column("proxy_profiles", "vinted_screen")
