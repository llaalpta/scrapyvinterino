"""Restore supported Chrome 146 runtime profile.

Revision ID: 0010_chrome146_runtime_profile
Revises: 0009_monitor_vinted_sessions
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_chrome146_runtime_profile"
down_revision: str | None = "0009_monitor_vinted_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE proxy_profiles
        SET accept_language = 'en-GB,en;q=0.9',
            updated_at = now()
        WHERE country_code = 'ES'
          AND accept_language = 'es-ES,es;q=0.9,en;q=0.8'
        """
    )
    op.execute(
        """
        UPDATE vinted_sessions
        SET status = 'invalid',
            invalidated_at = COALESCE(invalidated_at, now()),
            last_error = 'Runtime profile chrome149 is not supported by installed curl_cffi; session invalidated by migration 0010.',
            updated_at = now()
        WHERE status = 'ready'
          AND (
            impersonate = 'chrome149'
            OR browser_profile = 'chrome_149_win10'
            OR (country_code = 'ES' AND accept_language = 'es-ES,es;q=0.9,en;q=0.8')
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE proxy_profiles
        SET accept_language = 'es-ES,es;q=0.9,en;q=0.8',
            updated_at = now()
        WHERE country_code = 'ES'
          AND accept_language = 'en-GB,en;q=0.9'
        """
    )
