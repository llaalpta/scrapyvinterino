"""Remove obsolete direct catalog scheduler settings.

Revision ID: 0022_proxy_only_catalog_egress
Revises: 0021_remove_scheduler_ui_gate
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022_proxy_only_catalog_egress"
down_revision: str | None = "0021_remove_scheduler_ui_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE app_settings "
        "SET value = value - 'allow_direct_without_proxy' - 'direct_max_concurrent_runs' "
        "WHERE key = 'scheduler' "
        "AND (value ? 'allow_direct_without_proxy' OR value ? 'direct_max_concurrent_runs')"
    )


def downgrade() -> None:
    # Removed operator choices cannot be reconstructed honestly. Older code
    # falls back to its own defaults if this migration is reversed.
    pass
