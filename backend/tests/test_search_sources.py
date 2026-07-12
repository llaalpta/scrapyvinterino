from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from vinted_monitor.api.main import app
from vinted_monitor.api.schemas import SearchSourceCreate
from vinted_monitor.db.models import AppSetting, MonitorSession, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services import search_sources as search_sources_service
from vinted_monitor.services.scheduler import SCHEDULER_SETTING_KEY
from vinted_monitor.services.search_sources import (
    SearchSourceNotFoundError,
    archive_source,
    catalog_filter_compatibility,
    normalize_vinted_catalog_url,
    start_source_monitor,
    stop_source_monitor,
    update_source,
    validate_search_source_name,
    validate_vinted_catalog_url,
)


def test_validate_search_source_name_trims_surrounding_whitespace() -> None:
    assert validate_search_source_name("  polos baratos  ") == "polos baratos"


def test_validate_search_source_name_rejects_blank_value() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_search_source_name("   ")


@pytest.mark.parametrize(("jitter_seconds", "expected_delay"), [(-6, 60), (6, 66)])
def test_start_source_monitor_persists_first_recurring_deadline_with_interval_floor(
    jitter_seconds: int,
    expected_delay: int,
) -> None:
    class FixedRng:
        def uniform(self, _minimum: float, _maximum: float) -> float:
            return jitter_seconds

    started_at = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)
    with SessionLocal() as db:
        source = SearchSource(
            name=f"pytest activation cadence {jitter_seconds}",
            url="https://www.vinted.es/catalog?search_text=cadence",
            normalized_query={"search_text": ["cadence"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 60, "jitter_percent": 10, "allowed_windows": []},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        with SessionLocal() as db:
            activated = start_source_monitor(db, source_id, now=started_at, rng=FixedRng())

            assert activated.monitor_started_at == started_at
            assert activated.next_run_at == started_at + timedelta(seconds=expected_delay)
    finally:
        with SessionLocal() as db:
            db.query(MonitorSession).filter(MonitorSession.source_id == source_id).delete(synchronize_session=False)
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            db.commit()


def test_validate_vinted_catalog_url_preserves_original_after_trim() -> None:
    url = " https://www.vinted.es/catalog?search_text=&brand_ids[]=88&order=newest_first "

    assert validate_vinted_catalog_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [
        "ftp://www.vinted.es/catalog",
        "http://www.vinted.es/catalog",
        "https://example.com/catalog",
        "https://user:secret@www.vinted.es/catalog",
        "https://www.vinted.es:443/catalog",
        "https://www.vinted.es:invalid/catalog",
        "https://www.vinted.es/member/123",
        "not a url",
    ],
)
def test_validate_vinted_catalog_url_rejects_non_catalog_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_vinted_catalog_url(url)


def test_validate_vinted_catalog_url_rejects_unsupported_catalog_filters() -> None:
    with pytest.raises(ValueError, match="color_ids"):
        validate_vinted_catalog_url("https://www.vinted.es/catalog?catalog[]=76&color_ids[]=12")


def test_catalog_filter_compatibility_reports_supported_ignored_and_unsupported() -> None:
    compatibility = catalog_filter_compatibility(
        "https://www.vinted.es/catalog?catalog[]=76&brand_ids[]=88&page=2&time=123&color_ids[]=12"
    )

    assert compatibility["compatible"] is False
    assert compatibility["supported"] == {"brand_ids": ["88"], "catalog": ["76"]}
    assert compatibility["ignored"] == {"page": ["2"], "time": ["123"]}
    assert compatibility["unsupported"] == {"color_ids[]": ["12"]}


def test_catalog_filter_compatibility_ignores_empty_search_by_image_params() -> None:
    compatibility = catalog_filter_compatibility(
        "https://www.vinted.es/catalog?catalog[]=2050&search_by_image_uuid=&search_by_image_id="
    )

    assert compatibility["compatible"] is True
    assert compatibility["supported"] == {"catalog": ["2050"]}
    assert compatibility["ignored"] == {
        "search_by_image_id": [""],
        "search_by_image_uuid": [""],
    }
    assert compatibility["unsupported"] == {}


def test_validate_vinted_catalog_url_rejects_non_empty_search_by_image_params() -> None:
    with pytest.raises(ValueError, match="search_by_image_uuid"):
        validate_vinted_catalog_url("https://www.vinted.es/catalog?catalog[]=2050&search_by_image_uuid=image-123")


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
        "/api/monitors",
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
        assert created["catalog_filter_compatibility"]["compatible"] is True
        assert created["catalog_filter_compatibility"]["supported"]["brand_ids"] == ["88", "364"]
        assert created["catalog_filter_compatibility"]["ignored"]["order"] == ["newest_first"]

        list_response = client.get("/api/monitors")
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
        "/api/monitors",
        json={"name": "bad source", "url": "https://example.com/catalog"},
    )

    assert response.status_code == 422


def test_create_source_api_rejects_unsupported_catalog_filter_without_persisting() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/monitors",
        json={"name": "bad filter source", "url": "https://www.vinted.es/catalog?catalog[]=76&color_ids[]=12"},
    )

    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.query(SearchSource).filter(SearchSource.name == "bad filter source").one_or_none() is None


def test_update_source_api_persists_scheduler_config() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest scheduler source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(
            f"/api/monitors/{source_id}",
            json={
                "scheduler_config": {
                    "interval_seconds": 120,
                    "jitter_percent": 10,
                    "allowed_windows": ["09:00-23:00"],
                    "stop_after_vinted_session_uses": 3,
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
            "stop_after_vinted_session_uses": 3,
        }

        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.is_active is False
            assert source.scheduler_config["interval_seconds"] == 120
            assert source.scheduler_config["stop_after_vinted_session_uses"] == 3
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_persists_monitor_filter_definition() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest filter source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(
            f"/api/monitors/{source_id}",
            json={"filter_definition": {"blacklist_terms": [" roto ", "manchas", "roto", ""]}},
        )

        assert response.status_code == 200
        assert response.json()["filter_definition"] == {"blacklist_terms": ["roto", "manchas"]}
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.filter_definition == {"blacklist_terms": ["roto", "manchas"]}
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_rejects_legacy_filter_rule_ids_field() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest legacy filter source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(f"/api/monitors/{source_id}", json={"filter_rule_ids": []})

        assert response.status_code == 422
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_rejects_legacy_is_active_field() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest no legacy active patch", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(f"/api/monitors/{source_id}", json={"is_active": True})

        assert response.status_code == 422
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.is_active is False
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_rejects_active_monitor_configuration_change() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest active edit source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]
    with SessionLocal() as db:
        source = db.get(SearchSource, source_id)
        assert source is not None
        source.is_active = True
        source.monitor_mode = "continuous"
        db.commit()

    try:
        response = client.patch(f"/api/monitors/{source_id}", json={"filter_definition": {"blacklist_terms": ["roto"]}})

        assert response.status_code == 409
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.filter_definition == {"blacklist_terms": []}
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_rejects_monitor_level_proxy_field() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest no monitor proxy", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(f"/api/monitors/{source_id}", json={"proxy_profile_id": 1})

        assert response.status_code == 422
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_rejects_invalid_scheduler_config_without_mutation() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest invalid scheduler source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(
            f"/api/monitors/{source_id}",
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


def test_update_source_api_clears_duration_when_payload_sets_null() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest duration cleanup source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        duration_response = client.patch(
            f"/api/monitors/{source_id}",
            json={
                "monitor_mode": "duration",
                "duration_minutes": 15,
                "scheduler_config": {"interval_seconds": 120, "jitter_percent": 10, "allowed_windows": []},
            },
        )
        assert duration_response.status_code == 200
        assert duration_response.json()["duration_minutes"] == 15

        manual_response = client.patch(
            f"/api/monitors/{source_id}",
            json={"monitor_mode": "manual", "duration_minutes": None},
        )

        assert manual_response.status_code == 200
        assert manual_response.json()["monitor_mode"] == "manual"
        assert manual_response.json()["duration_minutes"] is None
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.duration_minutes is None
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_delete_source_api_archives_and_hides_source_idempotently() -> None:
    client = TestClient(app)
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest archived source", "url": "https://www.vinted.es/catalog?search_text="},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.delete(f"/api/monitors/{source_id}")
        assert response.status_code == 204

        second_response = client.delete(f"/api/monitors/{source_id}")
        assert second_response.status_code == 204

        list_response = client.get("/api/monitors")
        assert list_response.status_code == 200
        assert all(source["id"] != source_id for source in list_response.json())

        patch_response = client.patch(f"/api/monitors/{source_id}", json={"name": "pytest archived source renamed"})
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
        "/api/monitors",
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
        response = client.delete(f"/api/monitors/{source_id}")
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


def test_archive_remains_authoritative_when_queue_cleanup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest archive redis outage",
            url="https://www.vinted.es/catalog?search_text=archive-outage",
            normalized_query={"search_text": ["archive-outage"]},
            is_active=True,
            monitor_mode="continuous",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        db.refresh(source)
        source_id = source.id

    monkeypatch.setattr(
        search_sources_service,
        "cancel_ready_task_for_source",
        lambda *args, **kwargs: (_ for _ in ()).throw(search_sources_service.TaskQueueError("redis down")),
    )
    try:
        with SessionLocal() as db:
            archive_source(db, source_id)

        with SessionLocal() as db:
            archived = db.get(SearchSource, source_id)
            assert archived is not None
            assert archived.archived_at is not None
            assert archived.is_active is False
            assert archived.next_run_at is None
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            db.commit()


@pytest.mark.parametrize("operation", ["start", "update", "stop"])
def test_source_mutation_waits_for_archive_and_rejects_stale_state(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    with SessionLocal() as db:
        source = SearchSource(
            name=f"pytest archive race {operation}",
            url="https://www.vinted.es/catalog?search_text=&order=newest_first",
            normalized_query={"order": ["newest_first"]},
            is_active=operation == "stop",
            monitor_mode="continuous",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    archive_holds_lock = Event()
    allow_archive_commit = Event()
    original_invalidate = search_sources_service.invalidate_vinted_sessions_for_source

    def pause_archive_before_commit(db, locked_source_id: int, *, reason: str) -> int:
        archive_holds_lock.set()
        assert allow_archive_commit.wait(timeout=5)
        return original_invalidate(db, locked_source_id, reason=reason)

    monkeypatch.setattr(
        search_sources_service,
        "invalidate_vinted_sessions_for_source",
        pause_archive_before_commit,
    )

    def archive() -> None:
        with SessionLocal() as db:
            archive_source(db, source_id)

    def mutate() -> None:
        assert archive_holds_lock.wait(timeout=5)
        with SessionLocal() as db:
            if operation == "start":
                start_source_monitor(db, source_id)
            elif operation == "update":
                update_source(db, source_id, name="pytest stale rename")
            else:
                stop_source_monitor(db, source_id)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            archive_future = executor.submit(archive)
            assert archive_holds_lock.wait(timeout=5)
            mutation_future = executor.submit(mutate)
            with pytest.raises(FutureTimeoutError):
                mutation_future.result(timeout=1)
            allow_archive_commit.set()
            archive_future.result(timeout=5)
            with pytest.raises(SearchSourceNotFoundError):
                mutation_future.result(timeout=5)

        with SessionLocal() as db:
            persisted = db.get(SearchSource, source_id)
            assert persisted is not None
            assert persisted.archived_at is not None
            assert persisted.is_active is False
            assert persisted.name == f"pytest archive race {operation}"
            assert (
                db.query(MonitorSession)
                .filter(MonitorSession.source_id == source_id, MonitorSession.stopped_at.is_(None))
                .one_or_none()
                is None
            )
    finally:
        allow_archive_commit.set()
        with SessionLocal() as db:
            persisted = db.get(SearchSource, source_id)
            if persisted is not None:
                db.delete(persisted)
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
