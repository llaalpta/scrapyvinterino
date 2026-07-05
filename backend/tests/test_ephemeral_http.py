from __future__ import annotations

import pytest

from vinted_monitor.providers.ephemeral_http import (
    CHROME120_ACCEPT_ENCODING,
    CHROME120_SEC_CH_UA,
    CHROME120_UA,
    EphemeralHttpClientError,
    EphemeralVintedHttpClient,
    chrome120_bootstrap_headers,
)


class FakeResponse:
    def __init__(self, status_code: int = 200, payload: object | None = None, json_error: Exception | None = None) -> None:
        self.status_code = status_code
        self.payload = payload if payload is not None else {"ok": True}
        self.json_error = json_error

    def json(self) -> object:
        if self.json_error:
            raise self.json_error
        return self.payload


class FakeCurlSession:
    def __init__(self, calls: list[dict], response: FakeResponse, *, impersonate=None, proxies=None) -> None:
        self.calls = calls
        self.response = response
        self.impersonate = impersonate
        self.proxies = proxies
        self.closed = False

    def get(self, url, *, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        return self.response

    def close(self) -> None:
        self.closed = True


def fake_session_factory(calls: list[dict], response: FakeResponse):
    sessions: list[FakeCurlSession] = []

    def factory(*, impersonate=None, proxies=None):
        session = FakeCurlSession(calls, response, impersonate=impersonate, proxies=proxies)
        sessions.append(session)
        return session

    return factory, sessions


def test_chrome120_bootstrap_headers_are_coherent() -> None:
    headers = chrome120_bootstrap_headers()

    assert headers["User-Agent"] == CHROME120_UA
    assert headers["sec-ch-ua"] == CHROME120_SEC_CH_UA
    assert headers["sec-ch-ua-platform"] == '"Windows"'
    assert headers["Accept-Encoding"] == CHROME120_ACCEPT_ENCODING
    assert headers["Accept-Language"] == "es-ES,es;q=0.9,en;q=0.8"


def test_ephemeral_client_uses_chrome120_impersonation_and_proxy() -> None:
    calls: list[dict] = []
    factory, sessions = fake_session_factory(calls, FakeResponse())

    client = EphemeralVintedHttpClient(
        base_url="https://www.vinted.es/",
        proxy_url="http://user-session-abc:pass@proxy.example:8000",
        timeout_ms=15000,
        session_factory=factory,
    )

    assert client.base_url == "https://www.vinted.es"
    assert sessions[0].impersonate == "chrome120"
    assert sessions[0].proxies == {
        "http": "http://user-session-abc:pass@proxy.example:8000",
        "https": "http://user-session-abc:pass@proxy.example:8000",
    }

    client.close()
    assert sessions[0].closed is True


def test_get_json_sends_chrome120_headers_and_timeout() -> None:
    calls: list[dict] = []
    factory, _sessions = fake_session_factory(calls, FakeResponse(payload={"headers": {}}))
    client = EphemeralVintedHttpClient(
        base_url="https://www.vinted.es",
        proxy_url=None,
        timeout_ms=12000,
        session_factory=factory,
    )

    payload = client.get_json("https://httpbin.org/headers")

    assert payload == {"headers": {}}
    assert calls[0]["timeout"] == 12
    assert calls[0]["headers"]["User-Agent"] == CHROME120_UA
    assert calls[0]["headers"]["sec-ch-ua"] == CHROME120_SEC_CH_UA
    assert calls[0]["headers"]["Accept-Encoding"] == CHROME120_ACCEPT_ENCODING


def test_get_json_rejects_http_errors() -> None:
    factory, _sessions = fake_session_factory([], FakeResponse(status_code=500))
    client = EphemeralVintedHttpClient(
        base_url="https://www.vinted.es",
        proxy_url=None,
        timeout_ms=15000,
        session_factory=factory,
    )

    with pytest.raises(EphemeralHttpClientError, match="HTTP 500"):
        client.get_json("https://example.test/error")


def test_get_json_requires_object_payload() -> None:
    factory, _sessions = fake_session_factory([], FakeResponse(payload=[]))
    client = EphemeralVintedHttpClient(
        base_url="https://www.vinted.es",
        proxy_url=None,
        timeout_ms=15000,
        session_factory=factory,
    )

    with pytest.raises(EphemeralHttpClientError, match="expected object"):
        client.get_json("https://example.test/list")
