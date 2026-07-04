import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vinted_monitor.api.main import app
from vinted_monitor.api.schemas import SearchSourceCreate
from vinted_monitor.db.models import AppSetting, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.scheduler import SCHEDULER_SETTING_KEY
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


def test_update_source_api_persists_pause_and_scheduler_config() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sources",
        json={"name": "pytest scheduler source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(
            f"/api/sources/{source_id}",
            json={
                "is_active": False,
                "scheduler_config": {
                    "interval_seconds": 120,
                    "jitter_percent": 10,
                    "allowed_windows": ["09:00-23:00"],
                },
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["is_active"] is False
        assert body["scheduler_config"] == {
            "interval_seconds": 120,
            "jitter_percent": 10,
            "allowed_windows": ["09:00-23:00"],
        }

        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.is_active is False
            assert source.scheduler_config["interval_seconds"] == 120
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_rejects_invalid_scheduler_config_without_mutation() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sources",
        json={"name": "pytest invalid scheduler source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(
            f"/api/sources/{source_id}",
            json={"scheduler_config": {"interval_seconds": 30}},
        )

        assert response.status_code == 422
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.scheduler_config == {}
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_delete_source_api_archives_and_hides_source_idempotently() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sources",
        json={"name": "pytest archived source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.delete(f"/api/sources/{source_id}")
        assert response.status_code == 204

        second_response = client.delete(f"/api/sources/{source_id}")
        assert second_response.status_code == 204

        list_response = client.get("/api/sources")
        assert list_response.status_code == 200
        assert all(source["id"] != source_id for source in list_response.json())

        patch_response = client.patch(f"/api/sources/{source_id}", json={"is_active": True})
        assert patch_response.status_code == 404

        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.is_active is False
            assert source.archived_at is not None
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_delete_source_api_stops_active_monitor() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/sources",
        json={"name": "pytest archived monitor source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = True
        db.commit()

    try:
        response = client.delete(f"/api/sources/{source_id}")
        assert response.status_code == 204

        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.is_active is False
            assert source.archived_at is not None
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            db.commit()


def test_scheduler_api_updates_persisted_ui_gate() -> None:
    client = TestClient(app)

    try:
        response = client.patch("/api/scheduler", json={"enabled": True})

        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is True
        assert "runtime_enabled" in body
        assert "effective_enabled" in body

        get_response = client.get("/api/scheduler")
        assert get_response.status_code == 200
        assert get_response.json()["enabled"] is True
    finally:
        with SessionLocal() as db:
            setting = db.get(AppSetting, SCHEDULER_SETTING_KEY)
            if setting is not None:
                db.delete(setting)
                db.commit()
