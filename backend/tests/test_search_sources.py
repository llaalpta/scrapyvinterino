import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vinted_monitor.api.main import app
from vinted_monitor.api.schemas import SearchSourceCreate
from vinted_monitor.db.models import SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.search_sources import (
    normalize_vinted_catalog_url,
    validate_search_source_name,
    validate_vinted_catalog_url,
)


def test_validate_search_source_name_trims_surrounding_whitespace() -> None:
    assert validate_search_source_name("  polos baratos  ") == "polos baratos"


def test_validate_search_source_name_rejects_blank_value() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_search_source_name("   ")


def test_validate_vinted_catalog_url_preserves_original_after_trim() -> None:
    url = " https://www.vinted.es/catalog?search_text=&brand_ids[]=88&order=newest_first "

    assert validate_vinted_catalog_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://www.vinted.es/catalog",
        "https://example.com/catalog",
        "https://www.vinted.es/member/123",
        "not a url",
    ],
)
def test_validate_vinted_catalog_url_rejects_non_catalog_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_vinted_catalog_url(url)


def test_normalize_vinted_catalog_url_preserves_blank_and_repeated_values() -> None:
    normalized = normalize_vinted_catalog_url(
        "https://www.vinted.es/catalog?search_text=&brand_ids[]=88&brand_ids[]=364&price_to=5.00"
    )

    assert normalized == {
        "brand_ids[]": ["88", "364"],
        "price_to": ["5.00"],
        "search_text": [""],
    }


def test_search_source_create_schema_validates_and_keeps_string_url() -> None:
    url = "https://www.vinted.es/catalog?search_text=&catalog[]=76"
    payload = SearchSourceCreate(name="  tenis  ", url=url)

    assert payload.name == "tenis"
    assert payload.url == url


def test_search_source_create_schema_rejects_invalid_url() -> None:
    with pytest.raises(ValidationError):
        SearchSourceCreate(name="test", url="https://example.com/catalog")


def test_create_source_api_persists_normalized_query() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/sources",
        json={
            "name": "  pytest source  ",
            "url": "https://www.vinted.es/catalog?search_text=&brand_ids[]=88&brand_ids[]=364&order=newest_first",
        },
    )
    assert response.status_code == 201

    created = response.json()
    created_id = created["id"]

    try:
        assert created["name"] == "pytest source"
        assert created["normalized_query"]["brand_ids[]"] == ["88", "364"]
        assert created["normalized_query"]["search_text"] == [""]

        list_response = client.get("/api/sources")
        assert list_response.status_code == 200
        assert any(source["id"] == created_id for source in list_response.json())

        with SessionLocal() as db:
            source = db.get(SearchSource, created_id)
            assert source is not None
            assert source.url == created["url"]
            assert source.normalized_query["order"] == ["newest_first"]
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, created_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_create_source_api_rejects_invalid_url() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/sources",
        json={"name": "bad source", "url": "https://example.com/catalog"},
    )

    assert response.status_code == 422
