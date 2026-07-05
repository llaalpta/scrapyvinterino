import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.browser_profiles import NavigationFlow, get_profile_by_name
from vinted_monitor.providers.datadome import DataDomeChallengeError
from vinted_monitor.providers.vinted_catalog import (
    CurlCffiVintedCatalogProvider,
    VintedCatalogProviderError,
    build_catalog_api_params,
    decode_next_flight_payload,
    map_catalog_item,
    parse_catalog_api_payload,
    parse_catalog_html,
    parse_item_detail_html,
    sanitize_catalog_item,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "vinted_catalog_payload.json"
INTERNAL_FLOW = NavigationFlow(name="internal_referral", bootstrap_referer=None, needs_home_visit=False)


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

    def get(self, url, *, params=None, headers=None, timeout=None):
        call = {
            "url": url,
            "params": params or {},
            "headers": headers or {},
            "timeout": timeout,
            "impersonate": self.impersonate,
            "proxies": self.proxies,
            "cookies": dict(self.cookies),
        }
        self.calls.append(call)
        response = self.handler(call)
        set_cookie = response.headers.get("set-cookie") or response.headers.get("Set-Cookie")
        if set_cookie:
            name, _, remainder = set_cookie.partition("=")
            value = remainder.split(";", 1)[0]
            self.cookies[name] = value
        return response

    def close(self) -> None:
        self.closed = True


def fake_session_factory(handler, calls: list[dict]):
    def factory(*, impersonate=None, proxies=None):
        return FakeCurlSession(handler, calls, impersonate=impersonate, proxies=proxies)

    return factory


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


def test_parse_catalog_api_payload_maps_items_and_provider_metadata() -> None:
    fixture = load_fixture()
    result = parse_catalog_api_payload(fixture)

    assert len(result.items) == 2
    assert result.page == 1
    assert result.per_page == 96
    assert result.provider_metadata == {"source": "catalog_api_json"}


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
        if path(call) == "/catalog":
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=anon; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            assert call["params"]["per_page"] == 5
            assert call["params"]["order"] == "newest_first"
            assert call["headers"]["Accept"] == "application/json, text/plain, */*"
            assert call["cookies"]["access_token_web"] == "anon"
            return FakeResponse(200, json_data=fixture, headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(),
        navigation_flow=NavigationFlow(name="internal_referral", bootstrap_referer=None, needs_home_visit=False),
        session_factory=fake_session_factory(handler, calls),
    )
    result = provider.search(source())

    assert len(result.items) == 2
    assert [path(call) for call in calls] == ["/catalog", "/api/v2/catalog/items"]
    assert calls[0]["headers"]["Referer"] == "https://www.vinted.es/"


def test_curl_provider_emits_safe_session_and_catalog_events() -> None:
    calls: list[dict] = []
    events: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) == "/catalog":
            return FakeResponse(200, text="<html>bootstrap</html>", headers={"set-cookie": "datadome=public-marker; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(),
        navigation_flow=NavigationFlow(name="google_referral", bootstrap_referer="https://www.google.com/", needs_home_visit=False),
        session_factory=fake_session_factory(handler, calls),
        event_sink=lambda **event: events.append(event),
    )

    provider.search(source())

    phases = [event["phase"] for event in events]
    assert phases == [
        "anonymous_session_bootstrap_start",
        "anonymous_session_bootstrap_success",
        "human_delay_applied",
        "catalog_api_request_start",
        "catalog_api_request_success",
    ]
    assert events[0]["details"]["navigation_flow"] == "google_referral"
    assert events[1]["details"]["datadome_cookie"] is True
    assert "bootstrap_duration_ms" in events[1]["details"]
    assert events[3]["details"]["browser_profile"] == provider.profile.name
    assert "public-marker" not in json.dumps(events)


def test_curl_provider_uses_only_explicit_proxy() -> None:
    captured_proxies: list[dict | None] = []

    def factory(*, impersonate=None, proxies=None):
        captured_proxies.append(proxies)
        return FakeCurlSession(lambda _call: FakeResponse(200), [], impersonate=impersonate, proxies=proxies)

    CurlCffiVintedCatalogProvider(settings=Settings(), session_factory=factory)._ensure_session()
    CurlCffiVintedCatalogProvider(
        settings=Settings(),
        proxy_url="http://user:pass@proxy.example:8000",
        session_factory=factory,
    )._ensure_session()

    assert captured_proxies == [None, {"https": "http://user:pass@proxy.example:8000", "http": "http://user:pass@proxy.example:8000"}]


def test_curl_provider_refreshes_anonymous_session_once_after_auth_failure() -> None:
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
            if api_calls == 1:
                return FakeResponse(401, json_data={"error": "invalid_authentication_token"}, headers={"content-type": "application/json"})
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(),
        navigation_flow=INTERNAL_FLOW,
        session_factory=fake_session_factory(handler, calls),
    )

    result = provider.search(source())

    assert len(result.items) == 2
    assert api_calls == 2
    assert bootstrap_calls == 2


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
        settings=Settings(),
        navigation_flow=INTERNAL_FLOW,
        session_factory=fake_session_factory(handler, calls),
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
        settings=Settings(),
        navigation_flow=INTERNAL_FLOW,
        session_factory=fake_session_factory(handler, calls),
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
        settings=Settings(),
        navigation_flow=INTERNAL_FLOW,
        session_factory=fake_session_factory(handler, calls),
    )

    with pytest.raises(DataDomeChallengeError):
        provider.search(source())


def test_curl_provider_home_navigation_visits_home_then_catalog() -> None:
    calls: list[dict] = []

    def handler(call: dict) -> FakeResponse:
        if path(call) in {"/", "/catalog"}:
            return FakeResponse(200, text="<html>ok</html>", headers={"set-cookie": "datadome=ok; Path=/;"})
        if path(call) == "/api/v2/catalog/items":
            return FakeResponse(200, json_data=load_fixture(), headers={"content-type": "application/json"})
        return FakeResponse(404)

    provider = CurlCffiVintedCatalogProvider(
        settings=Settings(),
        navigation_flow=NavigationFlow(name="home_navigation", bootstrap_referer=None, needs_home_visit=True),
        session_factory=fake_session_factory(handler, calls),
    )

    provider.search(source())

    assert [path(call) for call in calls] == ["/", "/catalog", "/api/v2/catalog/items"]
    assert calls[1]["headers"]["Referer"] == "https://www.vinted.es/"


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
