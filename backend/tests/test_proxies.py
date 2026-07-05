import pytest

from vinted_monitor.core.config import Settings
from vinted_monitor.db.models import ProxyProfile
from vinted_monitor.services.proxies import proxy_url_with_sticky_session


def proxy_profile(username: str | None = "customer-user") -> ProxyProfile:
    return ProxyProfile(
        name="pytest proxy",
        scheme="http",
        kind="residential",
        host="proxy.example",
        port=7777,
        username=username,
        password_encrypted=None,
        max_concurrent_runs=1,
        is_active=True,
    )


def test_proxy_url_with_sticky_session_uses_configured_username_template() -> None:
    url = proxy_url_with_sticky_session(
        proxy_profile(),
        "session-123",
        Settings(proxy_sticky_username_template="{username}-sessid-{session_id}"),
    )

    assert url == "http://customer-user-sessid-session-123:@proxy.example:7777"


def test_proxy_url_with_sticky_session_rejects_template_without_session_id() -> None:
    with pytest.raises(ValueError, match="must include"):
        proxy_url_with_sticky_session(
            proxy_profile(),
            "session-123",
            Settings(proxy_sticky_username_template="{username}"),
        )


def test_proxy_url_with_sticky_session_keeps_plain_proxy_without_username() -> None:
    url = proxy_url_with_sticky_session(proxy_profile(username=None), "session-123", Settings())

    assert url == "http://proxy.example:7777"
