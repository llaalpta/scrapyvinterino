from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.db.models import SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.monitor_sessions import stop_active_monitor_session
from vinted_monitor.services.run_events import record_run_event
from vinted_monitor.services.scheduler_liveness import scheduler_worker_availability
from vinted_monitor.services.seen_cache import SeenCacheUnavailableError, get_seen_cache
from vinted_monitor.services.task_queue import TaskQueueError, cancel_ready_task_for_source

WORKER_UNAVAILABLE_STOP_REASON = "scheduler_worker_unavailable"


class SchedulerWatchdog:
    """Fail closed when the scheduler producer heartbeat expires."""

    def __init__(
        self,
        settings: Settings,
        *,
        started_at: datetime | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self.started_at = started_at or self._clock()
        self.logger = structlog.get_logger()

    def run_once(self, now: datetime | None = None) -> list[int]:
        initial_time = now or self._clock()
        startup_grace = timedelta(seconds=self.settings.scheduler_watchdog_startup_grace_seconds)
        if initial_time < self.started_at + startup_grace:
            return []

        with SessionLocal() as db:
            if scheduler_worker_availability(db, self.settings, now=initial_time).available:
                return []

            sources = list(
                db.scalars(
                    select(SearchSource)
                    .where(
                        SearchSource.is_active.is_(True),
                        SearchSource.archived_at.is_(None),
                        SearchSource.monitor_mode != "manual",
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )

            # A producer may recover while this transaction waits for source locks.
            recheck_time = self._clock()
            db.expire_all()
            if scheduler_worker_availability(db, self.settings, now=recheck_time).available:
                return []

            stopped_ids: list[int] = []
            for source in sources:
                source.is_active = False
                source.monitor_started_at = None
                source.next_run_at = None
                source.monitor_until = None
                stop_active_monitor_session(
                    db,
                    source.id,
                    stopped_at=recheck_time,
                    reason=WORKER_UNAVAILABLE_STOP_REASON,
                )
                record_run_event(
                    db,
                    source_id=source.id,
                    phase=WORKER_UNAVAILABLE_STOP_REASON,
                    level="warning",
                    message="Stopped recurring monitor because the scheduler worker heartbeat expired",
                    details={
                        "heartbeat_timeout_seconds": self.settings.scheduler_worker_heartbeat_timeout_seconds,
                    },
                )
                stopped_ids.append(source.id)
            db.commit()

        for source_id in stopped_ids:
            self._cancel_ready_task(source_id)
        if stopped_ids:
            self.logger.warning("scheduler_watchdog_stopped_monitors", source_ids=stopped_ids)
        return stopped_ids

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception as exc:
                self.logger.critical(
                    "scheduler_watchdog_crashed",
                    error=redact_sensitive_text(str(exc)),
                )
                raise
            self._sleep(max(self.settings.scheduler_watchdog_poll_interval_seconds, 1))

    def _cancel_ready_task(self, source_id: int) -> None:
        try:
            cache = get_seen_cache(self.settings)
            cancel_ready_task_for_source(
                cache.client,
                source_id,
                queue_key=self.settings.worker_task_queue_key,
            )
        except (SeenCacheUnavailableError, TaskQueueError) as exc:
            self.logger.warning(
                "scheduler_watchdog_ready_task_cancel_failed",
                source_id=source_id,
                error=redact_sensitive_text(str(exc)),
            )


def main() -> None:
    settings = get_settings()
    SchedulerWatchdog(settings).run_forever()


if __name__ == "__main__":
    main()
