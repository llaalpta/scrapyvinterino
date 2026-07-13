"""Bind prepared sessions to a versioned proxy identity.

Revision ID: 0019_proxy_session_identity
Revises: 0018_local_user_sessions
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_proxy_session_identity"
down_revision: str | None = "0018_local_user_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Development-only rows predate the effective transport/template binding.
    # Their historical identity cannot be reconstructed safely during Alembic.
    op.execute("DELETE FROM vinted_sessions")
    op.add_column(
        "proxy_profiles",
        sa.Column("identity_generation", sa.BigInteger(), nullable=False, server_default="1"),
    )
    op.add_column(
        "proxy_profiles",
        sa.Column("identity_fingerprint", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "vinted_sessions",
        sa.Column("proxy_identity_generation", sa.String(length=100), nullable=False),
    )
    op.drop_index("ix_vinted_sessions_source_proxy_status", table_name="vinted_sessions")
    op.create_index(
        "ix_vinted_sessions_source_proxy_identity_status",
        "vinted_sessions",
        ["source_id", "proxy_profile_id", "proxy_identity_generation", "status"],
    )


def downgrade() -> None:
    # Rows created with the generation-aware contract are unsafe after the
    # binding column is removed. A development downgrade therefore purges them
    # instead of making them reusable by proxy_profile_id alone.
    op.execute("DELETE FROM vinted_sessions")
    op.drop_index("ix_vinted_sessions_source_proxy_identity_status", table_name="vinted_sessions")
    op.create_index(
        "ix_vinted_sessions_source_proxy_status",
        "vinted_sessions",
        ["source_id", "proxy_profile_id", "status"],
    )
    op.drop_column("vinted_sessions", "proxy_identity_generation")
    op.drop_column("proxy_profiles", "identity_fingerprint")
    op.drop_column("proxy_profiles", "identity_generation")
