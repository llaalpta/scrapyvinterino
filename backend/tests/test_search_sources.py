import pytest
from pydantic import ValidationError

from vinted_monitor.api.schemas import SearchSourceCreate
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
