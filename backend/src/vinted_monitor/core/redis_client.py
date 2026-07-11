from __future__ import annotations

from typing import Any

import redis

try:
    from redis.maint_notifications import MaintNotificationsConfig
except ImportError:  # pragma: no cover - kept for older redis-py builds.
    MaintNotificationsConfig = None  # type: ignore[assignment]


def redis_client_from_url(
    url: str,
    *,
    decode_responses: bool = True,
    socket_timeout: float | None = 5,
) -> redis.Redis:
    """Create Redis clients consistently for the local Redis OSS runtime.

    redis-py 8 defaults to RESP3 and enables maintenance notifications in
    auto mode. Redis 7 OSS does not support CLIENT MAINT_NOTIFICATIONS, so
    using RESP2 keeps worker logs clean without changing queue/cache data.
    """

    kwargs: dict[str, Any] = {
        "decode_responses": decode_responses,
        "protocol": 2,
        "socket_timeout": socket_timeout,
    }
    if MaintNotificationsConfig is not None:
        kwargs["maint_notifications_config"] = MaintNotificationsConfig(enabled=False)
    return redis.Redis.from_url(url, **kwargs)
