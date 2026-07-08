import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest

from vinted_monitor.core.config import Settings
from vinted_monitor.providers import vinted_catalog as catalog_provider
from vinted_monitor.providers.browser_profiles import get_profile_by_name
from vinted_monitor.providers.datadome import (
    DataDomeChallengeError,
    extract_datadome_client_key,
    extract_datadome_cookie_from_response_cookie,
    extract_datadome_tags_version,
    is_datadome_challenge,
)
from vinted_monitor.providers.ephemeral_http import CHROME120_ACCEPT_ENCODING, CHROME120_SEC_CH_UA, CHROME120_UA
from vinted_monitor.providers.vinted_catalog import (
    CurlCffiVintedCatalogProvider,
    VintedCatalogProviderError,
    VintedCatalogRateLimitError,
    build_catalog_api_params,
    decode_next_flight_payload,
    extract_csrf_token,
    map_catalog_item,
    parse_catalog_api_payload,
    parse_catalog_html,
    parse_item_detail_html,
    sanitize_catalog_item,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vinted_catalog_payload.json"
class FakeResponse:
    def __init__(self, status_code: int = 200, *, text: str = "", json_data: dict | None = None, headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.headers = headers or {}

    def json(self) -> dict:
        return self._json_data or {}


class FakeCurlSession:
    def __init__(self, handler, calls: list[dict], *, impersonate=None, proxies=None) -> None:
        self.handler = handler
        self.calls = calls
        self.impersonate = impersonate
        self.proxies = proxies
        self.cookies: dict[str, str] = {}
        self.closed = False

    def get(self, url, *, params=None, headers=None, timeout=None, default_headers=None):
        call = {
            "method": "GET",
            "url": url,
            "params": params or {},
            "headers": headers or {},
            "timeout": timeout,
            "default_headers": default_headers,
            "impersonate": self.impersonate,
            "proxies": self.proxies,
            "cookies": dict(self.cookies),
        }
        self.calls.append(call)
        response = self.handler(call)
        self._store_response_cookies(response)
        return response

    def post(self, url, *, data=None, headers=None, timeout=None, default_headers=None):
        call = {
            "method": "POST",
            "url": url,
            "data": data or {},
            "headers": headers or {},
            "timeout": timeout,
            "default_headers": default_headers,
            "impersonate": self.impersonate,
            "proxies": self.proxies,
            "cookies": dict(self.cookies),
        }
        self.calls.append(call)
        response = self.handler(call)
        self._store_response_cookies(response)
        return response

    def _store_response_cookies(self, response: FakeResponse) -> None:
        set_cookie_header = response.headers.get("set-cookie") or response.headers.get("Set-Cookie")
        if isinstance(set_cookie_header, str):
            set_cookie_values = [set_cookie_header]
        else:
            set_cookie_values = list(set_cookie_header or [])
        for set_cookie in set_cookie_values:
            name, _, remainder = set_cookie.partition("=")
            value = remainder.split(";", 1)[0]
            self.cookies[name] = value

    def close(self) -> None:
        self.closed = True


def fake_session_factory(handler, calls: list[dict]):
    def factory(*, impersonate=None, proxies=None):
        return FakeCurlSession(handler, calls, impersonate=impersonate, proxies=proxies)

    return factory


def test_curl_provider_defaults_to_configured_chrome120_profile() -> None:
    captured_sessions: list[dict] = []

    def factory(*, impersonate=None, proxies=None):
        captured_sessions.append({"impersonate": impersonate, "proxies": proxies})
        return FakeCurlSession(lambda _call: FakeResponse(200), [], impersonate=impersonate, proxies=proxies)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(curl_impersonate_browser="chrome120"),
        session_factory=factory,
    )
    provider._ensure_session()

    assert provider.profile.name == "chrome_120_win10"
    assert provider.profile.user_agent == CHROME120_UA
    assert provider.profile.sec_ch_ua == CHROME120_SEC_CH_UA
    assert provider.profile.build_bootstrap_headers()["accept-encoding"] == CHROME120_ACCEPT_ENCODING
    assert provider.profile.build_api_headers("https://www.vinted.es/catalog")["accept-encoding"] == CHROME120_ACCEPT_ENCODING
    assert captured_sessions == [{"impersonate": "chrome120", "proxies": None}]


def test_curl_provider_default_runtime_profile_is_chrome149_without_env_file() -> None:
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(_env_file=None),
        session_factory=lambda **_: FakeCurlSession(lambda _call: FakeResponse(200), []),
    )

    assert provider.profile.name == "chrome_149_win10"
    assert provider.profile.impersonate == "chrome149"


def test_chrome120_runtime_headers_are_ordered_and_do_not_force_hop_by_hop_headers() -> None:
    profile = get_profile_by_name("chrome_120_win10")
    assert profile is not None

    bootstrap_headers = profile.build_bootstrap_headers(referer="https://www.vinted.es/")
    api_headers = profile.build_api_headers("https://www.vinted.es/catalog?search_text=tommy")

    assert list(bootstrap_headers) == [
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "sec-fetch-user",
        "upgrade-insecure-requests",
        "user-agent",
        "referer",
    ]
    assert list(api_headers) == [
        "accept",
        "accept-encoding",
        "accept-language",
        "cache-control",
        "pragma",
        "referer",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
        "sec-fetch-dest",
        "sec-fetch-mode",
        "sec-fetch-site",
        "user-agent",
    ]
    assert "connection" not in bootstrap_headers
    assert "te" not in bootstrap_headers
    assert "connection" not in api_headers
    assert "te" not in api_headers


def test_chrome149_runtime_headers_match_observed_catalog_flow() -> None:
    profile = get_profile_by_name("chrome_149_win10")
    assert profile is not None

    bootstrap_headers = profile.build_bootstrap_headers()
    api_headers = profile.build_api_headers("https://www.vinted.es/catalog?catalog[]=2050")

    assert profile.impersonate == "chrome149"
    assert profile.user_agent.endswith("Chrome/149.0.0.0 Safari/537.36")
    assert bootstrap_headers["sec-ch-ua"] == '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"'
    assert bootstrap_headers["accept-language"] == "es-ES,es;q=0.9,en;q=0.8"
    assert bootstrap_headers["priority"] == "u=0, i"
    assert bootstrap_headers["cache-control"] == "no-cache"
    assert bootstrap_headers["pragma"] == "no-cache"
    assert api_headers["accept"] == "application/json,text/plain,*/*,image/webp"
    assert api_headers["locale"] == "es-ES"
    assert api_headers["priority"] == "u=3"
    assert api_headers["referer"] == "https://www.vinted.es/catalog?catalog[]=2050"


@pytest.fixture(autouse=True)
def no_provider_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vinted_monitor.providers.vinted_catalog.human_delay", lambda *args, **kwargs: 0.0)


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def build_next_flight_html(payload: dict) -> str:
    flight_payload = json.dumps(
        {
            "items": {
                "items": payload["items"],
                "pagination": payload["pagination"],
            }
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    escaped_payload = json.dumps(flight_payload, ensure_ascii=False)[1:-1]
    return f'<html><body><script>self.__next_f.push([1,"{escaped_payload}"])</script></body></html>'


def build_next_flight_chunk(payload: str) -> str:
    escaped_payload = json.dumps(payload, ensure_ascii=False)[1:-1]
    return f'<script>self.__next_f.push([1,"{escaped_payload}"])</script>'


def source(url: str = "https://www.vinted.es/catalog?catalog[]=76&order=newest_first"):
    return type("Source", (), {"url": url})()


def path(call: dict) -> str:
    return urlparse(call["url"]).path


def test_parse_catalog_html_maps_items_and_pagination() -> None:
    fixture = load_fixture()
    result = parse_catalog_html(build_next_flight_html(fixture))

    assert len(result.items) == 2
    assert result.page == 1
    assert result.total_pages == 3
    assert result.total_entries == 192
    assert result.per_page == 96
    assert result.next_page == 2
    assert result.provider_metadata == {"source": "next_flight_html"}


def test_build_catalog_api_params_translates_public_catalog_url_and_forces_newest_order() -> None:
    params = build_catalog_api_params(
        "https://www.vinted.es/catalog?catalog[]=76&brand_ids[]=88&brand_ids[]=364&size_ids[]=208&status_ids[]=1"
        "&price_from=0.00&price_to=5.00&currency=EUR&order=relevance",
        page=None,
        per_page=5,
    )

    assert params == {
        "page": 1,
        "per_page": 5,
        "order": "newest_first",
        "price_from": "0.00",
        "price_to": "5.00",
        "currency": "EUR",
        "catalog_ids": "76",
        "brand_ids": "88,364",
        "size_ids": "208",
        "status_ids": "1",
    }


def test_build_catalog_api_params_ignores_non_filter_page_time_and_order() -> None:
    params = build_catalog_api_params(
        "https://www.vinted.es/catalog?catalog[]=76&page=3&time=1783419579&order=relevance",
        page=None,
        per_page=5,
    )

    assert params == {
        "page": 1,
        "per_page": 5,
        "order": "newest_first",
        "catalog_ids": "76",
    }


def test_parse_catalog_api_payload_maps_items_and_provider_metadata() -> None:
    fixture = load_fixture()
    result = parse_catalog_api_payload(fixture)

    assert len(result.items) == 2
    assert result.page == 1
    assert result.per_page == 96
    assert result.provider_metadata == {"source": "catalog_api_json"}


def test_extract_csrf_token_from_catalog_document_variants() -> None:
    assert extract_csrf_token('{"CSRF_TOKEN":"csrf-secret-value"}') == "csrf-secret-value"
    assert extract_csrf_token(r'\"csrfToken\":\"csrf-secret-value\"') == "csrf-secret-value"
    assert extract_csrf_token(r'headers.set(\"X-CSRF-Token\",\"csrf-secret-value\")') == "csrf-secret-value"
    assert extract_csrf_token("<html>no token</html>") is None


def test_extract_datadome_bootstrap_metadata() -> None:
    html = (
        '<script src="https://static-assets.vinted.com/datadome/5.7.0/tags.js"></script>'
        '<script>window.ddjskey="TESTDATADOMEKEY1234567890";</script>'
    )

    assert extract_datadome_tags_version(html) == "5.7.0"
    assert extract_datadome_client_key(html) == "TESTDATADOMEKEY1234567890"
    assert extract_datadome_cookie_from_response_cookie("datadome=dd-cookie-secret; Path=/; Secure") == "dd-cookie-secret"
    assert extract_datadome_cookie_from_response_cookie("session=other; Path=/") is None


def test_datadome_challenge_detection_uses_signals_not_status_only() -> None:
    assert is_datadome_challenge(
        429,
        {"content-type": "application/json"},
        '{"error":"rate_limited"}',
    ) is False
    assert is_datadome_challenge(
        403,
        {"x-datadome-traffic-rule-response": "captcha"},
        "",
    ) is True
    assert is_datadome_challenge(
        200,
        {"set-cookie": "datadome=dd-cookie-secret; Path=/; Secure"},
        "",
    ) is False
    assert is_datadome_challenge(
        403,
        {"set-cookie": "datadome=dd-cookie-secret; Path=/; Secure"},
        "",
    ) is True
    assert is_datadome_challenge(
        200,
        {"set-cookie": "datadome=; Max-Age=-1; Path=/;"},
        "",
    ) is False
    assert is_datadome_challenge(
        403,
        {"content-type": "text/html"},
        "<html>geo.captcha-delivery.com</html>",
    ) is True


def test_retry_after_parser_supports_missing_seconds_http_date_and_invalid_values() -> None:
    now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
    retry_at = now + timedelta(seconds=7)

    assert catalog_provider._retry_after_seconds(None, now=now) == (5.0, "missing")
    assert catalog_provider._retry_after_seconds("2", now=now) == (2.0, "seconds")
    assert catalog_provider._retry_after_seconds(format_datetime(retry_at), now=now) == (7.0, "http_date")
    assert catalog_provider._retry_after_seconds("soon", now=now) == (None, "invalid")


def test_parse_catalog_html_allows_empty_catalog_results() -> None:
    result = parse_catalog_html(
        build_next_flight_html(
            {
                "items": [],
                "pagination": {
                    "current_page": 1,
                    "total_pages": 1,
                    "total_entries": 0,
                    "per_page": 96,
                },
            }
        )
    )

    assert result.items == []
    assert result.page == 1
    assert result.total_entries == 0
    assert result.next_page is None


def test_decode_next_flight_payload_concatenates_multiple_chunks() -> None:
    html = f"<html><body>{build_next_flight_chunk('first')}{build_next_flight_chunk('second')}</body></html>"

    assert decode_next_flight_payload(html) == "firstsecond"


def test_map_catalog_item_maps_observed_fields() -> None:
    item = map_catalog_item(load_fixture()["items"][0])

    assert item.vinted_item_id == "1000000001"
    assert item.title == "Polo Ralph Lauren de prueba"
    assert item.brand == "Ralph Lauren"
    assert item.price_amount == Decimal("2.50")
    assert item.currency == "EUR"
    assert item.size == "M"
    assert item.status == "Satisfactorio"
    assert item.seller_login == "fixture_seller"
    assert item.seller_country is None
    assert item.favorite_count == 2
    assert item.url == "https://www.vinted.es/items/1000000001-polo-ralph-lauren-de-prueba"
    assert item.image_url == "https://images.example.test/item-1000000001.webp"


def test_map_catalog_item_allows_missing_optional_fields() -> None:
    item = map_catalog_item(load_fixture()["items"][1])

    assert item.vinted_item_id == "1000000002"
    assert item.brand is None
    assert item.size is None
    assert item.status is None
    assert item.seller_login is None
    assert item.favorite_count is None
    assert item.image_url is None


def test_sanitize_catalog_item_keeps_only_safe_public_fields() -> None:
    raw = {
        **load_fixture()["items"][0],
        "search_tracking_params": {"secret": "do-not-store"},
        "user": {"id": 123, "login": "fixture_seller", "profile_url": "https://example.test/member/123"},
    }

    sanitized = sanitize_catalog_item(raw)

    assert sanitized["user"] == {"login": "fixture_seller"}
    assert "search_tracking_params" not in sanitized
    assert "profile_url" not in sanitized["user"]


def test_curl_provider_uses_catalog_api_after_anonymous_bootstrap() -> None:
    calls: list[dict] = []
    fixture = load_fixture()

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": ["access_token_web=anon; Path=/;", "datadome=dd-secret-value; Path=/;"],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            assert call["params"]["per_page"] == 5
            assert call["params"]["order"] == "newest_first"
            assert call["headers"]["accept"] == "application/json,text/plain,*/*,image/webp"
            assert call["headers"]["x-csrf-token"] == "csrf-secret-value"
            assert call["headers"]["x-anon-id"] == "anon-secret-value"
            assert call["headers"]["locale"] == "es-ES"
            assert "x-screen" not in call["headers"]
            assert call["headers"]["priority"] == "u=3"
            assert call["headers"]["referer"] == source().url
            assert call["default_headers"] is False
            assert call["cookies"]["access_token_web"] == "anon"
            return FakeResponse(200, json_data=fixture, headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip", curl_impersonate_browser="chrome149"),
        session_factory=fake_session_factory(handler, calls),
    )
    result = provider.search(source())

    assert len(result.items) == 2
    assert [path(call) for call in calls] == ["/ip", "/catalog", "/api/v2/catalog/items"]
    assert "referer" not in calls[1]["headers"]
    assert calls[1]["default_headers"] is False


def test_curl_provider_emits_safe_session_and_catalog_events() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": ["access_token_web=access-secret-value; Path=/;", "datadome=public-marker; Path=/;"],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    provider.search(source())

    phases = [event["phase"] for event in events]
    assert phases == [
        "http_session_created",
        "egress_diagnostic_start",
        "egress_diagnostic_success",
        "anonymous_session_bootstrap_start",
        "anonymous_session_bootstrap_success",
        "human_delay_applied",
        "catalog_session_context_ready",
        "catalog_api_request_start",
        "catalog_api_request_success",
    ]
    assert events[0]["details"]["http_session"]["masked"]
    assert events[3]["details"]["bootstrap_origin"] == "catalog_document"
    assert events[4]["details"]["datadome_cookie"] is True
    assert "bootstrap_duration_ms" in events[4]["details"]
    assert events[4]["details"]["csrf_token_found"] is True
    assert events[4]["details"]["anon_id_found"] is True
    assert events[4]["details"]["access_token_found"] is True
    assert events[4]["details"]["v_udt_found"] is True
    assert events[6]["details"]["egress_country_match"] is True
    assert events[7]["details"]["csrf_token_found"] is True
    assert events[7]["details"]["anon_id_found"] is True
    assert events[7]["details"]["browser_profile"] == provider.profile.name
    serialized = json.dumps(events)
    assert "public-marker" not in serialized
    assert "access-secret-value" not in serialized
    assert "csrf-secret-value" not in serialized
    assert "anon-secret-value" not in serialized
    assert "udt-secret-value" not in serialized


def test_curl_provider_diagnoses_egress_with_same_session_and_safe_markers() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES", "connection": {"asn": 64500, "org": "Test ISP"}},
                headers={"content-type": "application/json", "set-cookie": "diagnostic_cookie=diag-secret-value; Path=/;"},
            )
        if path(call) == "/catalog":
            assert call["cookies"]["diagnostic_cookie"] == "diag-secret-value"
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": ["access_token_web=anonymous-secret-value; Path=/;", "datadome=dd-secret-value; Path=/;"],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            assert call["cookies"]["access_token_web"] == "anonymous-secret-value"
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    proxy_session = {
        "kind": "proxy_session",
        "name": "proxy_sticky_session_id",
        "masked": "abcd****wxyz",
        "length": 36,
        "fingerprint": "sha256:test",
    }
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
        proxy_session_marker=proxy_session,
        event_sink=lambda **event: events.append(event),
    )

    provider.search(source())

    assert [path(call) for call in calls] == ["/ip", "/catalog", "/api/v2/catalog/items"]
    egress_event = next(event for event in events if event["phase"] == "egress_diagnostic_success")
    assert egress_event["status_code"] == 200
    assert egress_event["duration_ms"] is not None
    assert egress_event["details"]["egress"] == {
        "ip": "203.0.113.10",
        "country": "Spain",
        "country_code": "ES",
        "asn": 64500,
        "org": "Test ISP",
    }
    assert egress_event["details"]["proxy_session"] == proxy_session
    assert "diag-secret-value" not in json.dumps(events)
    assert "anonymous-secret-value" not in json.dumps(events)
    assert "csrf-secret-value" not in json.dumps(events)


def test_curl_provider_blocks_catalog_api_when_session_context_is_incomplete() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(200, text='{"CSRF_TOKEN":"csrf-secret-value"}', headers={"x-anon-id": "anon-secret-value"})
        if path(call) == "/api/v2/catalog/items":
            raise AssertionError("catalog API must not be called with incomplete session context")
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    with pytest.raises(VintedCatalogProviderError, match="Catalog session context incomplete"):
        provider.search(source())

    assert [path(call) for call in calls] == ["/catalog"]
    incomplete_event = next(event for event in events if event["phase"] == "catalog_session_context_incomplete")
    assert set(incomplete_event["details"]["missing_required"]) >= {"access_token_web", "datadome", "v_udt", "egress_country_code"}


def test_curl_provider_catalog_api_probe_calls_api_with_incomplete_datadome_context() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": "access_token_web=access-secret-value; Path=/;",
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            assert call["headers"]["x-csrf-token"] == "csrf-secret-value"
            assert call["headers"]["x-anon-id"] == "anon-secret-value"
            assert call["default_headers"] is False
            assert call["cookies"]["access_token_web"] == "access-secret-value"
            assert "datadome" not in call["cookies"]
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
    )

    probe = provider.probe_catalog_api(source().url)

    assert [path(call) for call in calls] == ["/ip", "/catalog", "/api/v2/catalog/items"]
    assert probe["outcome"] == "accepted_json"
    assert probe["status_code"] == 200
    assert probe["response"]["items_count"] == 2
    assert "datadome" in probe["missing_required"]
    serialized_probe = json.dumps(probe)
    assert "access-secret-value" not in serialized_probe
    assert "csrf-secret-value" not in serialized_probe
    assert "anon-secret-value" not in serialized_probe
    assert "udt-secret-value" not in serialized_probe


def test_curl_provider_catalog_api_probe_reports_challenge_without_raising() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": "access_token_web=access-secret-value; Path=/;",
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(
                403,
                text="<html>geo.captcha-delivery.com datadome=raw-secret</html>",
                headers={"content-type": "text/html", "set-cookie": "datadome=raw-secret; Path=/;"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
    )

    probe = provider.probe_catalog_api(source().url)

    assert probe["outcome"] == "challenge"
    assert probe["status_code"] == 403
    assert "body_snippet" in probe["response"]
    assert "raw-secret" not in json.dumps(probe)


def test_curl_provider_catalog_api_probe_reports_transport_error() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": "access_token_web=access-secret-value; Path=/;",
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            raise RuntimeError("proxy timeout datadome=raw-secret")
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
    )

    probe = provider.probe_catalog_api(source().url)

    assert probe["outcome"] == "transport_error"
    assert probe["status_code"] is None
    assert "raw-secret" not in json.dumps(probe)


def test_curl_provider_preflight_collector_marks_session_ready_when_cookie_returned() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text=(
                    '<script src="https://static-assets.vinted.com/datadome/5.7.0/tags.js"></script>'
                    '{"CSRF_TOKEN":"csrf-secret-value"}'
                ),
                headers={
                    "set-cookie": "access_token_web=access-secret-value; Path=/;",
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/datadome/5.7.0/tags.js":
            assert call["method"] == "GET"
            assert call["headers"]["sec-fetch-dest"] == "script"
            assert call["headers"]["sec-fetch-site"] == "cross-site"
            assert call["default_headers"] is False
            return FakeResponse(200, text='window.ddk="TESTDATADOMEKEY1234567890";', headers={"content-type": "application/javascript"})
        if path(call) == "/js":
            assert call["method"] == "POST"
            assert call["data"]["jsType"] == "ch"
            assert call["data"]["ddv"] == "5.7.0"
            assert call["data"]["ddk"] == "TESTDATADOMEKEY1234567890"
            assert call["headers"]["sec-fetch-site"] == "cross-site"
            assert call["headers"]["accept"] == "*/*"
            assert call["headers"]["priority"] == "u=1, i"
            assert call["default_headers"] is False
            return FakeResponse(
                200,
                json_data={"status": 200, "cookie": "datadome=dd-cookie-secret; Path=/; Secure; SameSite=Lax"},
                headers={"content-type": "application/json"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    report = provider.bootstrap_for_session(source().url, collect_datadome=True)
    prepared = provider.export_prepared_session(proxy_session_id="pytestproxy01")

    assert report["datadome_cookie"] is True
    assert prepared.datadome == "dd-cookie-secret"
    assert prepared.cookies["datadome"] == "dd-cookie-secret"
    assert [path(call) for call in calls] == ["/ip", "/catalog", "/datadome/5.7.0/tags.js", "/js"]
    phases = [event["phase"] for event in events]
    assert "datadome_tags_request_start" in phases
    assert "datadome_tags_request_success" in phases
    assert "datadome_collector_start" in phases
    assert "datadome_collector_attempt_start" in phases
    assert "datadome_collector_attempt_success" in phases
    assert phases[-1] == "datadome_collector_success"
    serialized_events = json.dumps(events)
    assert "dd-cookie-secret" not in serialized_events
    assert "TESTDATADOMEKEY1234567890" not in serialized_events
    assert "access-secret-value" not in serialized_events
    assert "csrf-secret-value" not in serialized_events
    assert "anon-secret-value" not in serialized_events
    assert "udt-secret-value" not in serialized_events


def test_curl_provider_preflight_collector_tries_le_after_ch_without_cookie() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text=(
                    '<script src="https://static-assets.vinted.com/datadome/5.7.0/tags.js"></script>'
                    '<script>window.ddjskey="TESTDATADOMEKEY1234567890";</script>'
                    '{"CSRF_TOKEN":"csrf-secret-value"}'
                ),
                headers={
                    "set-cookie": "access_token_web=access-secret-value; Path=/;",
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/js":
            if call["data"]["jsType"] == "ch":
                return FakeResponse(200, json_data={"status": 200, "cid": "collector-cid"}, headers={"content-type": "application/json"})
            return FakeResponse(
                200,
                json_data={"status": 200, "cookie": "datadome=dd-cookie-secret; Path=/; Secure"},
                headers={"content-type": "application/json"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
    )

    report = provider.bootstrap_for_session(source().url, collect_datadome=True)

    assert report["datadome_cookie"] is True
    assert [call["data"]["jsType"] for call in calls if path(call) == "/js"] == ["ch", "le"]


def test_curl_provider_preflight_collector_keeps_incomplete_when_no_cookie_returned() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text=(
                    '<script src="https://static-assets.vinted.com/datadome/5.7.0/tags.js"></script>'
                    '<script>window.ddjskey="TESTDATADOMEKEY1234567890";</script>'
                    '{"CSRF_TOKEN":"csrf-secret-value"}'
                ),
                headers={
                    "set-cookie": "access_token_web=access-secret-value; Path=/;",
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/js":
            return FakeResponse(200, json_data={"status": 200}, headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    report = provider.bootstrap_for_session(source().url, collect_datadome=True)
    prepared = provider.export_prepared_session(proxy_session_id="pytestproxy01")

    assert report["datadome_cookie"] is False
    assert "datadome" in provider._missing_session_context(report)
    assert prepared.datadome is None
    assert [call["data"]["jsType"] for call in calls if path(call) == "/js"] == ["ch", "le"]
    assert [event["phase"] for event in events][-1] == "datadome_collector_failed"
    assert [event["phase"] for event in events].count("datadome_collector_attempt_failed") == 2


def test_curl_provider_preflight_collector_skips_when_base_context_is_incomplete() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text=(
                    '<script src="https://static-assets.vinted.com/datadome/5.7.0/tags.js"></script>'
                    '<script>window.ddjskey="TESTDATADOMEKEY1234567890";</script>'
                    '{"CSRF_TOKEN":"csrf-secret-value"}'
                ),
                headers={"x-anon-id": "anon-secret-value", "x-user-iso-locale": "ES", "x-screen": "catalog"},
            )
        if path(call) == "/js":
            raise AssertionError("DataDome collector must not run before base context is complete")
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    report = provider.bootstrap_for_session(source().url, collect_datadome=True)

    assert report["datadome_cookie"] is False
    assert {call["method"] for call in calls} == {"GET"}
    skipped = next(event for event in events if event["phase"] == "datadome_collector_skipped")
    assert skipped["details"]["reason"] == "base_context_incomplete"
    assert set(skipped["details"]["missing_required"]) >= {"access_token_web", "v_udt"}


def test_curl_provider_emits_detail_http_error_with_duration_on_network_failure() -> None:
    calls: list[dict] = []
    events: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])

    def handler(_call: dict) -> FakeResponse:
        raise RuntimeError("detail network boom")

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    with pytest.raises(VintedCatalogProviderError):
        provider.fetch_detail(candidate)

    phases = [event["phase"] for event in events]
    assert phases == ["http_session_created", "detail_http_request_start", "detail_http_request_error"]
    error_event = events[-1]
    assert error_event["duration_ms"] is not None
    assert error_event["details"]["vinted_item_id"] == candidate.vinted_item_id
    assert error_event["details"]["http_session"]["masked"]


def test_curl_provider_uses_only_explicit_proxy() -> None:
    captured_proxies: list[dict | None] = []

    def factory(*, impersonate=None, proxies=None):
        captured_proxies.append(proxies)
        return FakeCurlSession(lambda _call: FakeResponse(200), [], impersonate=impersonate, proxies=proxies)

    CurlCffiVintedCatalogProvider(settings=Settings(), session_factory=factory)._ensure_session()
    CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        proxy_url="http://user:pass@proxy.example:8000",
        session_factory=factory,
    )._ensure_session()

    assert captured_proxies == [None, {"https": "http://user:pass@proxy.example:8000", "http": "http://user:pass@proxy.example:8000"}]


def test_curl_provider_refreshes_anonymous_session_once_after_auth_failure() -> None:
    calls: list[dict] = []
    api_calls = 0
    bootstrap_calls = 0
    sessions = 0

    def handler(call: dict) -> FakeResponse:
        nonlocal api_calls, bootstrap_calls
        if path(call) == "/catalog":
            bootstrap_calls += 1
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=fresh; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            api_calls += 1
            if api_calls == 1:
                return FakeResponse(401, json_data={"error": "invalid_authentication_token"}, headers={"content-type": "application/json"})
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    def factory(*, impersonate=None, proxies=None):
        nonlocal sessions
        sessions += 1
        return FakeCurlSession(handler, calls, impersonate=impersonate, proxies=proxies)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=factory,
        require_complete_session_context=False,
    )

    result = provider.search(source())

    assert len(result.items) == 2
    assert api_calls == 2
    assert bootstrap_calls == 2
    assert sessions == 1
    assert provider.prepared_session_refreshed is True


def test_curl_provider_respects_retry_after_before_silent_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    sleeps: list[float] = []
    api_calls = 0
    bootstrap_calls = 0

    monkeypatch.setattr("vinted_monitor.providers.vinted_catalog._rate_limit_jitter_seconds", lambda: 0.0)
    monkeypatch.setattr("vinted_monitor.providers.vinted_catalog.time.sleep", lambda seconds: sleeps.append(seconds))

    def handler(call: dict) -> FakeResponse:
        nonlocal api_calls, bootstrap_calls
        if path(call) == "/catalog":
            bootstrap_calls += 1
            if bootstrap_calls == 2:
                assert call["cookies"]["access_token_web"] == "initial"
            return FakeResponse(
                200,
                text=f'{{"CSRF_TOKEN":"csrf-{bootstrap_calls}"}}',
                headers={
                    "set-cookie": f"access_token_web={'initial' if bootstrap_calls == 1 else 'fresh'}; Path=/;",
                    "x-anon-id": f"anon-{bootstrap_calls}",
                    "x-v-udt": f"udt-{bootstrap_calls}",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            api_calls += 1
            if api_calls == 1:
                return FakeResponse(
                    429,
                    text='{"error":"rate_limited"}',
                    headers={"content-type": "application/json", "Retry-After": "2"},
                )
            assert call["headers"]["x-csrf-token"] == "csrf-2"
            assert call["headers"]["x-anon-id"] == "anon-2"
            assert call["default_headers"] is False
            assert call["cookies"]["access_token_web"] == "fresh"
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    result = provider.search(source())

    assert len(result.items) == 2
    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items", "/catalog", "/api/v2/catalog/items"]
    assert sleeps == [2.0]
    assert api_calls == 2
    assert provider.prepared_session_refreshed is True


def test_curl_provider_does_not_refresh_when_retry_after_exceeds_budget() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=initial; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(
                429,
                text='{"error":"rate_limited"}',
                headers={"content-type": "application/json", "Retry-After": "120"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    with pytest.raises(VintedCatalogRateLimitError):
        provider.search(source())

    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items"]
    assert provider.prepared_session_refreshed is False


def test_curl_provider_does_not_refresh_invalid_retry_after() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=initial; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(
                429,
                text='{"error":"rate_limited"}',
                headers={"content-type": "application/json", "Retry-After": "soon"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    with pytest.raises(VintedCatalogRateLimitError):
        provider.search(source())

    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items"]


def test_curl_provider_raises_after_second_session_failure() -> None:
    calls: list[dict] = []
    api_calls = 0
    bootstrap_calls = 0

    def handler(call: dict) -> FakeResponse:
        nonlocal api_calls, bootstrap_calls
        if path(call) == "/catalog":
            bootstrap_calls += 1
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=fresh; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            api_calls += 1
            return FakeResponse(401, json_data={"error": "invalid_authentication_token"}, headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    with pytest.raises(VintedCatalogProviderError):
        provider.search(source())

    assert api_calls == 2
    assert bootstrap_calls == 2


def test_curl_provider_does_not_use_catalog_html_fallback_after_api_failure() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text=build_next_flight_html(load_fixture()),
                headers={"set-cookie": "access_token_web=anon; Path=/;"},
            )
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(500, json_data={"error": "boom"}, headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    with pytest.raises(VintedCatalogProviderError):
        provider.search(source())

    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items", "/api/v2/catalog/items"]


def test_curl_provider_raises_datadome_challenge_before_parsing_catalog() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "datadome=ok; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(200, text="<html>geo.captcha-delivery.com</html>", headers={"content-type": "text/html"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    with pytest.raises(DataDomeChallengeError):
        provider.search(source())


def test_curl_provider_standard_flow_visits_catalog_document_then_api() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES"},
                headers={"content-type": "application/json"},
            )
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": ["access_token_web=anon; Path=/;", "datadome=ok; Path=/;"],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
    )

    provider.search(source())

    assert [path(call) for call in calls] == ["/ip", "/catalog", "/api/v2/catalog/items"]
    assert "referer" not in calls[1]["headers"]
    assert calls[1]["default_headers"] is False
    assert calls[2]["headers"]["referer"] == source().url
    assert calls[2]["default_headers"] is False


def test_get_profile_by_name_scans_all_profiles() -> None:
    assert get_profile_by_name("chrome_142_win10").name == "chrome_142_win10"
    assert get_profile_by_name("missing") is None


def test_parse_item_detail_html_extracts_sanitized_public_detail() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    product_json = {
        "@type": "Product",
        "description": "Tiene una mancha pequena en la manga",
        "color": "Azul",
        "category": "Polos",
        "image": ["https://images.example.test/full-1.webp", "https://images.example.test/full-2.webp"],
        "offers": {"availability": "https://schema.org/InStock"},
    }
    embedded = {
        "item": {
            "shipping_price": {"amount": "2.99"},
            "buyer_protection_fee": {"amount": "0.70"},
            "total_price": {"amount": "6.19"},
            "seller_rating": "4.8",
            "seller_badges": [{"title": "Very responsive"}],
            "is_visible": True,
            "photos": [{"url": "https://images.example.test/full-3.webp"}],
        }
    }
    html = (
        '<script type="application/ld+json">'
        f"{json.dumps(product_json)}"
        "</script>"
        f"<script>window.__detail={json.dumps(embedded)}</script>"
    )

    detail = parse_item_detail_html(html, candidate)

    assert detail.description == "Tiene una mancha pequena en la manga"
    assert detail.color == "Azul"
    assert detail.category == "Polos"
    assert detail.shipping_price_amount == Decimal("2.99")
    assert detail.buyer_protection_fee_amount == Decimal("0.70")
    assert detail.total_price_amount == Decimal("6.19")
    assert detail.seller_rating == Decimal("4.8")
    assert detail.seller_badges == ["Very responsive"]
    assert detail.availability_flags["is_visible"] is True
    assert detail.photos == [
        "https://images.example.test/full-1.webp",
        "https://images.example.test/full-2.webp",
        "https://images.example.test/full-3.webp",
        "https://images.example.test/item-1000000001.webp",
    ]
