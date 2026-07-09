import logging

from vinted_monitor.core.logging import configure_logging


def test_configure_logging_keeps_redis_operation_debug_logs_quiet() -> None:
    configure_logging("DEBUG")

    assert logging.getLogger("redis.client").getEffectiveLevel() == logging.WARNING
    assert logging.getLogger("redis.cluster").getEffectiveLevel() == logging.WARNING
