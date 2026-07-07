"""add geo context to proxy profiles

Revision ID: 0007_proxy_geo_context
Revises: 0006_monitor_filter_definition
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_proxy_geo_context"
down_revision: str | None = "0006_monitor_filter_definition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("proxy_profiles", sa.Column("country_code", sa.String(length=2), nullable=False, server_default="ES"))
    op.add_column("proxy_profiles", sa.Column("locale", sa.String(length=20), nullable=False, server_default="es-ES"))
    op.add_column(
        "proxy_profiles",
        sa.Column("accept_language", sa.String(length=120), nullable=False, server_default="es-ES,es;q=0.9,en;q=0.8"),
    )
    op.add_column("proxy_profiles", sa.Column("screen", sa.String(length=40), nullable=False, server_default="1920x1080"))


def downgrade() -> None:
    op.drop_column("proxy_profiles", "screen")
    op.drop_column("proxy_profiles", "accept_language")
    op.drop_column("proxy_profiles", "locale")
    op.drop_column("proxy_profiles", "country_code")
