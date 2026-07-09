"""Normalize invalid Vinted session status.

Revision ID: 0011_vinted_status_invalid
Revises: 0010_chrome146_runtime_profile
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011_vinted_status_invalid"
down_revision: str | None = "0010_chrome146_runtime_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE vinted_sessions
        SET status = 'invalid',
            updated_at = now()
        WHERE status = 'invalidated'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE vinted_sessions
        SET status = 'invalidated',
            updated_at = now()
        WHERE status = 'invalid'
          AND invalidated_at IS NOT NULL
        """
    )
