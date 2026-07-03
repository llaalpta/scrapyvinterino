from __future__ import annotations

import random
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime

import structlog

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.runs import SCHEDULER_TRIGGER, execute_monitor_run
from vinted_monitor.services.scheduler import (
    get_scheduler_state,
    get_scheduler_timezone,
    is_within_allowed_windows,
    list_schedulable_sources,
    next_run_after,
    source_config,
)

RunTask = Callable[[int], None]


class BoundedSourceExecutor:
    def __init__(self, max_workers: int, per_source_limit: int = 1) -> None:
        self.max_workers = max(max_workers, 1)
        self.per_source_limit = max(per_source_limit, 1)
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._active: dict[Future[None], int] = {}
        self._active_counts: dict[int, int] = {}
        self._logger = structlog.get_logger()

    @property
    def available_slots(self) -> int:
        return max(self.max_workers - len(self._active), 0)

    def can_submit(self, source_id: int) -> bool:
        return self.available_slots > 0 and self._active_counts.get(source_id, 0) < self.per_source_limit

    def submit(self, source_id: int, task: RunTask) -> bool:
        if not self.can_submit(source_id):
            return False
        future = self._executor.submit(task, source_id)
        self._active[future] = source_id
        self._active_counts[source_id] = self._active_counts.get(source_id, 0) + 1
        return True

    def reap_completed(self) -> None:
        completed = [future for future in self._active if future.done()]
        for future in completed:
            source_id = self._active.pop(future)
            current_count = self._active_counts.get(source_id, 0) - 1
            if current_count <= 0:
                self._active_counts.pop(source_id, None)
            else:
                self._active_counts[source_id] = current_count
            try:
                future.result()
            except Exception as exc:
                self._logger.error("scheduler_task_error", source_id=source_id, error=str(exc))

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True)


class SchedulerRunner:
    def __init__(
        self,
        settings: Settings,
        executor: BoundedSourceExecutor | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self.executor = executor or BoundedSourceExecutor(
            settings.scheduler_max_concurrent_runs,
            settings.scheduler_per_source_concurrency,
        )
        self.rng = rng or random.Random()
        self.timezone = get_scheduler_timezone(settings)
        self.next_due_by_session_id: dict[int, datetime] = {}
        self.logger = structlog.get_logger()

    def run_once(self, now: datetime | None = None) -> list[int]:
        current_time = now or datetime.now(UTC)
        self.executor.reap_completed()

        with SessionLocal() as db:
            state = get_scheduler_state(db, self.settings)
            if not state.effective_enabled:
                return []

            sources = list_schedulable_sources(db)
            source_ids = {source.id for source in sources}
            self.next_due_by_session_id = {
                source_id: due_at
                for source_id, due_at in self.next_due_by_session_id.items()
                if source_id in source_ids
            }

            submitted: list[int] = []
            due_sources = []
            for source in sources:
                due_at = self.next_due_by_session_id.setdefault(source.id, source.next_run_at or current_time)
                config = source_config(source)
                if due_at <= current_time:
                    if not is_within_allowed_windows(current_time, config.allowed_windows, self.timezone):
                        self.next_due_by_session_id[source.id] = next_run_after(current_time, config, self.rng, self.timezone)
                        continue
                    due_sources.append((due_at, source.id, config))

            for _, source_id, config in sorted(due_sources, key=lambda entry: (entry[0], entry[1])):
                if self.executor.available_slots <= 0:
                    break
                if not self.executor.submit(source_id, self._run_source):
                    continue
                next_due = next_run_after(current_time, config, self.rng, self.timezone)
                self.next_due_by_session_id[source_id] = next_due
                source = db.get(SearchSource, source_id)
                if source is not None:
                    source.next_run_at = next_due
                submitted.append(source_id)
            db.commit()

            return submitted

    def run_forever(self) -> None:
        while True:
            try:
                submitted = self.run_once()
                if submitted:
                    self.logger.info("scheduler_submitted_runs", session_ids=submitted)
            except Exception as exc:
                self.logger.error("scheduler_loop_error", error=str(exc))
            time.sleep(max(self.settings.scheduler_poll_interval_seconds, 1))

    def _run_source(self, source_id: int) -> None:
        with SessionLocal() as db:
            run = execute_monitor_run(db, source_id, trigger=SCHEDULER_TRIGGER)
            self.logger.info(
                "scheduler_run_finished",
                source_id=source_id,
                run_id=run.id,
                status=run.status,
                items_found=run.items_found,
                items_new=run.items_new,
            )
