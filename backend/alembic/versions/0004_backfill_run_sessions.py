"""backfill sessions for orphan runs

Revision ID: 0004_backfill_run_sessions
Revises: 0003_add_monitor_sessions
Create Date: 2026-07-04 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_backfill_run_sessions"
down_revision: str | None = "0003_add_monitor_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            run_record RECORD;
            inserted_session_id INTEGER;
        BEGIN
            FOR run_record IN
                SELECT id, source_id, status, started_at, finished_at
                FROM runs
                WHERE monitor_session_id IS NULL
                ORDER BY started_at ASC, id ASC
            LOOP
                INSERT INTO monitor_sessions (source_id, started_at, stopped_at, stop_reason)
                VALUES (
                    run_record.source_id,
                    run_record.started_at,
                    COALESCE(run_record.finished_at, run_record.started_at),
                    CASE
                        WHEN run_record.status = 'failed' THEN 'failed'
                        WHEN run_record.status = 'running' THEN 'interrupted'
                        ELSE 'completed'
                    END
                )
                RETURNING id INTO inserted_session_id;

                UPDATE runs
                SET monitor_session_id = inserted_session_id
                WHERE id = run_record.id;
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    pass
