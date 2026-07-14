import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta

import structlog

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.logging import configure_logging
from vinted_monitor.core.redaction import redact_sensitive_text
from vinted_monitor.core.redis_client import redis_client_from_url
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler import SchedulerConfigError, validate_proxy_settings
from vinted_monitor.services.scheduler_liveness import scheduler_worker_availability
from vinted_monitor.services.seen_cache import SeenCacheUnavailableError, get_seen_cache
from vinted_monitor.services.task_queue import TaskQueueError, recover_inflight_tasks
from vinted_monitor.worker.consumer import TaskConsumer
from vinted_monitor.worker.scheduler import SchedulerRunner


def main() -> None:
    settings = get_settings()
    started_at = datetime.now(UTC)
    configure_logging(settings.log_level)
    logger = structlog.get_logger()
    logger.info(
        "worker_started",
        scheduler_enabled=settings.scheduler_enabled,
        consumer_count=settings.worker_consumer_count,
        queue_key=settings.worker_task_queue_key,
    )

    try:
        validate_proxy_settings(settings)
    except SchedulerConfigError as exc:
        logger.critical("worker_config_error", error=redact_sensitive_text(str(exc)))
        raise SystemExit(2) from exc

    cache = get_seen_cache(settings)
    queue_client = redis_client_from_url(
        settings.redis_url,
        decode_responses=False,
        socket_timeout=max(settings.worker_reserve_timeout_seconds + 5, 10),
    )
    try:
        cache.require_available()
        recovered_tasks = recover_inflight_tasks(queue_client, queue_key=settings.worker_task_queue_key)
    except (SeenCacheUnavailableError, TaskQueueError) as exc:
        logger.critical("worker_task_recovery_failed", error=str(exc))
        raise
    logger.info(
        "worker_task_recovery_complete",
        queue_key=settings.worker_task_queue_key,
        recovered_tasks=recovered_tasks,
    )

    # Producer: evaluates timing and enqueues tasks
    producer = SchedulerRunner(settings)

    # Consumers: dequeue tasks and execute with anti-bot evasion
    consumers = [
        TaskConsumer(settings, consumer_id=i)
        for i in range(max(settings.worker_consumer_count, 1))
    ]

    total_threads = 1 + len(consumers)
    logger.info("worker_launching_threads", producer=1, consumers=len(consumers), total=total_threads)

    pool = ThreadPoolExecutor(max_workers=total_threads, thread_name_prefix="worker")
    targets = [(producer.run_forever, "producer")]
    targets.extend((consumer.run_forever, f"consumer-{consumer.consumer_id}") for consumer in consumers)
    futures = {
        pool.submit(_run_with_logging, target, name, logger): (target, name)
        for target, name in targets
    }
    while True:
        completed, _ = wait(
            futures,
            timeout=min(max(settings.scheduler_worker_heartbeat_interval_seconds, 1), 5),
            return_when=FIRST_COMPLETED,
        )
        try:
            cache.require_available()
        except Exception as exc:
            logger.critical(
                "worker_redis_healthcheck_failed",
                error=redact_sensitive_text(str(exc)),
            )
            _exit_process(1)
        try:
            producer_expired = _producer_heartbeat_expired(settings, started_at=started_at)
        except Exception as exc:
            logger.critical("worker_producer_healthcheck_failed", error=redact_sensitive_text(str(exc)))
            _exit_process(1)
        if producer_expired:
            logger.critical("worker_producer_heartbeat_expired")
            _exit_process(1)
        for future in completed:
            target, name = futures.pop(future)
            try:
                future.result()
            except Exception:
                pass
            if name == "producer":
                logger.critical("worker_producer_thread_stopped")
                _exit_process(1)
            logger.warning("worker_thread_restarting", thread_name=name)
            time.sleep(1)
            futures[pool.submit(_run_with_logging, target, name, logger)] = (target, name)


def _producer_heartbeat_expired(
    settings: Settings,
    *,
    started_at: datetime,
    now: datetime | None = None,
) -> bool:
    current_time = now or datetime.now(UTC)
    if current_time - started_at < timedelta(seconds=settings.scheduler_worker_heartbeat_timeout_seconds):
        return False
    with SessionLocal() as db:
        return not scheduler_worker_availability(db, settings, now=current_time).available


def _exit_process(code: int) -> None:
    os._exit(code)


def _run_with_logging(target, name: str, logger) -> None:
    """Run a target function with top-level exception logging."""
    try:
        target()
    except Exception as exc:
        logger.critical("worker_thread_crashed", thread_name=name, error=str(exc))
        raise


if __name__ == "__main__":
    main()
