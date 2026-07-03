import time

import structlog

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.logging import configure_logging
from vinted_monitor.services.scheduler import SchedulerConfigError, validate_proxy_settings
from vinted_monitor.worker.scheduler import SchedulerRunner


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger()
    logger.info("worker_started", scheduler_enabled=settings.scheduler_enabled)

    try:
        validate_proxy_settings(settings)
    except SchedulerConfigError as exc:
        logger.error("worker_config_error", error=str(exc))
        while True:
            time.sleep(30)
            logger.info("worker_heartbeat", scheduler_available=False)

    runner = SchedulerRunner(settings)
    runner.run_forever()


if __name__ == "__main__":
    main()
