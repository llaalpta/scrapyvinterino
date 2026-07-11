import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from curl_cffi.requests import Cookies

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.vinted_catalog import (
    CurlCffiVintedCatalogProvider,
    PreparedCatalogSession,
    VintedCatalogProviderError,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vinted_catalog_payload.json"


class _Response:
    def __init__(self, payload: dict, *, url: str) -> None:
        self.status_code = 200
        self.text = json.dumps(payload)
        self.headers = {"content-type": "application/json"}
        self.url = url
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, payload: dict) -> None:
        self.cookies: dict[str, str] = {}
        self.payload = payload
        self.calls: list[dict] = []

    def get(self, url: str, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return _Response(self.payload, url="https://attacker.example/catalog-data")

    def close(self) -> None:
        return None


class _BootstrapSession:
    def __init__(self, canary: str) -> None:
        self.cookies: dict[str, str] = {}
        self.canary = canary

    def get(self, url: str, **kwargs):
        response = _Response({}, url="https://attacker.example/catalog")
        response.text = f'<script>{{"CSRF_TOKEN":"{self.canary}"}}</script>'
        response.headers = {
            "content-type": "text/html",
            "x-anon-id": self.canary,
            "x-v-udt": self.canary,
        }
        return response

    def close(self) -> None:
        return None


class _CookieSession:
    def __init__(self) -> None:
        self.cookies = Cookies()

    def close(self) -> None:
        return None


class _NeverCalledSession:
    def __init__(self) -> None:
        self.cookies = Cookies()
        self.cookies.set("datadome", "prepared-cookie-canary", domain=".vinted.es", path="/", secure=True)
        self.calls = 0

    def get(self, url: str, **kwargs):
        self.calls += 1
        raise AssertionError(f"unsafe URL reached transport: {url}")

    def close(self) -> None:
        return None


def test_catalog_rejects_cross_origin_effective_response_before_parsing() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    session = _Session(payload)
    canary = "AUDIT-CATALOG-HEADER-CANARY-6631"
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=lambda **_: session,
        request_retries=0,
        prepared_session=PreparedCatalogSession(
            cookies={},
            csrf_token=canary,
            anon_id=canary,
        ),
        require_complete_session_context=False,
        require_datadome_cookie=False,
    )
    source = SimpleNamespace(
        url="https://www.vinted.es/catalog?order=newest_first",
        normalized_query={"order": ["newest_first"]},
    )

    with pytest.raises(VintedCatalogProviderError):
        provider.search(source)

    assert session.calls[0]["headers"]["x-csrf-token"] == canary
    assert session.calls[0]["headers"]["x-anon-id"] == canary


def test_bootstrap_rejects_cross_origin_response_before_refreshing_session_context() -> None:
    canary = "AUDIT-BOOTSTRAP-CONTEXT-CANARY-91ef"
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=lambda **_: _BootstrapSession(canary),
        request_retries=0,
        require_complete_session_context=False,
        require_datadome_cookie=False,
    )

    with pytest.raises(VintedCatalogProviderError):
        provider.bootstrap_for_session("https://www.vinted.es/catalog")

    assert provider._bootstrapped is False
    assert provider.prepared_session_refreshed is False
    assert canary not in str(provider._session_context_values())


@pytest.mark.parametrize(
    "source_url",
    [
        "http://www.vinted.es/catalog",
        "https://www.vinted.es:443/catalog",
        "https://user:secret@www.vinted.es/catalog",
    ],
)
def test_bootstrap_rejects_unsafe_initial_url_before_cookie_bearing_transport(source_url: str) -> None:
    session = _NeverCalledSession()
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=lambda **_: session,
        request_retries=0,
        require_complete_session_context=False,
        require_datadome_cookie=False,
    )

    with pytest.raises(VintedCatalogProviderError):
        provider.bootstrap_for_session(source_url)

    assert session.calls == 0


def test_prepared_session_cookies_are_scoped_to_vinted_and_foreign_cookies_are_not_exported() -> None:
    canary = "AUDIT-FOREIGN-COOKIE-CANARY-72a1"
    session = _CookieSession()
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=lambda **_: session,
        prepared_session=PreparedCatalogSession(cookies={"datadome": "prepared-public-cookie"}),
        require_complete_session_context=False,
        require_datadome_cookie=False,
    )

    provider._ensure_session()
    loaded_cookie_domains = {cookie.domain.lstrip(".") for cookie in session.cookies.jar}
    session.cookies.set("foreign_cookie", canary, domain="attacker.example", path="/")
    exported = provider.export_prepared_session()

    assert loaded_cookie_domains <= {"vinted.es", "www.vinted.es"}
    assert "foreign_cookie" not in exported.cookies
    assert canary not in str(exported.cookies)
