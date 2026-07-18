"""Remove the redundant persisted scheduler UI gate.

Revision ID: 0021_remove_scheduler_ui_gate
Revises: 0020_honest_found_metrics
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021_remove_scheduler_ui_gate"
down_revision: str | None = "0020_honest_found_metrics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE app_settings "
        "SET value = value - 'enabled' "
        "WHERE key = 'scheduler' AND value ? 'enabled'"
    )


def downgrade() -> None:
    # The removed operator choice cannot be reconstructed honestly. Older code
    # therefore falls back to its existing default if this migration is reversed.
    pass
