import json
from decimal import Decimal
from pathlib import Path

from vinted_monitor.providers.vinted_catalog import decode_next_flight_payload, map_catalog_item, parse_catalog_html, sanitize_catalog_item

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
