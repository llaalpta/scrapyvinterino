from __future__ import annotations

import structlog

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import Run
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.services.proxies import mark_proxy_challenge_detected, mark_proxy_run_failure
from vinted_monitor.services.runs import SCHEDULER_TRIGGER, SUCCESS, execute_monitor_run
from vinted_monitor.services.scheduler import RunEgress
from vinted_monitor.services.seen_cache import get_seen_cache
from vinted_monitor.services.task_queue import MonitorTask, TaskQueueError, dequeue_task
from vinted_monitor.services.vinted_sessions import VintedSessionRequiredError


class TaskConsumer:
    """Consumer worker: dequeues tasks from Redis and processes them with anti-bot evasion.

    Each consumed task delegates provider creation to ``execute_monitor_run``.
    The run factory resolves a prepared persistent Vinted session for the
    selected proxy. If no ready session exists, the task fails before catalog
    traffic is sent.
    """

    def __init__(self, settings: Settings, consumer_id: int = 0) -> None:
        self.settings = settings
        self.consumer_id = consumer_id
        self.logger = structlog.get_logger().bind(consumer_id=consumer_id)

    def run_forever(self) -> None:
        """Block on BRPOP and process tasks indefinitely."""
        self.logger.info("consumer_started", queue_key=self.settings.worker_task_queue_key)
        cache = get_seen_cache()
        while True:
            try:
                task = dequeue_task(
                    cache.client,
                    timeout=self.settings.worker_blpop_timeout_seconds,
                    queue_key=self.settings.worker_task_queue_key,
                )
            except TaskQueueError as exc:
                self.logger.error("consumer_dequeue_error", error=str(exc))
                continue
            except Exception as exc:
                self.logger.error("consumer_dequeue_unexpected_error", error=str(exc))
                continue

            if task is None:
                # Timeout; no task available, loop back
                continue

            self.logger.info(
                "consumer_task_received",
                source_id=task.source_id,
                task_id=task.task_id,
                trigger=task.trigger,
            )
            try:
                self._process_with_escalation(task)
            except Exception as exc:
                self.logger.error(
                    "consumer_task_failed",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    error=str(exc),
                )

    def _process_with_escalation(self, task: MonitorTask) -> None:
        """Process a task with retry escalation on DataDome challenges."""
        max_attempts = self.settings.worker_max_retry_attempts

        for attempt in range(1, max_attempts + 1):
            self.logger.info(
                "consumer_attempt_start",
                source_id=task.source_id,
                task_id=task.task_id,
                attempt=attempt,
                proxy_profile_id=task.proxy_profile_id,
            )

            try:
                run = self._execute_run(task, attempt)
                if run.status != SUCCESS:
                    self.logger.warning(
                        "consumer_run_failed_no_retry",
                        source_id=task.source_id,
                        task_id=task.task_id,
                        run_id=run.id,
                        status=run.status,
                        attempt=attempt,
                    )
                # ``execute_monitor_run`` owns success/failure proxy bookkeeping for completed runs.
                return

            except DataDomeChallengeError:
                self.logger.warning(
                    "consumer_datadome_challenge",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                )
                # Penalize proxy with DataDome-specific multiplier
                if task.proxy_profile_id:
                    with SessionLocal() as db:
                        mark_proxy_challenge_detected(
                            db,
                            task.proxy_profile_id,
                            penalty_multiplier=self.settings.datadome_challenge_penalty_multiplier,
                        )
                        db.commit()
            except VintedSessionRequiredError as exc:
                self.logger.warning(
                    "consumer_vinted_session_required",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    error=str(exc),
                )
                return
            except Exception as exc:
                self.logger.error(
                    "consumer_attempt_error",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if task.proxy_profile_id:
                    with SessionLocal() as db:
                        mark_proxy_run_failure(db, task.proxy_profile_id)
                        db.commit()
        # All attempts exhausted
        self.logger.error(
            "consumer_all_attempts_exhausted",
            source_id=task.source_id,
            task_id=task.task_id,
            max_attempts=max_attempts,
        )

    def _execute_run(
        self,
        task: MonitorTask,
        attempt: int,
    ) -> Run:
        """Execute the monitor run using the pre-configured provider."""
        egress = RunEgress(
            mode="proxy" if task.proxy_profile_id else "direct",
            proxy_profile_id=task.proxy_profile_id,
            proxy_url=None,
        )

        trigger = task.trigger or SCHEDULER_TRIGGER

        with SessionLocal() as db:
            run = execute_monitor_run(
                db,
                task.source_id,
                trigger=trigger,
                egress=egress,
                runtime_metadata_extra={
                    "task_id": task.task_id,
                    "consumer_id": self.consumer_id,
                    "attempt": attempt,
                },
            )
            self.logger.info(
                "consumer_run_finished",
                source_id=task.source_id,
                task_id=task.task_id,
                run_id=run.id,
                status=run.status,
                items_found=run.items_found,
                items_new=run.items_new,
                attempt=attempt,
            )
            return run
