"""Move sticky proxy contract to each profile.

Revision ID: 0024_proxy_sticky_contract
Revises: 0023_remove_proxy_test_telemetry
Create Date: 2026-07-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_proxy_sticky_contract"
down_revision: str | None = "0023_remove_proxy_test_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STICKY_USERNAME_TEMPLATE = "{username};sessid.{session_id}"
STICKY_TTL_MINUTES = 25


def upgrade() -> None:
    op.add_column(
        "proxy_profiles",
        sa.Column("sticky_username_template", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "proxy_profiles",
        sa.Column("sticky_ttl_minutes", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE proxy_profiles
            SET sticky_username_template = :template,
                sticky_ttl_minutes = :ttl
            """
        ).bindparams(template=STICKY_USERNAME_TEMPLATE, ttl=STICKY_TTL_MINUTES)
    )
    op.alter_column("proxy_profiles", "sticky_username_template", nullable=False)
    op.alter_column("proxy_profiles", "sticky_ttl_minutes", nullable=False)
    op.create_check_constraint(
        "ck_proxy_profiles_sticky_ttl_minutes",
        "proxy_profiles",
        "sticky_ttl_minutes BETWEEN 1 AND 120",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_proxy_profiles_sticky_ttl_minutes",
        "proxy_profiles",
        type_="check",
    )
    op.drop_column("proxy_profiles", "sticky_ttl_minutes")
    op.drop_column("proxy_profiles", "sticky_username_template")
