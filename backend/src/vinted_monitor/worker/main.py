import time
from concurrent.futures import ThreadPoolExecutor

import structlog

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.logging import configure_logging
from vinted_monitor.services.scheduler import SchedulerConfigError, validate_proxy_settings
from vinted_monitor.worker.consumer import TaskConsumer
from vinted_monitor.worker.scheduler import SchedulerRunner


def main() -> None:
    settings = get_settings()
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
        logger.error("worker_config_error", error=str(exc))
        while True:
            time.sleep(30)
            logger.info("worker_heartbeat", scheduler_available=False)

    # Producer: evaluates timing and enqueues tasks
    producer = SchedulerRunner(settings)

    # Consumers: dequeue tasks and execute with anti-bot evasion
    consumers = [
        TaskConsumer(settings, consumer_id=i)
        for i in range(max(settings.worker_consumer_count, 1))
    ]

    total_threads = 1 + len(consumers)
    logger.info("worker_launching_threads", producer=1, consumers=len(consumers), total=total_threads)

    with ThreadPoolExecutor(max_workers=total_threads, thread_name_prefix="worker") as pool:
        # Submit producer
        pool.submit(_run_with_logging, producer.run_forever, "producer", logger)
        # Submit consumers
        for consumer in consumers:
            pool.submit(_run_with_logging, consumer.run_forever, f"consumer-{consumer.consumer_id}", logger)

        # Block forever — threads run indefinitely
        while True:
            time.sleep(60)


def _run_with_logging(target, name: str, logger) -> None:
    """Run a target function with top-level exception logging."""
    try:
        target()
    except Exception as exc:
        logger.critical("worker_thread_crashed", thread_name=name, error=str(exc))
        raise


if __name__ == "__main__":
    main()
