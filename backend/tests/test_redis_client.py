from __future__ import annotations

from vinted_monitor.core.redis_client import redis_client_from_url


def test_redis_client_uses_resp2_without_maintenance_notifications() -> None:
    client = redis_client_from_url("redis://localhost:6379/0", decode_responses=True)

    assert client.connection_pool.connection_kwargs["protocol"] == 2
    assert client.connection_pool.connection_kwargs["decode_responses"] is True
    assert getattr(client.connection_pool, "_maint_notifications_pool_handler", None) is None

    connection = client.connection_pool.make_connection()
    assert getattr(connection, "protocol", None) == 2
    assert getattr(connection, "maint_notifications_config", None) is None
