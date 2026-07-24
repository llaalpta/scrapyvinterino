import json
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse

import pytest
from curl_cffi.curl import CURL_WRITEFUNC_ERROR
from curl_cffi.requests.exceptions import RequestException

from vinted_monitor.core.config import Settings
from vinted_monitor.providers import vinted_catalog as catalog_provider
from vinted_monitor.providers.browser_profiles import (
    BROWSER_PROFILES,
    get_profile_by_name,
    profile_for_impersonate,
    supported_curl_impersonates,
)
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
    PreparedCatalogSession,
    VintedCatalogChallengeError,
    VintedCatalogProviderError,
    VintedCatalogRateLimitError,
    VintedCatalogSessionError,
    VintedDetailDeferred,
    VintedItemDetailHTTPError,
    VintedItemEarlyDiscard,
    build_catalog_api_params,
    build_item_detail_navigation_url,
    decode_next_flight_payload,
    extract_csrf_token,
    extract_vinted_item_id,
    map_catalog_item,
    parse_catalog_api_payload,
    parse_catalog_html,
    parse_item_detail_html,
    parse_next_flight_records,
    sanitize_catalog_item,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vinted_catalog_payload.json"
DETAIL_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vinted_item_detail_flight.json"


class FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        *,
        text: str = "",
        json_data: dict | None = None,
        headers: dict | None = None,
        url: str | None = None,
        request_size: int = 0,
        upload_size: int = 0,
        header_size: int = 0,
        download_size: int = 0,
        redirect_count: int = 0,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.headers = headers or {}
        self.url = url
        self.request_size = request_size
        self.upload_size = upload_size
        self.header_size = header_size
        self.download_size = download_size
        self.redirect_count = redirect_count

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

    def get(
        self,
        url,
        *,
        params=None,
        headers=None,
        timeout=None,
        default_headers=None,
        allow_redirects=None,
        content_callback=None,
    ):
        call = {
            "method": "GET",
            "url": url,
            "params": params or {},
            "headers": headers or {},
            "timeout": timeout,
            "default_headers": default_headers,
            "allow_redirects": allow_redirects,
            "impersonate": self.impersonate,
            "proxies": self.proxies,
            "cookies": dict(self.cookies),
        }
        self.calls.append(call)
        response = self.handler(call)
        response.url = response.url or url
        self._store_response_cookies(response)
        if content_callback is not None:
            encoded = response.text.encode("utf-8")
            for offset in range(0, len(encoded), 17):
                if content_callback(encoded[offset : offset + 17]) == CURL_WRITEFUNC_ERROR:
                    raise RequestException("expected callback abort", 23, response)
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


def test_curl_provider_default_runtime_profile_is_chrome146_without_env_file() -> None:
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(_env_file=None),
        session_factory=lambda **_: FakeCurlSession(lambda _call: FakeResponse(200), []),
    )

    assert provider.profile.name == "chrome_146_win10"
    assert provider.profile.impersonate == "chrome146"


def test_configured_runtime_profiles_are_supported_by_installed_curl_cffi() -> None:
    installed_targets = supported_curl_impersonates()

    assert installed_targets
    assert {profile.impersonate for profile in BROWSER_PROFILES}.issubset(installed_targets)
    with pytest.raises(ValueError, match="No browser profile configured"):
        profile_for_impersonate("chrome149")


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


def test_chrome146_runtime_headers_match_observed_catalog_flow() -> None:
    profile = get_profile_by_name("chrome_146_win10")
    assert profile is not None

    bootstrap_headers = profile.build_bootstrap_headers()
    api_headers = profile.build_api_headers("https://www.vinted.es/catalog?catalog[]=2050")

    assert profile.impersonate == "chrome146"
    assert profile.user_agent.endswith("Chrome/146.0.0.0 Safari/537.36")
    assert bootstrap_headers["sec-ch-ua"] == '"Not-A.Brand";v="24", "Chromium";v="146"'
    assert bootstrap_headers["accept-language"] == "en-GB,en;q=0.9"
    assert bootstrap_headers["priority"] == "u=0, i"
    assert bootstrap_headers["cache-control"] == "no-cache"
    assert bootstrap_headers["pragma"] == "no-cache"
    assert all(header == header.lower() for header in bootstrap_headers)
    assert not any(header.startswith(":") for header in bootstrap_headers)
    assert "host" not in bootstrap_headers
    assert "cookie" not in bootstrap_headers
    assert "content-length" not in bootstrap_headers
    assert api_headers["accept"] == "application/json,text/plain,*/*,image/webp"
    assert api_headers["locale"] == "es-ES"
    assert api_headers["priority"] == "u=3"
    assert api_headers["referer"] == "https://www.vinted.es/catalog?catalog[]=2050"
    assert all(header == header.lower() for header in api_headers)
    assert not any(header.startswith(":") for header in api_headers)
    assert "host" not in api_headers
    assert "cookie" not in api_headers
    assert "content-length" not in api_headers


@pytest.fixture(autouse=True)
def no_provider_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vinted_monitor.providers.vinted_catalog.human_delay", lambda *args, **kwargs: 0.0)


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def load_detail_fixture() -> dict:
    return json.loads(DETAIL_FIXTURE_PATH.read_text(encoding="utf-8"))


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


def build_item_detail_flight_html(*, product_json: dict | None = None, records: dict | None = None) -> str:
    resolved_records = records if records is not None else load_detail_fixture()["records"]
    flight_payload = "".join(
        f"{record_id}:{json.dumps(record, ensure_ascii=False, separators=(',', ':'))}\n"
        for record_id, record in resolved_records.items()
    )
    split_at = len(flight_payload) // 2
    chunks = build_next_flight_chunk(flight_payload[:split_at]) + build_next_flight_chunk(flight_payload[split_at:])
    json_ld = product_json or {
        "@type": "Product",
        "name": "Titulo JSON-LD de respaldo",
        "description": "Descripcion JSON-LD de respaldo",
        "image": "https://images1.vinted.net/t/json-ld/f800/main.webp?s=signed-json-ld",
        "brand": {"@type": "Brand", "name": "Marca JSON-LD"},
        "offers": {
            "url": "https://www.vinted.es/items/1000000001-polo-ralph-lauren-de-prueba",
            "price": "9.99",
            "priceCurrency": "EUR",
            "availability": "InStock",
        },
        "category": "Hombre Polos",
        "color": "Gris",
    }
    return f'<script type="application/ld+json">{json.dumps(json_ld)}</script>{chunks}'


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
    next_payload = '{"DATADOME_CLIENT_SIDE_KEY":"NEXTDATADOMEKEY1234567890"}'
    escaped_payload = r'{\"DATADOME_CLIENT_SIDE_KEY\":\"ESCAPEDDATADOMEKEY123456\"}'

    assert extract_datadome_tags_version(html) == "5.7.0"
    assert extract_datadome_client_key(html) == "TESTDATADOMEKEY1234567890"
    assert extract_datadome_client_key(next_payload) == "NEXTDATADOMEKEY1234567890"
    assert extract_datadome_client_key(escaped_payload) == "ESCAPEDDATADOMEKEY123456"
    assert extract_datadome_client_key('{"DATADOME_CLIENT_SIDE_KEY":"E6EAF460AA2A8322D66B42C85B62F9"}') == (
        "E6EAF460AA2A8322D66B42C85B62F9"
    )
    assert extract_datadome_client_key('"E6EAF460AA2A8322D66B42C85B62F9"==window.ddjskey') == (
        "E6EAF460AA2A8322D66B42C85B62F9"
    )
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

    assert catalog_provider._retry_after_seconds(None, now=now) == (None, "missing")
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


def test_parse_next_flight_records_uses_dynamic_ids_and_skips_protocol_lines() -> None:
    payload = 'D{"protocol":"metadata"}\n2f:{"value":1}\nk3:["$",null,null,{"itemId":1000000001}]\nbad:not-json\n'

    assert parse_next_flight_records(payload) == {
        "2f": {"value": 1},
        "k3": ["$", None, None, {"itemId": 1000000001}],
    }


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
    assert item.view_count is None
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
    assert item.view_count is None
    assert item.image_url is None


@pytest.mark.parametrize(
    ("raw_view_count", "expected"),
    [(0, 0), (17, 17), ("8", 8), (-1, None), (1.5, None), (True, None), ("invalid", None)],
)
def test_map_catalog_item_validates_optional_view_count(raw_view_count: object, expected: int | None) -> None:
    raw = {**load_fixture()["items"][0], "view_count": raw_view_count}

    item = map_catalog_item(raw)

    assert item.view_count == expected
    assert item.raw["view_count"] == raw_view_count


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
                    "set-cookie": [
                        "access_token_web=anon; Path=/;",
                        "datadome=dd-secret-value; Path=/;",
                        "__cf_bm=cf-secret-value; Path=/;",
                    ],
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
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip", curl_impersonate_browser="chrome146"),
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
                    "set-cookie": [
                        "access_token_web=access-secret-value; Path=/;",
                        "datadome=public-marker; Path=/;",
                        "__cf_bm=cf-secret-value; Path=/;",
                    ],
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


def test_curl_provider_diagnoses_egress_with_isolated_session_and_safe_markers() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/ip":
            assert call["cookies"] == {}
            return FakeResponse(
                200,
                json_data={"ip": "203.0.113.10", "country": "Spain", "country_code": "ES", "connection": {"asn": 64500, "org": "Test ISP"}},
                headers={"content-type": "application/json", "set-cookie": "diagnostic_cookie=diag-secret-value; Path=/;"},
                request_size=100,
                upload_size=20,
                header_size=40,
                download_size=840,
            )
        if path(call) == "/catalog":
            assert "diagnostic_cookie" not in call["cookies"]
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                    "set-cookie": [
                        "access_token_web=anonymous-secret-value; Path=/;",
                        "datadome=dd-secret-value; Path=/;",
                        "__cf_bm=cf-secret-value; Path=/;",
                    ],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
                request_size=200,
                header_size=100,
                download_size=1700,
            )
        if path(call) == "/api/v2/catalog/items":
            assert call["cookies"]["access_token_web"] == "anonymous-secret-value"
            return FakeResponse(
                200,
                json_data=load_fixture(),
                headers={"content-type": "application/json"},
                request_size=300,
                header_size=120,
                download_size=2580,
            )
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
        proxy_url="http://proxy.example:8000",
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
    assert egress_event["details"]["diagnostic_session"] == "isolated"
    assert egress_event["details"]["cookies_sent"] is False
    assert egress_event["details"]["proxy_transfer"] == {
        "category": "egress",
        "observed_requests": 1,
        "unobserved_attempts": 0,
        "request_size_bytes": 100,
        "upload_size_bytes": 20,
        "header_size_bytes": 40,
        "download_size_bytes": 840,
        "total_observed_bytes": 1000,
    }
    bootstrap_event = next(event for event in events if event["phase"] == "anonymous_session_bootstrap_success")
    assert bootstrap_event["details"]["proxy_transfer"]["category"] == "session_setup"
    assert bootstrap_event["details"]["proxy_transfer"]["total_observed_bytes"] == 2000
    catalog_event = next(event for event in events if event["phase"] == "catalog_api_request_success")
    assert catalog_event["details"]["proxy_transfer"]["category"] == "catalog"
    assert catalog_event["details"]["proxy_transfer"]["total_observed_bytes"] == 3000
    assert "diag-secret-value" not in json.dumps(events)
    assert "anonymous-secret-value" not in json.dumps(events)
    assert "csrf-secret-value" not in json.dumps(events)


def test_proxy_egress_probe_classifies_and_redacts_session_constructor_failure() -> None:
    events: list[dict] = []

    def failing_factory(**_kwargs):
        raise RuntimeError("proxy http://qa-user:qa-password@proxy.invalid:8080 failed")

    result = catalog_provider.probe_proxy_egress(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        profile=profile_for_impersonate("chrome146"),
        proxy_url="http://proxy.invalid:8080",
        timeout_ms=1000,
        proxy_session_marker=None,
        expected_country_code="ES",
        event_sink=lambda **event: events.append(event),
        attempt=2,
        session_factory=failing_factory,
    )

    assert result.validated_at is None
    assert result.error is not None
    assert [event["phase"] for event in events] == [
        "egress_diagnostic_start",
        "egress_diagnostic_error",
    ]
    serialized = json.dumps(events)
    assert "qa-user" not in serialized
    assert "qa-password" not in serialized


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
                        "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
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
        event_sink=lambda **event: events.append(event),
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
    probe_start = next(event for event in events if event["phase"] == "catalog_api_probe_start")
    assert probe_start["details"]["request_profile"] == "api_har146"
    assert probe_start["details"]["recovered_context"] == [
        "csrf",
        "anon_id",
        "access_token_web",
        "v_udt",
        "__cf_bm",
        "locale",
        "x_screen",
    ]
    assert "datadome" in probe_start["details"]["missing_context"]
    probe_success = next(event for event in events if event["phase"] == "catalog_api_probe_success")
    assert probe_success["details"]["items_count"] == 2
    assert probe_success["details"]["request_profile"] == "api_har146"


def test_curl_provider_catalog_api_probe_reports_challenge_without_raising() -> None:
    calls: list[dict] = []
    challenge_body = "<html>geo.captcha-delivery.com datadome=raw-secret</html>"

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                        "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(
                403,
                text=challenge_body,
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
    assert "body_snippet" not in probe["response"]
    assert probe["response"]["body_observation"] == {
        "bytes": len(challenge_body.encode("utf-8")),
        "chars": len(challenge_body),
        "sampled_chars": len(challenge_body),
        "looks_like_html": True,
        "looks_like_json": False,
    }
    assert "raw-secret" not in json.dumps(probe)


def test_curl_provider_catalog_api_probe_reports_transport_error() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text='{"CSRF_TOKEN":"csrf-secret-value"}',
                headers={
                        "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
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


def test_extract_vinted_item_id_accepts_id_or_item_url() -> None:
    assert extract_vinted_item_id("9356705635") == "9356705635"
    assert extract_vinted_item_id("https://www.vinted.es/items/9356705635-dead-cowboy?referrer=catalog") == "9356705635"
    assert extract_vinted_item_id("https://www.vinted.es/catalog?search_text=foo") is None


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
                    "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
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
            assert call["data"]["jsType"] in {"ch", "le"}
            assert call["data"]["ddv"] == "5.7.0"
            assert call["data"]["ddk"] == "TESTDATADOMEKEY1234567890"
            if call["data"]["jsType"] == "ch":
                assert call["data"]["eventCounters"] == "[]"
            else:
                event_counters = json.loads(call["data"]["eventCounters"])
                assert event_counters["mousemove"] == 26
                assert event_counters["pointermove"] == 26
                assert event_counters["keydown"] == 0
                assert event_counters["keyup"] == 0
            assert call["headers"]["sec-fetch-site"] == "cross-site"
            assert call["headers"]["accept"] == "*/*"
            assert call["headers"]["priority"] == "u=1, i"
            assert all(header == header.lower() for header in call["headers"])
            assert not any(header.startswith(":") for header in call["headers"])
            assert "host" not in call["headers"]
            assert "cookie" not in call["headers"]
            assert "content-length" not in call["headers"]
            assert call["default_headers"] is False
            return FakeResponse(
                200,
                json_data={"status": 200, "cookie": "datadome=dd-cookie-secret; Path=/; Secure; SameSite=Lax"},
                headers={"content-type": "application/json"},
                request_size=10,
                upload_size=10,
                header_size=10,
                download_size=70,
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
        proxy_url="http://proxy.example:8000",
        event_sink=lambda **event: events.append(event),
    )

    report = provider.bootstrap_for_session(source().url, collect_datadome=True)
    prepared = provider.export_prepared_session(proxy_session_id="pytestproxy01")

    assert report["datadome_cookie"] is True
    assert prepared.datadome == "dd-cookie-secret"
    assert prepared.cookies["datadome"] == "dd-cookie-secret"
    assert [path(call) for call in calls] == ["/ip", "/catalog", "/datadome/5.7.0/tags.js", "/js", "/js"]
    assert [call["data"]["jsType"] for call in calls if path(call) == "/js"] == ["ch", "le"]
    phases = [event["phase"] for event in events]
    assert "datadome_tags_request_start" in phases
    assert "datadome_tags_request_success" in phases
    assert "datadome_collector_start" in phases
    assert "datadome_collector_attempt_start" in phases
    assert "datadome_collector_attempt_success" in phases
    assert phases[-1] == "datadome_collector_success"
    collector_attempts = [event for event in events if event["phase"] == "datadome_collector_attempt_success"]
    assert len(collector_attempts) == 2
    assert all(event["details"]["proxy_transfer"]["category"] == "session_setup" for event in collector_attempts)
    assert all(event["details"]["proxy_transfer"]["total_observed_bytes"] == 100 for event in collector_attempts)
    serialized_events = json.dumps(events)
    assert "dd-cookie-secret" not in serialized_events
    assert "TESTDATADOMEKEY1234567890" not in serialized_events
    assert "access-secret-value" not in serialized_events
    assert "csrf-secret-value" not in serialized_events
    assert "anon-secret-value" not in serialized_events
    assert "udt-secret-value" not in serialized_events


def test_curl_provider_preflight_collector_uses_next_datadome_client_side_key() -> None:
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
                    '{"CSRF_TOKEN":"csrf-secret-value",'
                    '"DATADOME_CLIENT_SIDE_KEY":"NEXTDATADOMEKEY1234567890"}'
                ),
                headers={
                    "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/datadome/5.7.0/tags.js":
            raise AssertionError("tags.js should not be fetched when HTML exposes DATADOME_CLIENT_SIDE_KEY")
        if path(call) == "/js":
            assert call["data"]["ddk"] == "NEXTDATADOMEKEY1234567890"
            return FakeResponse(
                200,
                json_data={"status": 200, "cookie": "datadome=dd-cookie-secret; Path=/; Secure; SameSite=Lax"},
                headers={"content-type": "application/json"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
    )

    provider.bootstrap_for_session(source().url, collect_datadome=True)
    prepared = provider.export_prepared_session(proxy_session_id="pytestproxy01")

    assert prepared.datadome == "dd-cookie-secret"
    assert [path(call) for call in calls] == ["/ip", "/catalog", "/js", "/js"]


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
                    "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
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
                    "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
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


def test_curl_provider_preflight_collector_without_ddk_logs_skip_without_post() -> None:
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
                    "set-cookie": ["access_token_web=access-secret-value; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
                    "x-anon-id": "anon-secret-value",
                    "x-v-udt": "udt-secret-value",
                    "x-user-iso-locale": "ES",
                    "x-screen": "catalog",
                },
            )
        if path(call) == "/js":
            raise AssertionError("collector POST must not run without a DataDome client key")
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
        require_datadome_cookie=False,
    )

    report = provider.bootstrap_for_session(source().url, collect_datadome=True)

    assert report["datadome_cookie"] is False
    assert [path(call) for call in calls] == ["/ip", "/catalog"]
    start_event = next(event for event in events if event["phase"] == "datadome_collector_start")
    assert start_event["method"] is None
    assert start_event["url"] is None
    assert start_event["details"]["post_sent"] is False
    failed_event = next(event for event in events if event["phase"] == "datadome_collector_failed")
    assert failed_event["method"] is None
    assert failed_event["url"] is None
    assert failed_event["details"]["post_sent"] is False
    assert failed_event["details"]["error"] == "datadome_client_key_missing"
    assert failed_event["details"]["non_blocking"] is True
    assert "cf-secret-value" not in json.dumps(events)


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


def test_build_item_detail_navigation_url_adds_referrer_once_and_preserves_query() -> None:
    assert (
        build_item_detail_navigation_url("https://www.vinted.es/items/100-test?foo=bar")
        == "https://www.vinted.es/items/100-test?foo=bar&referrer=catalog"
    )
    assert (
        build_item_detail_navigation_url("https://www.vinted.es/items/100-test?referrer=feed")
        == "https://www.vinted.es/items/100-test?referrer=feed"
    )


@pytest.mark.parametrize(
    "item_url",
    [
        "http://www.vinted.es/items/100-test",
        "https://example.test/items/100-test",
        "https://www.vinted.es.evil.test/items/100-test",
        "https://user:secret@www.vinted.es/items/100-test",
        "https://www.vinted.es/catalog",
    ],
)
def test_build_item_detail_navigation_url_rejects_non_vinted_targets(item_url: str) -> None:
    with pytest.raises(ValueError, match="HTTPS Vinted ES item URL"):
        build_item_detail_navigation_url(item_url)


def test_curl_provider_fetch_detail_uses_html_document_with_referer() -> None:
    calls: list[dict] = []
    events: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])
    product_json = {
        "@type": "Product",
        "description": "Detalle publico",
        "color": "Azul",
        "category": "Polos",
        "image": ["https://images1.vinted.net/t/detail/f800/detail.webp?s=signed-detail"],
        "offers": {"url": candidate.url, "availability": "https://schema.org/InStock"},
    }
    html = f'<script type="application/ld+json">{json.dumps(product_json)}</script>'

    def handler(call: dict) -> FakeResponse:
        assert call["method"] == "GET"
        assert call["url"] == f"{candidate.url}?referrer=catalog"
        assert call["default_headers"] is False
        assert call["headers"]["referer"] == source().url
        assert call["headers"]["sec-fetch-site"] == "same-origin"
        assert "cookie" not in call["headers"]
        return FakeResponse(200, text=html, headers={"content-type": "text/html", "set-cookie": "datadome=dd; Path=/"})

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    detail = provider.fetch_detail(candidate, referer_url=source().url)

    assert detail.description == "Detalle publico"
    assert detail.photos == ["https://images1.vinted.net/t/detail/f800/detail.webp?s=signed-detail"]
    assert [event["phase"] for event in events] == [
        "http_session_created",
        "detail_http_request_start",
        "detail_http_request_success",
        "detail_parse_success",
    ]
    assert provider._catalog_session_context.datadome == "dd"
    assert provider.prepared_session_refreshed is True


def test_curl_provider_fetch_detail_follows_only_same_item_vinted_redirects() -> None:
    calls: list[dict] = []
    events: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])
    html = build_item_detail_flight_html()

    def handler(call: dict) -> FakeResponse:
        assert call["allow_redirects"] is False
        if len(calls) == 1:
            return FakeResponse(
                302,
                headers={"location": f"/items/{candidate.vinted_item_id}-canonical"},
                request_size=100,
                header_size=50,
                download_size=50,
            )
        return FakeResponse(
            200,
            text=html,
            headers={"content-type": "text/html"},
            request_size=200,
            header_size=100,
            download_size=1500,
        )

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        proxy_url="http://proxy.example:8000",
        event_sink=lambda **event: events.append(event),
    )

    detail = provider.fetch_detail(candidate)

    assert detail.vinted_item_id == candidate.vinted_item_id
    assert len(calls) == 2
    assert calls[1]["url"].endswith(f"/items/{candidate.vinted_item_id}-canonical?referrer=catalog")
    success = next(event for event in events if event["phase"] == "detail_http_request_success")
    assert success["details"]["proxy_transfer"]["category"] == "detail"
    assert success["details"]["proxy_transfer"]["observed_requests"] == 2
    assert success["details"]["proxy_transfer"]["total_observed_bytes"] == 2000


@pytest.mark.parametrize(
    "location",
    [
        "https://attacker.example/items/1000000001",
        "http://www.vinted.es/items/1000000001",
        "https://www.vinted.es:444/items/1000000001",
        "https://www.vinted.es/items/9999999999-other",
        "https://www.vinted.es/catalog",
    ],
)
def test_curl_provider_fetch_detail_rejects_unsafe_redirect_before_following(location: str) -> None:
    calls: list[dict] = []
    events: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(
                302,
                headers={"location": location},
                request_size=100,
                header_size=50,
                download_size=50,
            ),
            calls,
        ),
        proxy_url="http://proxy.example:8000",
        event_sink=lambda **event: events.append(event),
    )

    with pytest.raises(VintedCatalogProviderError, match="detail request failed"):
        provider.fetch_detail(candidate)

    assert len(calls) == 1
    terminal = next(event for event in events if event["phase"] == "detail_http_request_error")
    assert terminal["details"]["proxy_transfer"]["observed_requests"] == 1
    assert terminal["details"]["proxy_transfer"]["unobserved_attempts"] == 0
    assert terminal["details"]["proxy_transfer"]["total_observed_bytes"] == 200


def test_curl_provider_fetch_detail_rejects_unsafe_effective_response_url() -> None:
    calls: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(
                200,
                text=build_item_detail_flight_html(),
                headers={"content-type": "text/html"},
                url="https://attacker.example/items/1000000001",
            ),
            calls,
        ),
    )

    with pytest.raises(VintedCatalogProviderError, match="detail request failed"):
        provider.fetch_detail(candidate)


def test_curl_provider_fetch_detail_counts_redirect_without_location_once() -> None:
    events: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(302, request_size=100, header_size=50, download_size=50),
            [],
        ),
        proxy_url="http://proxy.example:8000",
        event_sink=lambda **event: events.append(event),
    )

    with pytest.raises(VintedCatalogProviderError, match="redirect omitted Location"):
        provider.fetch_detail(candidate)

    terminal_events = [event for event in events if event["phase"] == "detail_http_request_error"]
    assert len(terminal_events) == 1
    transfer = terminal_events[0]["details"]["proxy_transfer"]
    assert transfer["observed_requests"] == 1
    assert transfer["unobserved_attempts"] == 0
    assert transfer["total_observed_bytes"] == 200


def test_curl_provider_fetch_detail_counts_non_html_response_once() -> None:
    events: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(
                200,
                text='{"item":"unexpected"}',
                headers={"content-type": "application/json"},
                request_size=120,
                header_size=80,
                download_size=300,
            ),
            [],
        ),
        proxy_url="http://proxy.example:8000",
        event_sink=lambda **event: events.append(event),
    )

    with pytest.raises(VintedCatalogProviderError, match="non-HTML content"):
        provider.fetch_detail(candidate)

    terminal_events = [event for event in events if event["phase"] == "detail_http_request_error"]
    assert len(terminal_events) == 1
    transfer = terminal_events[0]["details"]["proxy_transfer"]
    assert transfer["observed_requests"] == 1
    assert transfer["unobserved_attempts"] == 0
    assert transfer["total_observed_bytes"] == 500


def test_curl_provider_fetch_detail_types_cloudflare_challenge() -> None:
    calls: list[dict] = []
    events: list[dict] = []
    candidate = map_catalog_item(load_fixture()["items"][0])

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(
                403,
                text="<html>Cloudflare challenge</html>",
                headers={"content-type": "text/html", "cf-mitigated": "challenge"},
            ),
            calls,
        ),
        event_sink=lambda **event: events.append(event),
    )

    with pytest.raises(VintedCatalogChallengeError, match="Cloudflare challenge"):
        provider.fetch_detail(candidate)

    assert calls[0]["url"].endswith("?referrer=catalog")
    assert [event["phase"] for event in events] == ["http_session_created", "detail_http_request_start", "detail_http_request_error"]


@pytest.mark.parametrize(("status_code", "retryable"), [(404, False), (410, False), (429, True), (503, True)])
def test_curl_provider_fetch_detail_exposes_http_status_for_retry_policy(
    status_code: int,
    retryable: bool,
) -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(status_code, text="error", headers={"content-type": "text/html"}),
            [],
        ),
    )

    with pytest.raises(VintedItemDetailHTTPError) as captured:
        provider.fetch_detail(candidate)

    assert captured.value.status_code == status_code
    assert captured.value.retryable is retryable


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


@pytest.mark.parametrize("status_code", [401, 403])
def test_curl_provider_fails_stop_on_first_rejected_catalog_session(status_code: int) -> None:
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
            return FakeResponse(
                status_code,
                json_data={"error": "invalid_authentication_token"},
                headers={"content-type": "application/json"},
            )
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

    with pytest.raises(VintedCatalogSessionError, match=f"status {status_code}"):
        provider.search(source())

    assert api_calls == 1
    assert bootstrap_calls == 1
    assert sessions == 1
    assert provider.prepared_session_refreshed is False


@pytest.mark.parametrize("retry_after", [None, "2", "120", "soon"])
def test_curl_provider_fails_stop_on_first_rate_limit_without_sleep_or_refresh(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
) -> None:
    calls: list[dict] = []
    sleeps: list[float] = []
    api_calls = 0
    bootstrap_calls = 0

    monkeypatch.setattr("vinted_monitor.providers.vinted_catalog.time.sleep", lambda seconds: sleeps.append(seconds))

    def handler(call: dict) -> FakeResponse:
        nonlocal api_calls, bootstrap_calls
        if path(call) == "/catalog":
            bootstrap_calls += 1
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
            headers = {"content-type": "application/json"}
            if retry_after is not None:
                headers["Retry-After"] = retry_after
            return FakeResponse(
                429,
                text='{"error":"rate_limited"}',
                headers=headers,
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
    assert sleeps == []
    assert bootstrap_calls == 1
    assert api_calls == 1
    assert provider.prepared_session_refreshed is False


def test_curl_provider_fails_stop_on_first_non_json_catalog_response() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=initial; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(
                200,
                text="<html>anonymous session expired</html>",
                headers={"content-type": "text/html"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    with pytest.raises(VintedCatalogSessionError, match="non-JSON response"):
        provider.search(source())

    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items"]
    assert provider.prepared_session_refreshed is False


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

    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items"]


def test_curl_provider_raises_datadome_challenge_before_parsing_catalog() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text="<html>bootstrap</html>",
                headers={"set-cookie": ["datadome=ok; Path=/;", "__cf_bm=cf-secret-value; Path=/;"]},
            )
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

    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items"]
    assert provider.prepared_session_refreshed is False


def test_curl_provider_fails_stop_on_first_cloudflare_catalog_challenge() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(
                200,
                text="<html>bootstrap</html>",
                headers={"set-cookie": "access_token_web=anon; Path=/;"},
            )
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(
                403,
                text="<html>Cloudflare challenge</html>",
                headers={"content-type": "text/html", "cf-mitigated": "challenge"},
            )
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        session_factory=fake_session_factory(handler, calls),
        require_complete_session_context=False,
    )

    with pytest.raises(VintedCatalogChallengeError, match="Cloudflare challenge"):
        provider.search(source())

    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items"]
    assert provider.prepared_session_refreshed is False


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
                    "set-cookie": ["access_token_web=anon; Path=/;", "datadome=ok; Path=/;", "__cf_bm=cf-secret-value; Path=/;"],
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


def test_parse_item_detail_html_extracts_item_anchored_flight_detail() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    html = build_item_detail_flight_html()

    detail = parse_item_detail_html(html, candidate)

    assert detail.title == "Polo Ralph Lauren de detalle"
    assert detail.description == "Tiene una marca pequena en la manga"
    assert detail.brand == "Ralph Lauren"
    assert detail.size == "M"
    assert detail.status == "Muy bueno"
    assert detail.color == "Azul"
    assert detail.category == "Hombre Polos"
    assert detail.price_amount == Decimal("2.50")
    assert detail.currency == "EUR"
    assert detail.shipping_price_amount == Decimal("1.75")
    assert detail.buyer_protection_fee_amount == Decimal("0.80")
    assert detail.total_price_amount == Decimal("3.30")
    assert detail.seller_rating == Decimal("0.98")
    assert detail.seller_badges == ["ACTIVE_LISTER"]
    assert detail.availability_flags == {
        "source": "public_snapshot",
        "can_buy": True,
        "instant_buy": True,
        "transaction_permitted": True,
        "is_closed": False,
        "is_hidden": False,
        "is_reserved": False,
        "is_draft": False,
        "is_processing": False,
        "has_reservation": False,
        "availability": "InStock",
        "shipping_available": True,
        "state": "buyable",
        "reason_codes": [],
    }
    assert detail.photos == [
        "https://images1.vinted.net/t/01_fixture/f800/1.webp?s=signed-one",
        "https://images1.vinted.net/t/02_fixture/f800/2.webp?s=signed-two",
    ]
    assert detail.field_sources["description"] == "flight.description"
    assert detail.field_sources["photos"] == "flight.rich_item"
    assert {"description", "photos", "shipping_price_amount"}.issubset(detail.observed_fields)
    assert detail.raw["parser_version"] == "next_flight_v3"
    assert detail.raw["flight_sections"] == ["plugins", "pricing", "rich_item", "shipping_details"]
    assert detail.raw["missing_fields"] == []
    assert detail.raw["validation_warnings"] == []
    assert "seller_id" not in json.dumps(detail.raw)


def test_parse_item_detail_html_preserves_observed_empty_description() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    description = next(plugin for plugin in records["a7"][3]["plugins"] if plugin["type"] == "description")
    description["data"]["description"] = ""

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.description == ""
    assert "description" in detail.observed_fields
    assert detail.field_sources["description"] == "flight.description"
    assert "description" not in detail.raw["missing_fields"]


def test_parse_item_detail_html_prefers_explicitly_item_scoped_plugin_over_unscoped_duplicate() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    records["a7"][3]["plugins"].insert(
        0,
        {
            "type": "description",
            "data": {"description": "Descripcion de una recomendacion sin identidad"},
        },
    )

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.description == "Tiene una marca pequena en la manga"


def test_parse_item_detail_html_extracts_item_scoped_summary_characteristics() -> None:
    candidate = replace(map_catalog_item(load_fixture()["items"][0]), brand=None, size=None, status=None)
    records = load_detail_fixture()["records"]
    plugins = records["a7"][3]["plugins"]
    records["a7"][3]["plugins"] = [plugin for plugin in plugins if plugin["type"] != "attributes"]
    summary = next(plugin for plugin in records["a7"][3]["plugins"] if plugin["type"] == "summary")
    summary["data"]["lines"].append(
        {
            "elements": [
                {"type": "text", "value": "M", "style": "body"},
                {"type": "text", "value": "Muy bueno", "style": "body"},
                {
                    "type": "navigational",
                    "value": "Ralph Lauren",
                    "code": "summary_brand",
                    "style": "body",
                },
                {"type": "text", "value": "Subido hace una hora", "style": "body"},
            ]
        }
    )

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.brand == "Ralph Lauren"
    assert detail.size == "M"
    assert detail.status == "Muy bueno"
    assert detail.field_sources["brand"] == "flight.summary"
    assert detail.field_sources["size"] == "flight.summary"
    assert detail.field_sources["status"] == "flight.summary"


def test_parse_item_detail_html_gives_blocking_availability_precedence() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    item_status = next(plugin for plugin in records["a7"][3]["plugins"] if plugin["type"] == "item_status")
    item_status["data"]["is_reserved"] = True

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.availability_flags["state"] == "reserved"
    assert detail.availability_flags["reason_codes"] == ["reserved"]
    assert detail.availability_flags["can_buy"] is True


def test_parse_item_detail_html_keeps_blocker_observed_in_lower_priority_plugin() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    ask_seller = next(plugin for plugin in records["a7"][3]["plugins"] if plugin["type"] == "ask_seller")
    ask_seller["data"]["is_hidden"] = True

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.availability_flags["is_hidden"] is True
    assert detail.availability_flags["state"] == "hidden"
    assert detail.availability_flags["reason_codes"] == ["hidden"]


def test_parse_item_detail_html_drops_optional_price_with_mismatched_currency() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    buyer_protection = records["k3"][3]["children"]["pricingServices"]["services"]["buyerProtection"]
    buyer_protection["finalPrice"]["currencyCode"] = "USD"

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.buyer_protection_fee_amount is None
    assert detail.total_price_amount == Decimal("3.30")
    assert detail.raw["validation_warnings"] == ["buyer_protection_price_invalid"]


@pytest.mark.parametrize(
    ("unsafe_rich_price", "expected_amount", "expected_currency"),
    [
        ({"amount": "99.00"}, Decimal("2.50"), "EUR"),
        ({"amount": "not-money", "currency_code": "USD"}, Decimal("2.50"), "EUR"),
    ],
)
def test_parse_item_detail_html_selects_money_amount_and_currency_as_one_pair(
    unsafe_rich_price: dict[str, str],
    expected_amount: Decimal,
    expected_currency: str,
) -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    records["2f"][3]["value"]["price"] = unsafe_rich_price

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.price_amount == expected_amount
    assert detail.currency == expected_currency
    assert detail.field_sources["price_amount"] == "flight.make_offer"
    assert detail.field_sources["currency"] == "flight.make_offer"


@pytest.mark.parametrize("unsafe_amount", ["-0.01", "NaN", "Infinity", "-Infinity"])
def test_parse_item_detail_html_skips_non_finite_or_negative_base_prices(unsafe_amount: str) -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    records["2f"][3]["value"]["price"]["amount"] = unsafe_amount

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.price_amount == Decimal("2.50")
    assert detail.currency == "EUR"
    assert detail.field_sources["price_amount"] == "flight.make_offer"
    assert detail.field_sources["currency"] == "flight.make_offer"


@pytest.mark.parametrize("unsafe_amount", ["-0.01", "NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize(
    ("price_name", "warning"),
    [
        ("shipping", "shipping_price_invalid"),
        ("buyer_protection", "buyer_protection_price_invalid"),
        ("total", "total_price_invalid"),
    ],
)
def test_parse_item_detail_html_rejects_non_finite_or_negative_optional_prices(
    price_name: str,
    warning: str,
    unsafe_amount: str,
) -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    pricing_record = records["k3"][3]
    if price_name == "shipping":
        pricing_record["shippingDetails"]["price"]["amount"] = unsafe_amount
        value_name = "shipping_price_amount"
    elif price_name == "buyer_protection":
        protection = pricing_record["children"]["pricingServices"]["services"]["buyerProtection"]
        protection["finalPrice"]["amount"] = unsafe_amount
        value_name = "buyer_protection_fee_amount"
    else:
        pricing_record["children"]["pricingServices"]["totalAmount"]["amount"] = unsafe_amount
        value_name = "total_price_amount"

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert getattr(detail, value_name) is None
    assert warning in detail.raw["validation_warnings"]


def test_parse_item_detail_html_maps_explicit_free_shipping_to_zero() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    shipping_details = records["k3"][3]["shippingDetails"]
    shipping_details["isFreeShipping"] = True
    shipping_details.pop("price")

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.shipping_price_amount == Decimal("0")
    assert detail.availability_flags["shipping_available"] is True
    assert detail.raw["validation_warnings"] == []


def test_parse_item_detail_html_does_not_mix_target_pricing_with_recommendation() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    records["z9"] = [
        "$",
        "$Lrecommendation",
        None,
        {
            "recommendation": {
                "shippingDetails": {
                    "isFreeShipping": False,
                    "price": {"amount": "99.00", "currencyCode": "EUR"},
                },
                "children": {
                    "pricingServices": {
                        "services": {
                            "buyerProtection": {
                                "finalPrice": {"amount": "19.00", "currencyCode": "EUR"}
                            }
                        },
                        "originalAskingAmount": {"amount": "80.00", "currencyCode": "EUR"},
                        "totalAmount": {"amount": "99.00", "currencyCode": "EUR"},
                    },
                    "item": {"item_id": 9999999999},
                },
            },
            "selectedItem": {"item_id": int(candidate.vinted_item_id)},
        },
    ]

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.shipping_price_amount == Decimal("1.75")
    assert detail.buyer_protection_fee_amount == Decimal("0.80")
    assert detail.total_price_amount == Decimal("3.30")


def test_parse_item_detail_html_keeps_signed_photos_but_rejects_explicit_ports() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    signed_url = "https://images2.vinted.net/t/a/f800/photo.webp?s=signed%2Btoken&foo=1"
    records["p9"][3]["value"]["photos"] = [
        {"image_no": 1, "is_hidden": False, "url": signed_url},
        {
            "image_no": 2,
            "is_hidden": False,
            "url": "https://images2.vinted.net:443/t/a/f800/ported.webp?s=signed-port",
        },
    ]

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.photos == [signed_url]


def test_parse_item_detail_html_treats_non_null_reservation_as_blocking() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    ask_seller = next(plugin for plugin in records["a7"][3]["plugins"] if plugin["type"] == "ask_seller")
    ask_seller["data"]["reservation"] = {"reserved_for_user_id": 999}

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.availability_flags["has_reservation"] is True
    assert detail.availability_flags["state"] == "reserved"
    assert detail.availability_flags["reason_codes"] == ["reserved"]


def test_parse_item_detail_html_treats_reservation_in_any_item_plugin_as_blocking() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    item_status = next(plugin for plugin in records["a7"][3]["plugins"] if plugin["type"] == "item_status")
    item_status["data"]["reservation"] = {"reserved_for_user_id": 999}

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.availability_flags["has_reservation"] is True
    assert detail.availability_flags["state"] == "reserved"
    assert detail.availability_flags["reason_codes"] == ["reserved"]


def test_parse_item_detail_html_does_not_treat_false_reservation_as_blocking() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    item_status = next(plugin for plugin in records["a7"][3]["plugins"] if plugin["type"] == "item_status")
    item_status["data"]["reservation"] = False

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.availability_flags["has_reservation"] is False
    assert detail.availability_flags["state"] == "buyable"
    assert detail.availability_flags["reason_codes"] == []


def test_parse_item_detail_html_does_not_treat_empty_shipping_details_as_available() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    records["k3"][3]["shippingDetails"] = {}

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.shipping_price_amount is None
    assert detail.availability_flags["shipping_available"] is False
    assert detail.availability_flags["state"] == "shipping_unavailable"
    assert detail.availability_flags["reason_codes"] == ["shipping_unavailable"]


def test_parse_item_detail_html_treats_json_ld_out_of_stock_as_blocking() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    product = {
        "@type": "Product",
        "offers": {
            "url": candidate.url,
            "price": "2.50",
            "priceCurrency": "EUR",
            "availability": "https://schema.org/OutOfStock",
        },
    }

    detail = parse_item_detail_html(build_item_detail_flight_html(product_json=product), candidate)

    assert detail.availability_flags["availability"] == "https://schema.org/OutOfStock"
    assert detail.availability_flags["state"] == "not_buyable"
    assert detail.availability_flags["reason_codes"] == ["out_of_stock"]


def test_parse_item_detail_html_treats_json_ld_reserved_as_blocking() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    product = {
        "@type": "Product",
        "offers": {
            "url": candidate.url,
            "price": "2.50",
            "priceCurrency": "EUR",
            "availability": "https://schema.org/Reserved",
        },
    }

    detail = parse_item_detail_html(build_item_detail_flight_html(product_json=product), candidate)

    assert detail.availability_flags["state"] == "reserved"
    assert detail.availability_flags["reason_codes"] == ["reserved"]


def test_parse_item_detail_html_ignores_json_ld_for_another_item() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    other_product = {
        "@type": "Product",
        "name": "Otro item",
        "offers": {"url": "https://www.vinted.es/items/9999999999-otro-item"},
    }

    with pytest.raises(ValueError, match="No public item detail data"):
        parse_item_detail_html(build_item_detail_flight_html(product_json=other_product, records={}), candidate)


def test_parse_item_detail_html_rejects_unscoped_json_ld_without_matching_flight_identity() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    unscoped_product = {
        "@type": "Product",
        "name": "Producto sin identidad",
        "offers": {"price": "2.50", "priceCurrency": "EUR"},
    }

    with pytest.raises(ValueError, match="No public item detail data"):
        parse_item_detail_html(build_item_detail_flight_html(product_json=unscoped_product, records={}), candidate)


@pytest.mark.parametrize(
    "product",
    [
        {
            "@type": "Product",
            "url": "https://attacker.example/items/1000000001-fake",
            "name": "Producto externo",
            "offers": {"price": "9.00", "priceCurrency": "EUR"},
        },
        {
            "@type": "Product",
            "url": "https://www.vinted.es/items/1000000001-target",
            "name": "Producto con oferta contradictoria",
            "offers": {
                "url": "https://www.vinted.es/items/9999999999-other",
                "price": "999.00",
                "priceCurrency": "USD",
            },
        },
    ],
)
def test_parse_item_detail_html_rejects_json_ld_with_invalid_or_conflicting_identity(product: dict) -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])

    with pytest.raises(ValueError, match="No public item detail data"):
        parse_item_detail_html(build_item_detail_flight_html(product_json=product, records={}), candidate)


def test_parse_item_detail_html_uses_later_matching_json_ld_product() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    unrelated = {
        "@type": "Product",
        "name": "Recomendacion",
        "offers": {"url": "https://www.vinted.es/items/9999999999-recomendacion"},
    }
    target = {
        "@type": "Product",
        "name": "Articulo objetivo",
        "description": "Descripcion objetivo",
        "offers": {
            "url": candidate.url,
            "price": "2.50",
            "priceCurrency": "EUR",
            "availability": "https://schema.org/InStock",
        },
    }
    html = (
        f'<script type="application/ld+json">{json.dumps(unrelated)}</script>'
        f'<script type="application/ld+json">{json.dumps(target)}</script>'
    )

    detail = parse_item_detail_html(html, candidate)

    assert detail.title == "Articulo objetivo"
    assert detail.description == "Descripcion objetivo"
    assert detail.price_amount == Decimal("2.50")
    assert detail.currency == "EUR"


def _prepared_detail_context() -> PreparedCatalogSession:
    now = datetime.now(UTC)
    return PreparedCatalogSession(
        session_id=77,
        proxy_session_id="sticky-test",
        cookies={
            "datadome": "dd-test",
            "__cf_bm": "cf-test",
            "access_token_web": "access-test",
            "v_udt": "udt-test",
        },
        csrf_token="csrf-test",
        anon_id="anon-test",
        access_token_web="access-test",
        datadome="dd-test",
        cf_bm="cf-test",
        v_udt="udt-test",
        user_iso_locale="es-ES",
        vinted_screen="catalog",
        egress_ip="198.51.100.20",
        egress_country_code="ES",
        egress_validated_at=now,
    )


def test_curl_provider_reuses_fresh_prepared_egress_without_http_probe() -> None:
    calls: list[dict] = []
    events: list[dict] = []
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url="https://diagnostic.example/ip"),
        prepared_session=_prepared_detail_context(),
        session_factory=fake_session_factory(lambda _call: FakeResponse(500), calls),
        event_sink=lambda **event: events.append(event),
    )

    provider._ensure_session()
    provider._diagnose_egress(attempt=1)

    assert calls == []
    assert [event["phase"] for event in events][-1] == "egress_diagnostic_reused"


def test_curl_provider_enforced_head_filter_aborts_matching_body() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    body = (
        "<html><head>"
        f'<link rel="canonical" href="{candidate.url}">'
        f"<title>{candidate.title}</title>"
        f'<meta name="description" content="{candidate.title} - Descripcion prohibido">'
        "</head><body>" + ("x" * 2000) + "</body></html>"
    )
    calls: list[dict] = []
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(
            egress_diagnostic_url=None,
            vinted_detail_early_filter_mode="enforced",
        ),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(200, text=body, headers={"content-type": "text/html"}),
            calls,
        ),
    )

    with pytest.raises(VintedItemEarlyDiscard) as captured:
        provider.fetch_detail(candidate, early_filter_terms=("prohibido",))

    assert captured.value.matched_terms == ["prohibido"]
    assert len(calls) == 1


def test_curl_provider_enforced_head_filter_ignores_catalog_title_and_finishes_same_request() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    description = "Tiene una marca pequena en la manga"
    body = (
        "<html><head>"
        f'<link rel="canonical" href="{candidate.url}">'
        f"<title>{candidate.title}</title>"
        f'<meta name="description" content="{candidate.title} - {description}">'
        f"</head><body>{build_item_detail_flight_html()}</body></html>"
    )
    calls: list[dict] = []
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(
            egress_diagnostic_url=None,
            vinted_detail_early_filter_mode="enforced",
        ),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(200, text=body, headers={"content-type": "text/html"}),
            calls,
        ),
    )

    detail = provider.fetch_detail(candidate, early_filter_terms=("polo",))

    assert detail.description == description
    assert len(calls) == 1


def test_curl_provider_shadow_head_filter_matches_only_final_description() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    description = "Tiene una marca pequena en la manga"
    body = (
        "<html><head>"
        f'<link rel="canonical" href="{candidate.url}">'
        f"<title>{candidate.title}</title>"
        f'<meta name="description" content="{candidate.title} - {description}">'
        f"</head><body>{build_item_detail_flight_html()}</body></html>"
    )
    calls: list[dict] = []
    events: list[dict] = []
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(
            egress_diagnostic_url=None,
            vinted_detail_early_filter_mode="shadow",
        ),
        session_factory=fake_session_factory(
            lambda _call: FakeResponse(200, text=body, headers={"content-type": "text/html"}),
            calls,
        ),
        event_sink=lambda **event: events.append(event),
    )

    detail = provider.fetch_detail(candidate, early_filter_terms=("marca", "polo"))

    shadow = next(event for event in events if event["phase"] == "detail_early_filter_shadow")
    assert detail.description == description
    assert shadow["details"] == {
        "vinted_item_id": candidate.vinted_item_id,
        "head_complete": True,
        "canonical_matches": True,
        "description_isolated": True,
        "filter_scope": "description",
        "head_bytes_observed": len(body.encode("utf-8")),
        "would_discard": True,
        "match_count": 1,
        "safe_subset_of_final_description": True,
        "equivalent_to_final_description": True,
    }
    assert len(calls) == 1


def test_detail_batch_uses_two_lanes_but_replays_events_on_caller_thread() -> None:
    base = map_catalog_item(load_fixture()["items"][0])
    candidates = [
        replace(
            base,
            vinted_item_id=str(1000000100 + offset),
            title=f"Articulo {offset}",
            url=f"https://www.vinted.es/items/{1000000100 + offset}-articulo-{offset}",
        )
        for offset in range(3)
    ]
    candidates_by_id = {candidate.vinted_item_id: candidate for candidate in candidates}
    calls: list[dict] = []
    lock = threading.Lock()
    active = 0
    max_active = 0

    def handler(call: dict) -> FakeResponse:
        nonlocal active, max_active
        if urlparse(call["url"]).path == "/api/v2/catalog/items":
            return FakeResponse(
                200,
                json_data={"items": [], "pagination": {}},
                headers={"content-type": "application/json"},
            )
        item_id = extract_vinted_item_id(call["url"])
        assert item_id is not None
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.04 if item_id == candidates[0].vinted_item_id else 0.01)
        with lock:
            active -= 1
        candidate = candidates_by_id[item_id]
        product = {
            "@type": "Product",
            "name": candidate.title,
            "description": "Detalle concurrente",
            "brand": {"name": candidate.brand},
            "image": ["https://images1.vinted.net/t/detail/f800/concurrent.webp?s=signed"],
            "offers": {
                "url": candidate.url,
                "price": str(candidate.price_amount),
                "priceCurrency": candidate.currency,
                "availability": "https://schema.org/InStock",
            },
        }
        html = f'<script type="application/ld+json">{json.dumps(product)}</script>'
        return FakeResponse(
            200,
            text=html,
            headers={
                "content-type": "text/html",
                "set-cookie": f"_vinted_fr_session=branch-{item_id}; Path=/",
            },
        )

    caller_thread = threading.get_ident()
    event_threads: list[int] = []
    event_phases: list[str] = []
    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        prepared_session=_prepared_detail_context(),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: (
            event_threads.append(threading.get_ident()),
            event_phases.append(event["phase"]),
        ),
    )

    result = provider.fetch_detail_batch(
        candidates,
        referer_url=source().url,
        concurrency=2,
        canary=True,
    )

    assert [outcome.candidate.vinted_item_id for outcome in result.outcomes] == [
        candidate.vinted_item_id for candidate in candidates
    ]
    assert all(outcome.detail is not None and outcome.error is None for outcome in result.outcomes)
    assert result.effective_concurrency == 2
    assert max_active == 2
    assert "_vinted_fr_session" in result.divergent_cookie_names
    assert event_threads and set(event_threads) == {caller_thread}
    assert "catalog_api_probe_success" in event_phases
    assert "detail_batch_finished" in event_phases


def test_detail_batch_aborts_all_lanes_on_challenge() -> None:
    base = map_catalog_item(load_fixture()["items"][0])
    candidates = [
        replace(
            base,
            vinted_item_id=str(1000000200 + offset),
            url=f"https://www.vinted.es/items/{1000000200 + offset}-challenge-{offset}",
        )
        for offset in range(2)
    ]

    def handler(call: dict) -> FakeResponse:
        item_id = extract_vinted_item_id(call["url"])
        if item_id == candidates[0].vinted_item_id:
            return FakeResponse(
                403,
                text="challenge",
                headers={"content-type": "text/html", "cf-mitigated": "challenge"},
            )
        candidate = candidates[1]
        product = {
            "@type": "Product",
            "name": candidate.title,
            "description": "Detalle",
            "image": ["https://images1.vinted.net/t/detail/f800/challenge.webp?s=signed"],
            "offers": {"url": candidate.url, "price": "2.50", "priceCurrency": "EUR"},
        }
        return FakeResponse(
            200,
            text=f'<script type="application/ld+json">{json.dumps(product)}</script>',
            headers={"content-type": "text/html"},
        )

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        prepared_session=_prepared_detail_context(),
        session_factory=fake_session_factory(handler, []),
    )

    with pytest.raises(VintedCatalogChallengeError) as captured:
        provider.fetch_detail_batch(candidates, referer_url=source().url, concurrency=2)

    assert captured.value.detail_candidate_id == candidates[0].vinted_item_id
    assert captured.value.detail_batch_telemetry["detail_fetch_elapsed_ms"] >= 0
    assert captured.value.detail_batch_telemetry["detail_fetch_request_duration_total_ms"] >= 0
    assert captured.value.detail_batch_telemetry["detail_fetch_attempts"] == 2


def test_detail_batch_stops_new_waves_after_429_without_consuming_deferred_attempt() -> None:
    base = map_catalog_item(load_fixture()["items"][0])
    candidates = [
        replace(
            base,
            vinted_item_id=str(1000000300 + offset),
            url=f"https://www.vinted.es/items/{1000000300 + offset}-rate-{offset}",
        )
        for offset in range(3)
    ]
    requested_ids: list[str] = []

    def handler(call: dict) -> FakeResponse:
        item_id = extract_vinted_item_id(call["url"])
        assert item_id is not None
        requested_ids.append(item_id)
        if item_id == candidates[0].vinted_item_id:
            return FakeResponse(429, text="rate limited", headers={"content-type": "text/html"})
        candidate = next(candidate for candidate in candidates if candidate.vinted_item_id == item_id)
        product = {
            "@type": "Product",
            "name": candidate.title,
            "description": "Detalle",
            "image": ["https://images1.vinted.net/t/detail/f800/rate.webp?s=signed"],
            "offers": {"url": candidate.url, "price": "2.50", "priceCurrency": "EUR"},
        }
        return FakeResponse(
            200,
            text=f'<script type="application/ld+json">{json.dumps(product)}</script>',
            headers={"content-type": "text/html"},
        )

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(egress_diagnostic_url=None),
        prepared_session=_prepared_detail_context(),
        session_factory=fake_session_factory(handler, []),
    )

    result = provider.fetch_detail_batch(candidates, referer_url=source().url, concurrency=2)

    assert set(requested_ids) == {candidates[0].vinted_item_id, candidates[1].vinted_item_id}
    assert isinstance(result.outcomes[0].error, VintedItemDetailHTTPError)
    assert result.outcomes[1].detail is not None
    assert isinstance(result.outcomes[2].error, VintedDetailDeferred)
    assert result.outcomes[2].duration_ms == 0


def test_item_flight_parser_skips_unrelated_large_record() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    records = load_detail_fixture()["records"]
    records["zz"] = ["$", "$Lunrelated", None, {"payload": "x" * 100_000, "itemId": 9999999999}]

    detail = parse_item_detail_html(build_item_detail_flight_html(records=records), candidate)

    assert detail.raw["flight_decoded_record_count"] < detail.raw["flight_record_count"]
    assert detail.shipping_price_amount == Decimal("1.75")


def test_parse_item_detail_html_rejects_document_without_detail_data() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])

    with pytest.raises(ValueError, match="No public item detail data"):
        parse_item_detail_html("<html><title>Vinted</title></html>", candidate)
