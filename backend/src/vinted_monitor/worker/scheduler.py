from __future__ import annotations

import random
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import Run, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler import (
    SchedulerCapacityError,
    active_run_egress_counts,
    choose_run_egress,
    get_scheduler_state,
    get_scheduler_timezone,
    is_within_allowed_windows,
    list_schedulable_sources,
    next_run_after,
    source_config,
)
from vinted_monitor.services.scheduler_liveness import touch_scheduler_worker_heartbeat
from vinted_monitor.services.seen_cache import get_seen_cache
from vinted_monitor.services.task_queue import MonitorTask, enqueue_task, pending_tasks, processing_queue_key


class SchedulerRunner:
    """Producer: evaluates which monitors are due and enqueues tasks to Redis.

    The scheduler no longer executes HTTP requests or monitor runs directly.
    It builds ``MonitorTask`` payloads and pushes them into the Redis task
    queue. The ``TaskConsumer`` workers reserve them into a processing list.
    """

    def __init__(
        self,
        settings: Settings,
        rng: random.Random | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.settings = settings
        self.rng = rng or random.Random()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self.timezone = get_scheduler_timezone(settings)
        self.next_due_by_source_id: dict[int, datetime] = {}
        self._last_heartbeat_at: datetime | None = None
        self.logger = structlog.get_logger()

    def run_once(self, now: datetime | None = None) -> list[int]:
        """Evaluate due monitors and enqueue tasks.  Return submitted source IDs."""
        current_time = now or self._clock()

        self._write_heartbeat_if_due(current_time)
        with SessionLocal() as db:
            state = get_scheduler_state(db, self.settings, now=current_time)
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
                # PostgreSQL owns scheduling state. The dictionary is only an
                # observable mirror and must never override a newer persisted
                # activation deadline.
                due_at = source.next_run_at or current_time
                self.next_due_by_source_id[source.id] = due_at
                config = source_config(source)
                if due_at <= current_time:
                    if not is_within_allowed_windows(current_time, config.allowed_windows, self.timezone):
                        next_due = next_run_after(current_time, config, self.rng, self.timezone)
                        self.next_due_by_source_id[source.id] = next_due
                        source.next_run_at = next_due
                        continue
                    due_sources.append((due_at, source.id, source, config))

            # Window deferrals are durable scheduling decisions and must not be
            # rolled back by a later cache or egress failure in this cycle.
            db.commit()

            cache = get_seen_cache()
            active_proxy_counts = active_run_egress_counts(db)
            active_task_ids = set(
                db.scalars(
                    select(Run.task_id).where(
                        Run.status == "running",
                        Run.finished_at.is_(None),
                        Run.task_id.is_not(None),
                    )
                )
            )
            queue_key = self.settings.worker_task_queue_key
            consumer_processing_keys = (
                processing_queue_key(queue_key),
                *(
                    processing_queue_key(queue_key, consumer_id)
                    for consumer_id in range(max(self.settings.worker_consumer_count, 1))
                ),
            )
            queued_tasks = pending_tasks(
                cache.client,
                queue_key=queue_key,
                processing_keys=consumer_processing_keys,
            )
            pending_by_source_id = {task.source_id: task for task in queued_tasks}
            for queued_task in queued_tasks:
                if queued_task.task_id in active_task_ids:
                    continue
                active_proxy_counts[queued_task.proxy_profile_id] = (
                    active_proxy_counts.get(queued_task.proxy_profile_id, 0) + 1
                )
            for _, source_id, _source, _config in sorted(due_sources, key=lambda e: (e[0], e[1])):
                source = db.scalar(
                    select(SearchSource)
                    .where(
                        SearchSource.id == source_id,
                        SearchSource.is_active.is_(True),
                        SearchSource.archived_at.is_(None),
                        SearchSource.monitor_mode != "manual",
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if source is None:
                    db.commit()
                    self.next_due_by_source_id.pop(source_id, None)
                    continue
                config = source_config(source)
                locked_due_at = source.next_run_at or current_time
                self.next_due_by_source_id[source_id] = locked_due_at
                if locked_due_at > current_time:
                    db.commit()
                    continue
                if not is_within_allowed_windows(current_time, config.allowed_windows, self.timezone):
                    next_due = next_run_after(current_time, config, self.rng, self.timezone)
                    self.next_due_by_source_id[source_id] = next_due
                    source.next_run_at = next_due
                    db.commit()
                    continue
                pending_task = pending_by_source_id.get(source_id)
                if pending_task is not None:
                    next_due = next_run_after(current_time, config, self.rng, self.timezone)
                    self.next_due_by_source_id[source_id] = next_due
                    source.next_run_at = next_due
                    self.logger.info(
                        "scheduler_task_coalesced",
                        source_id=source_id,
                        task_id=pending_task.task_id,
                        reason="monitor_task_already_pending",
                    )
                    db.commit()
                    continue
                if sum(active_proxy_counts.values()) >= state.max_concurrent_runs:
                    db.commit()
                    break

                # Proxy identity ownership must always precede the source row
                # lock. Profile edits take the advisory lock first and then
                # lock affected sources while purging sessions; retaining this
                # source lock through choose_run_egress would invert that order
                # and deadlock a concurrent identity edit.
                db.commit()
                try:
                    egress = choose_run_egress(
                        db,
                        self.settings,
                        active_proxy_counts=active_proxy_counts,
                    )
                except SchedulerCapacityError:
                    db.rollback()
                    break

                # The source may have changed while its first row lock was
                # released. Revalidate every scheduling predicate under the
                # final source lock while the proxy advisory fence is held.
                source = db.scalar(
                    select(SearchSource)
                    .where(
                        SearchSource.id == source_id,
                        SearchSource.is_active.is_(True),
                        SearchSource.archived_at.is_(None),
                        SearchSource.monitor_mode != "manual",
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if source is None:
                    db.commit()
                    self.next_due_by_source_id.pop(source_id, None)
                    continue
                config = source_config(source)
                locked_due_at = source.next_run_at or current_time
                self.next_due_by_source_id[source_id] = locked_due_at
                if locked_due_at > current_time:
                    db.commit()
                    continue
                if not is_within_allowed_windows(current_time, config.allowed_windows, self.timezone):
                    next_due = next_run_after(current_time, config, self.rng, self.timezone)
                    self.next_due_by_source_id[source_id] = next_due
                    source.next_run_at = next_due
                    db.commit()
                    continue

                if egress.proxy_profile_id is None or egress.proxy_identity_generation is None:
                    db.rollback()
                    self.logger.error(
                        "scheduler_proxy_binding_missing",
                        source_id=source_id,
                    )
                    break

                task = MonitorTask(
                    source_id=source_id,
                    source_url=source.url,
                    monitor_mode=source.monitor_mode,
                    trigger="scheduler",
                    scheduler_config={
                        "interval_seconds": config.interval_seconds,
                        "jitter_percent": config.jitter_percent,
                    },
                    proxy_profile_id=egress.proxy_profile_id,
                    proxy_identity_generation=egress.proxy_identity_generation,
                )
                try:
                    redis_client = cache.client
                    enqueued = enqueue_task(redis_client, task, queue_key=self.settings.worker_task_queue_key)
                except Exception as exc:
                    db.rollback()
                    self.logger.error(
                        "scheduler_enqueue_error",
                        source_id=source_id,
                        task_id=task.task_id,
                        error=str(exc),
                    )
                    continue

                next_due = next_run_after(current_time, config, self.rng, self.timezone)
                self.next_due_by_source_id[source_id] = next_due
                source.next_run_at = next_due
                if not enqueued:
                    self.logger.info(
                        "scheduler_task_coalesced",
                        source_id=source_id,
                        task_id=task.task_id,
                        reason="monitor_task_already_pending",
                    )
                    db.commit()
                    continue
                active_proxy_counts[egress.proxy_profile_id] = active_proxy_counts.get(egress.proxy_profile_id, 0) + 1
                submitted.append(source_id)
                pending_by_source_id[source_id] = task
                db.commit()

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
            self._sleep_until_next_poll()

    def _write_heartbeat_if_due(self, current_time: datetime) -> None:
        if (
            self._last_heartbeat_at is not None
            and current_time - self._last_heartbeat_at
            < timedelta(seconds=self.settings.scheduler_worker_heartbeat_interval_seconds)
        ):
            return
        with SessionLocal() as db:
            touch_scheduler_worker_heartbeat(db, now=current_time)
            db.commit()
        self._last_heartbeat_at = current_time

    def _sleep_until_next_poll(self) -> None:
        remaining = float(max(self.settings.scheduler_poll_interval_seconds, 1))
        heartbeat_interval = float(self.settings.scheduler_worker_heartbeat_interval_seconds)
        while remaining > 0:
            delay = min(remaining, heartbeat_interval)
            self._sleep(delay)
            remaining -= delay
            self._write_heartbeat_if_due(self._clock())
