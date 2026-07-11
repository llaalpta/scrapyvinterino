import logging

import structlog

from vinted_monitor.core.redaction import redact_sensitive_value


def _redact_event(_logger, _method_name: str, event_dict: dict) -> dict:
    redacted = redact_sensitive_value(event_dict)
    return redacted if isinstance(redacted, dict) else {"event": str(redacted)}


def configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    logging.getLogger("redis.client").setLevel(logging.WARNING)
    logging.getLogger("redis.cluster").setLevel(logging.WARNING)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
            _redact_event,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
