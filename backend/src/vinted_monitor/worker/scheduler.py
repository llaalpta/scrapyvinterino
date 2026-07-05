from __future__ import annotations

import random
import time
from datetime import UTC, datetime

import structlog

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler import (
    SchedulerCapacityError,
    choose_run_egress,
    get_scheduler_state,
    get_scheduler_timezone,
    is_within_allowed_windows,
    list_schedulable_sources,
    next_run_after,
    source_config,
)
from vinted_monitor.services.seen_cache import get_seen_cache
from vinted_monitor.services.task_queue import MonitorTask, enqueue_task


class SchedulerRunner:
    """Producer: evaluates which monitors are due and enqueues tasks to Redis.

    The scheduler no longer executes HTTP requests or monitor runs directly.
    It builds ``MonitorTask`` payloads and pushes them into the Redis task
    queue.  The ``TaskConsumer`` workers pick them up via BLPOP.
    """

    def __init__(
        self,
        settings: Settings,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.rng = rng or random.Random()
        self.timezone = get_scheduler_timezone(settings)
        self.next_due_by_source_id: dict[int, datetime] = {}
        self.logger = structlog.get_logger()

    def run_once(self, now: datetime | None = None) -> list[int]:
        """Evaluate due monitors and enqueue tasks.  Return submitted source IDs."""
        current_time = now or datetime.now(UTC)

        with SessionLocal() as db:
            state = get_scheduler_state(db, self.settings)
            if not state.effective_enabled:
                return []

            sources = list_schedulable_sources(db)
            source_ids = {source.id for source in sources}
            self.next_due_by_source_id = {
                source_id: due_at
                for source_id, due_at in self.next_due_by_source_id.items()
                if source_id in source_ids
            }

            submitted: list[int] = []
            due_sources = []
            for source in sources:
                due_at = self.next_due_by_source_id.setdefault(
                    source.id, source.next_run_at or current_time,
                )
                config = source_config(source)
                if due_at <= current_time:
                    if not is_within_allowed_windows(current_time, config.allowed_windows, self.timezone):
                        self.next_due_by_source_id[source.id] = next_run_after(current_time, config, self.rng, self.timezone)
                        continue
                    due_sources.append((due_at, source.id, source, config))

            cache = get_seen_cache()
            for _, source_id, source, config in sorted(due_sources, key=lambda e: (e[0], e[1])):
                try:
                    egress = choose_run_egress(
                        db,
                        self.settings,
                        active_proxy_counts={},
                        active_direct_count=0,
                    )
                except SchedulerCapacityError:
                    break

                # Build proxy URL template for the consumer to inject UUID
                proxy_url_template = egress.proxy_url

                task = MonitorTask(
                    source_id=source_id,
                    source_url=source.url,
                    monitor_mode=source.monitor_mode,
                    trigger="scheduler",
                    filter_rule_ids=list(source.filter_rule_ids or []),
                    scheduler_config={
                        "interval_seconds": config.interval_seconds,
                        "jitter_percent": config.jitter_percent,
                    },
                    proxy_profile_id=egress.proxy_profile_id,
                    proxy_url_template=proxy_url_template,
                )
                try:
                    redis_client = cache.client
                    enqueue_task(redis_client, task, queue_key=self.settings.worker_task_queue_key)
                except Exception as exc:
                    self.logger.error(
                        "scheduler_enqueue_error",
                        source_id=source_id,
                        task_id=task.task_id,
                        error=str(exc),
                    )
                    continue

                next_due = next_run_after(current_time, config, self.rng, self.timezone)
                self.next_due_by_source_id[source_id] = next_due
                source_obj = db.get(SearchSource, source_id)
                if source_obj is not None:
                    source_obj.next_run_at = next_due
                submitted.append(source_id)

            db.commit()
            return submitted

    def run_forever(self) -> None:
        """Loop forever, enqueueing tasks on each poll cycle."""
        while True:
            try:
                submitted = self.run_once()
                if submitted:
                    self.logger.info("scheduler_enqueued_tasks", source_ids=submitted)
            except Exception as exc:
                self.logger.error("scheduler_loop_error", error=str(exc))
            time.sleep(max(self.settings.scheduler_poll_interval_seconds, 1))
