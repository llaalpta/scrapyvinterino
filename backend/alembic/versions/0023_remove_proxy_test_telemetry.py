"""Remove obsolete standalone proxy test telemetry.

Revision ID: 0023_remove_proxy_test_telemetry
Revises: 0022_proxy_only_catalog_egress
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_remove_proxy_test_telemetry"
down_revision: str | None = "0022_proxy_only_catalog_egress"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("proxy_profiles", "last_test_error")
    op.drop_column("proxy_profiles", "last_test_ip")
    op.drop_column("proxy_profiles", "last_test_status")


def downgrade() -> None:
    op.add_column("proxy_profiles", sa.Column("last_test_status", sa.String(length=40), nullable=True))
    op.add_column("proxy_profiles", sa.Column("last_test_ip", sa.String(length=80), nullable=True))
    op.add_column("proxy_profiles", sa.Column("last_test_error", sa.Text(), nullable=True))
