import time

import structlog

from vinted_monitor.core.config import get_settings
from vinted_monitor.core.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = structlog.get_logger()
    logger.info("worker_started", scheduler_enabled=settings.scheduler_enabled)

    while True:
        time.sleep(30)
        logger.info("worker_heartbeat")


if __name__ == "__main__":
    main()
