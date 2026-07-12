"""remove persisted response body snippets from run events

Revision ID: 0015_redact_event_bodies
Revises: 0014_add_item_view_count
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015_redact_event_bodies"
down_revision: str | None = "0014_add_item_view_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE run_events
        SET details = (details - 'body_snippet') #- '{response,body_snippet}'
        WHERE details ? 'body_snippet'
           OR (details->'response') ? 'body_snippet'
        """
    )


def downgrade() -> None:
    # Redacted response content must never be restored.
    pass
