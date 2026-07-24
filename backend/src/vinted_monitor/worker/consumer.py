from __future__ import annotations

import hashlib
import time

import structlog
from redis import Redis

from vinted_monitor.core.config import Settings
from vinted_monitor.core.redis_client import redis_client_from_url
from vinted_monitor.db.models import Run
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import (
    VintedCatalogChallengeError,
    VintedCatalogRateLimitError,
    VintedCatalogSessionContextError,
    VintedCatalogSessionError,
)
from vinted_monitor.services.proxies import mark_proxy_run_failure
from vinted_monitor.services.runs import (
    FAILED,
    FINALIZING,
    SCHEDULER_TRIGGER,
    SUCCESS,
    SearchSourceInactiveError,
    SearchSourceNotFoundError,
    execute_monitor_run,
    recover_task_run_before_delivery,
)
from vinted_monitor.services.scheduler import RunEgress
from vinted_monitor.services.seen_cache import RedisSeenCache, get_seen_cache
from vinted_monitor.services.task_queue import (
    InvalidTaskPayloadError,
    MonitorTask,
    TaskQueueError,
    TaskReservation,
    ack_task,
    dead_letter_task,
    processing_queue_key,
    recover_inflight_tasks,
    requeue_task,
    reserve_task,
)
from vinted_monitor.services.vinted_sessions import VintedSessionRequiredError


def _untrusted_task_id_fingerprint(task_id: str | None) -> str | None:
    if not isinstance(task_id, str) or not task_id:
        return None
    return hashlib.sha256(task_id.encode("utf-8", errors="replace")).hexdigest()[:16]


class TaskConsumer:
    """Consumer worker: dequeues tasks from Redis and processes them fail-stop.

    Each consumed task delegates provider creation to ``execute_monitor_run``.
    The run factory resolves a prepared persistent Vinted session for the
    selected proxy. Classified response failures are terminal and acknowledged;
    unexpected worker failures retain the bounded delivery-recovery path.
    """

    def __init__(self, settings: Settings, consumer_id: int = 0) -> None:
        self.settings = settings
        self.consumer_id = consumer_id
        self.logger = structlog.get_logger().bind(consumer_id=consumer_id)

    def run_forever(self) -> None:
        """Reserve reliable FIFO work and process tasks indefinitely."""
        self.logger.info("consumer_started", queue_key=self.settings.worker_task_queue_key)
        cache = get_seen_cache(self.settings)
        queue_client = redis_client_from_url(
            self.settings.redis_url,
            decode_responses=False,
            socket_timeout=max(self.settings.worker_reserve_timeout_seconds + 5, 10),
        )
        consumer_processing_key = processing_queue_key(
            self.settings.worker_task_queue_key,
            self.consumer_id,
        )
        self._recover_processing_queue(queue_client, consumer_processing_key)
        while True:
            try:
                reservation = reserve_task(
                    queue_client,
                    timeout=self.settings.worker_reserve_timeout_seconds,
                    queue_key=self.settings.worker_task_queue_key,
                    consumer_id=self.consumer_id,
                )
            except InvalidTaskPayloadError as exc:
                self._dead_letter_invalid_task(queue_client, exc)
                continue
            except TaskQueueError as exc:
                self.logger.error("consumer_reserve_error", error=str(exc))
                self._recover_processing_queue(queue_client, consumer_processing_key)
                time.sleep(1)
                continue
            except Exception as exc:
                self.logger.error("consumer_reserve_unexpected_error", error=str(exc))
                self._recover_processing_queue(queue_client, consumer_processing_key)
                time.sleep(1)
                continue

            if reservation is None:
                continue

            self._consume_reservation(cache, reservation, queue_client=queue_client)

    def _consume_reservation(
        self,
        cache: RedisSeenCache,
        reservation: TaskReservation,
        *,
        queue_client: Redis | None = None,
    ) -> None:
        task = reservation.task
        resolved_queue_client = queue_client or cache.client
        self.logger.info(
            "consumer_task_received",
            source_id=task.source_id,
            task_id=task.task_id,
            trigger=task.trigger,
        )
        try:
            with SessionLocal() as db:
                previous_run = recover_task_run_before_delivery(
                    db,
                    source_id=task.source_id,
                    task_id=task.task_id,
                    seen_cache=cache,
                )
            next_attempt = self._next_recovery_attempt(previous_run)
            if previous_run is not None and next_attempt is None:
                self.logger.info(
                    "consumer_task_already_terminal",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    run_id=previous_run.id,
                    status=previous_run.status,
                )
            elif next_attempt is not None:
                self._process_with_escalation(
                    task,
                    first_attempt=next_attempt,
                    authoritative_egress=_durable_run_egress(previous_run),
                )
            else:
                self._process_with_escalation(task)
        except Exception as exc:
            self.logger.info(
                "consumer_task_requeue",
                source_id=task.source_id,
                task_id=task.task_id,
                error=str(exc),
            )
            requeued = self._retry_requeue(resolved_queue_client, reservation)
            if not requeued:
                self.logger.error(
                    "consumer_task_requeue_missing_reservation",
                    source_id=task.source_id,
                    task_id=task.task_id,
                )
            return

        acknowledged = self._retry_ack(resolved_queue_client, reservation)
        if not acknowledged:
            self.logger.error(
                "consumer_task_ack_missing_reservation",
                source_id=task.source_id,
                task_id=task.task_id,
            )
            return
        self.logger.info(
            "consumer_task_acknowledged",
            source_id=task.source_id,
            task_id=task.task_id,
        )

    def _dead_letter_invalid_task(self, queue_client: Redis, exc: InvalidTaskPayloadError) -> None:
        while True:
            try:
                moved = dead_letter_task(
                    queue_client,
                    exc.raw_queue_payload,
                    queue_key=self.settings.worker_task_queue_key,
                    source_id=exc.source_id,
                    task_id=exc.task_id,
                    processing_key_override=exc.processing_key
                    or processing_queue_key(self.settings.worker_task_queue_key, self.consumer_id),
                )
                break
            except TaskQueueError as dead_letter_exc:
                self.logger.error("consumer_dead_letter_error", error=str(dead_letter_exc))
                time.sleep(1)
        self.logger.error(
            "consumer_invalid_task_dead_lettered",
            error=str(exc),
            moved=moved,
            source_id=exc.source_id,
            task_id_fingerprint=_untrusted_task_id_fingerprint(exc.task_id),
        )

    def _recover_processing_queue(self, queue_client: Redis, consumer_processing_key: str) -> None:
        while True:
            try:
                recovered = recover_inflight_tasks(
                    queue_client,
                    queue_key=self.settings.worker_task_queue_key,
                    processing_keys=(consumer_processing_key,),
                )
                if recovered:
                    self.logger.warning(
                        "consumer_ambiguous_reservation_recovered",
                        recovered_tasks=recovered,
                    )
                return
            except TaskQueueError as exc:
                self.logger.error("consumer_reservation_recovery_error", error=str(exc))
                time.sleep(1)

    def _retry_ack(self, queue_client: Redis, reservation: TaskReservation) -> bool:
        while True:
            try:
                return ack_task(
                    queue_client,
                    reservation,
                    queue_key=self.settings.worker_task_queue_key,
                )
            except TaskQueueError as exc:
                self.logger.error(
                    "consumer_task_ack_error",
                    source_id=reservation.task.source_id,
                    task_id=reservation.task.task_id,
                    error=str(exc),
                )
                time.sleep(1)

    def _retry_requeue(self, queue_client: Redis, reservation: TaskReservation) -> bool:
        while True:
            try:
                return requeue_task(
                    queue_client,
                    reservation,
                    queue_key=self.settings.worker_task_queue_key,
                )
            except TaskQueueError as exc:
                self.logger.error(
                    "consumer_task_requeue_error",
                    source_id=reservation.task.source_id,
                    task_id=reservation.task.task_id,
                    error=str(exc),
                )
                time.sleep(1)

    def _process_with_escalation(
        self,
        task: MonitorTask,
        *,
        first_attempt: int = 1,
        authoritative_egress: RunEgress | None = None,
    ) -> None:
        """Process a task; only unexpected worker failures may use another attempt."""
        max_attempts = self.settings.worker_max_retry_attempts
        last_unexpected_error: Exception | None = None
        delivery_egress = authoritative_egress or _task_payload_egress(task)

        for attempt in range(first_attempt, max_attempts + 1):
            self.logger.info(
                "consumer_attempt_start",
                source_id=task.source_id,
                task_id=task.task_id,
                attempt=attempt,
                proxy_profile_id=delivery_egress.proxy_profile_id,
            )

            try:
                if authoritative_egress is None:
                    run = self._execute_run(task, attempt)
                else:
                    run = self._execute_run(
                        task,
                        attempt,
                        authoritative_egress=authoritative_egress,
                    )
                if run.status == FINALIZING:
                    raise TaskRunNotTerminalError(f"Run {run.id} is still finalizing")
                if run.status not in {SUCCESS, FAILED}:
                    raise TaskRunNotTerminalError(f"Run {run.id} has non-terminal status {run.status!r}")
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

            except (
                DataDomeChallengeError,
                VintedCatalogChallengeError,
                VintedCatalogRateLimitError,
                VintedCatalogSessionContextError,
                VintedCatalogSessionError,
            ):
                self.logger.warning(
                    "consumer_catalog_response_fail_stop",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                )
                return
            except TaskRunNotTerminalError:
                raise
            except VintedSessionRequiredError as exc:
                self.logger.warning(
                    "consumer_vinted_session_required",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    error=str(exc),
                )
                return
            except (SearchSourceNotFoundError, SearchSourceInactiveError) as exc:
                self.logger.warning(
                    "consumer_task_no_longer_runnable",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    error=str(exc),
                )
                return
            except Exception as exc:
                last_unexpected_error = exc
                self.logger.error(
                    "consumer_attempt_error",
                    source_id=task.source_id,
                    task_id=task.task_id,
                    attempt=attempt,
                    error=str(exc),
                )
                with SessionLocal() as db:
                    mark_proxy_run_failure(
                        db,
                        delivery_egress.proxy_profile_id,
                    )
                    db.commit()
        # All attempts exhausted
        self.logger.error(
            "consumer_all_attempts_exhausted",
            source_id=task.source_id,
            task_id=task.task_id,
            max_attempts=max_attempts,
        )
        if last_unexpected_error is not None:
            raise last_unexpected_error

    def _next_recovery_attempt(self, previous_run: Run | None) -> int | None:
        if previous_run is None or previous_run.status != FAILED:
            return None
        metadata = previous_run.runtime_metadata or {}
        if metadata.get("failure_kind") not in {
            "worker_task_delivery_interrupted",
        }:
            return None
        if _durable_run_egress(previous_run) is None:
            return None
        previous_attempt = metadata.get("attempt")
        if not isinstance(previous_attempt, int) or isinstance(previous_attempt, bool):
            return None
        next_attempt = previous_attempt + 1
        return next_attempt if next_attempt <= self.settings.worker_max_retry_attempts else None

    def _execute_run(
        self,
        task: MonitorTask,
        attempt: int,
        *,
        authoritative_egress: RunEgress | None = None,
    ) -> Run:
        """Execute the monitor run using the pre-configured provider."""
        egress = authoritative_egress or _task_payload_egress(task)

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
                task_queue_key=self.settings.worker_task_queue_key,
            )
            self.logger.info(
                "consumer_run_finished",
                source_id=task.source_id,
                task_id=task.task_id,
                run_id=run.id,
                status=run.status,
                items_found=run.items_found,
                attempt=attempt,
            )
            return run


def _task_payload_egress(task: MonitorTask) -> RunEgress:
    return RunEgress(
        mode="proxy",
        proxy_profile_id=task.proxy_profile_id,
        proxy_identity_generation=task.proxy_identity_generation,
    )


def _durable_run_egress(run: Run) -> RunEgress | None:
    metadata = run.runtime_metadata or {}
    proxy_profile_id = metadata.get("proxy_profile_id")
    generation = metadata.get("proxy_identity_generation")
    if (
        not isinstance(proxy_profile_id, int)
        or isinstance(proxy_profile_id, bool)
        or proxy_profile_id <= 0
        or not isinstance(generation, str)
        or not generation
    ):
        return None
    return RunEgress(
        mode="proxy",
        proxy_profile_id=proxy_profile_id,
        proxy_identity_generation=generation,
        proxy_name=metadata.get("proxy_name"),
        proxy_kind=metadata.get("proxy_kind"),
    )


class TaskRunNotTerminalError(RuntimeError):
    """Keep a queue reservation recoverable until its SQL/Redis run is terminal."""
