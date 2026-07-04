import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.vinted_catalog import (
    HttpVintedCatalogProvider,
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


def test_http_provider_uses_catalog_api_after_anonymous_bootstrap() -> None:
    calls: list[str] = []
    fixture = load_fixture()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/catalog":
            return httpx.Response(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=anon; Path=/;"})
        if request.url.path == "/api/v2/catalog/items":
            assert request.url.params["per_page"] == "5"
            assert request.url.params["order"] == "newest_first"
            assert request.headers["accept"] == "application/json, text/plain, */*"
            assert request.headers.get("cookie")
            return httpx.Response(200, json=fixture, headers={"content-type": "application/json"})
        return httpx.Response(404)

    provider = HttpVintedCatalogProvider(settings=Settings(), transport=httpx.MockTransport(handler))
    result = provider.search(type("Source", (), {"url": "https://www.vinted.es/catalog?catalog[]=76&order=newest_first"})())

    assert len(result.items) == 2
    assert [httpx.URL(call).path for call in calls] == ["/catalog", "/api/v2/catalog/items"]


def test_http_provider_emits_safe_session_and_catalog_events() -> None:
    events: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/catalog":
            return httpx.Response(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=secret; Path=/;"})
        if request.url.path == "/api/v2/catalog/items":
            return httpx.Response(200, json=load_fixture(), headers={"content-type": "application/json"})
        return httpx.Response(404)

    provider = HttpVintedCatalogProvider(
        settings=Settings(),
        transport=httpx.MockTransport(handler),
        event_sink=lambda **event: events.append(event),
    )

    provider.search(type("Source", (), {"url": "https://www.vinted.es/catalog?catalog[]=76"})())

    phases = [event["phase"] for event in events]
    assert phases == [
        "anonymous_session_bootstrap_start",
        "anonymous_session_bootstrap_success",
        "catalog_api_request_start",
        "catalog_api_request_success",
    ]
    assert events[1]["details"] == {"session_marker_count": 1}
    assert events[2]["details"]["session_marker_count"] == 1
    assert events[3]["details"]["item_count"] == 2
    assert "secret" not in json.dumps(events)
    assert "access_token_web" not in json.dumps(events)


def test_http_provider_configures_proxy_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_proxies: list[str | None] = []

    class FakeClient:
        def __init__(self, *args, proxy=None, **kwargs) -> None:
            captured_proxies.append(proxy)

    monkeypatch.setattr("vinted_monitor.providers.vinted_catalog.httpx.Client", FakeClient)

    HttpVintedCatalogProvider(
        settings=Settings(vinted_proxy_enabled=False, vinted_proxy_url="http://user:pass@proxy.example:8000")
    )._client({})
    HttpVintedCatalogProvider(
        settings=Settings(vinted_proxy_enabled=True, vinted_proxy_url="http://user:pass@proxy.example:8000")
    )._client({})

    assert captured_proxies == [None, "http://user:pass@proxy.example:8000"]


def test_http_provider_refreshes_anonymous_session_once_after_auth_failure() -> None:
    api_calls = 0
    bootstrap_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls, bootstrap_calls
        if request.url.path == "/catalog":
            bootstrap_calls += 1
            return httpx.Response(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=fresh; Path=/;"})
        if request.url.path == "/api/v2/catalog/items":
            api_calls += 1
            if api_calls == 1:
                return httpx.Response(401, json={"error": "invalid_authentication_token"}, headers={"content-type": "application/json"})
            return httpx.Response(200, json=load_fixture(), headers={"content-type": "application/json"})
        return httpx.Response(404)

    provider = HttpVintedCatalogProvider(settings=Settings(), transport=httpx.MockTransport(handler))
    provider._cookies.set("access_token_web", "expired", domain="www.vinted.es")

    result = provider.search(type("Source", (), {"url": "https://www.vinted.es/catalog?catalog[]=76"})())

    assert len(result.items) == 2
    assert api_calls == 2
    assert bootstrap_calls == 1


def test_http_provider_raises_after_second_session_failure() -> None:
    api_calls = 0
    bootstrap_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls, bootstrap_calls
        if request.url.path == "/catalog":
            bootstrap_calls += 1
            return httpx.Response(200, text="<html>bootstrap</html>", headers={"set-cookie": "access_token_web=fresh; Path=/;"})
        if request.url.path == "/api/v2/catalog/items":
            api_calls += 1
            return httpx.Response(403, json={"error": "invalid_authentication_token"}, headers={"content-type": "application/json"})
        return httpx.Response(404)

    provider = HttpVintedCatalogProvider(settings=Settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(VintedCatalogProviderError):
        provider.search(type("Source", (), {"url": "https://www.vinted.es/catalog?catalog[]=76"})())

    assert api_calls == 2
    assert bootstrap_calls == 2


def test_http_provider_does_not_use_catalog_html_fallback_after_api_failure() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/catalog":
            return httpx.Response(
                200,
                text=build_next_flight_html(load_fixture()),
                headers={"set-cookie": "access_token_web=anon; Path=/;"},
            )
        if request.url.path == "/api/v2/catalog/items":
            return httpx.Response(500, json={"error": "boom"}, headers={"content-type": "application/json"})
        return httpx.Response(404)

    provider = HttpVintedCatalogProvider(settings=Settings(), transport=httpx.MockTransport(handler))

    with pytest.raises(VintedCatalogProviderError):
        provider.search(type("Source", (), {"url": "https://www.vinted.es/catalog?catalog[]=76"})())

    assert calls == ["/catalog", "/api/v2/catalog/items"]


def test_parse_item_detail_html_extracts_sanitized_public_detail() -> None:
    candidate = map_catalog_item(load_fixture()["items"][0])
    product_json = {
        "@type": "Product",
        "description": "Tiene una mancha pequeña en la manga",
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

    assert detail.description == "Tiene una mancha pequeña en la manga"
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
