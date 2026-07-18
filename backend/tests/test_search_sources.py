from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from api_client import authenticated_test_client
from pydantic import ValidationError

from vinted_monitor.api.schemas import SearchSourceCreate
from vinted_monitor.db.models import AppSetting, MonitorSession, Run, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services import search_sources as search_sources_service
from vinted_monitor.services.scheduler import SCHEDULER_SETTING_KEY
from vinted_monitor.services.search_sources import (
    SearchSourceActiveError,
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


def test_validate_search_source_name_enforces_database_length_after_trim() -> None:
    assert validate_search_source_name(f" {'n' * 160} ") == "n" * 160

    with pytest.raises(ValueError, match="160 characters"):
        validate_search_source_name("n" * 161)


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


def test_start_source_monitor_opens_manual_session_without_deadline() -> None:
    started_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest manual session activation",
            url="https://www.vinted.es/catalog?search_text=manual-session",
            normalized_query={"search_text": ["manual-session"]},
            is_active=False,
            monitor_mode="manual",
            scheduler_config={},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        with SessionLocal() as db:
            activated = start_source_monitor(db, source_id, now=started_at)
            sessions = list(db.query(MonitorSession).filter(MonitorSession.source_id == source_id))

            assert activated.is_active is True
            assert activated.monitor_started_at == started_at
            assert activated.monitor_until is None
            assert activated.next_run_at is None
            assert len(sessions) == 1
            assert sessions[0].started_at == started_at
            assert sessions[0].stopped_at is None
    finally:
        with SessionLocal() as db:
            db.query(MonitorSession).filter(MonitorSession.source_id == source_id).delete(synchronize_session=False)
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            db.commit()


def test_start_source_monitor_rejects_a_second_activation() -> None:
    with SessionLocal() as db:
        source = SearchSource(
            name="pytest duplicate activation",
            url="https://www.vinted.es/catalog?search_text=duplicate-activation",
            normalized_query={"search_text": ["duplicate-activation"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": []},
        )
        db.add(source)
        db.commit()
        source_id = source.id

    try:
        with SessionLocal() as db:
            first = start_source_monitor(db, source_id)
            first_started_at = first.monitor_started_at
            first_deadline = first.next_run_at

            with pytest.raises(SearchSourceActiveError, match="already active"):
                start_source_monitor(db, source_id)

            db.refresh(first)
            assert first.monitor_started_at == first_started_at
            assert first.next_run_at == first_deadline
            assert db.query(MonitorSession).filter(MonitorSession.source_id == source_id).count() == 1
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


def test_search_source_create_schema_rejects_name_beyond_storage_limit() -> None:
    with pytest.raises(ValidationError, match="160 characters"):
        SearchSourceCreate(name="n" * 161, url="https://www.vinted.es/catalog")


def test_create_source_api_persists_normalized_query() -> None:
    client = authenticated_test_client()
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
    client = authenticated_test_client()
    response = client.post(
        "/api/monitors",
        json={"name": "bad source", "url": "https://example.com/catalog"},
    )

    assert response.status_code == 422


def test_create_source_api_rejects_unsupported_catalog_filter_without_persisting() -> None:
    client = authenticated_test_client()
    response = client.post(
        "/api/monitors",
        json={"name": "bad filter source", "url": "https://www.vinted.es/catalog?catalog[]=76&color_ids[]=12"},
    )

    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.query(SearchSource).filter(SearchSource.name == "bad filter source").one_or_none() is None


def test_update_source_api_persists_scheduler_config() -> None:
    client = authenticated_test_client()
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


def test_update_source_api_persists_identity_on_same_monitor() -> None:
    client = authenticated_test_client()
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest identity source", "url": "https://www.vinted.es/catalog?search_text=before"},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]
    with SessionLocal() as db:
        historical_run = Run(
            source_id=source_id,
            status="success",
            trigger="manual",
            finished_at=datetime.now(UTC),
            runtime_metadata={},
        )
        db.add(historical_run)
        db.commit()
        historical_run_id = historical_run.id

    try:
        response = client.patch(
            f"/api/monitors/{source_id}",
            json={
                "name": "  pytest identity renamed  ",
                "url": "  https://www.vinted.es/catalog?search_text=after&brand_ids[]=88  ",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == source_id
        assert body["name"] == "pytest identity renamed"
        assert body["url"] == "https://www.vinted.es/catalog?search_text=after&brand_ids[]=88"
        assert body["normalized_query"] == {"brand_ids[]": ["88"], "search_text": ["after"]}
        assert body["catalog_filter_compatibility"]["compatible"] is True
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.id == source_id
            assert source.name == body["name"]
            assert source.url == body["url"]
            assert source.normalized_query == body["normalized_query"]
            persisted_run = db.get(Run, historical_run_id)
            assert persisted_run is not None and persisted_run.source_id == source_id
    finally:
        with SessionLocal() as db:
            db.query(Run).filter(Run.source_id == source_id).delete(synchronize_session=False)
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


@pytest.mark.parametrize(
    ("payload", "expected_error"),
    [
        (
            {
                "name": "n" * 161,
                "url": "https://www.vinted.es/catalog?search_text=must-not-persist",
            },
            "160 characters",
        ),
        (
            {
                "name": "pytest forbidden invalid URL rename",
                "url": "https://www.vinted.es/catalog?search_text=must-not-persist&color_ids[]=12",
            },
            "color_ids",
        ),
    ],
)
def test_update_source_api_rejects_invalid_identity_without_mutation(
    payload: dict[str, str],
    expected_error: str,
) -> None:
    client = authenticated_test_client()
    create_response = client.post(
        "/api/monitors",
        json={"name": "pytest identity limit", "url": "https://www.vinted.es/catalog?search_text=unchanged"},
    )
    assert create_response.status_code == 201
    source_id = create_response.json()["id"]

    try:
        response = client.patch(
            f"/api/monitors/{source_id}",
            json=payload,
        )

        assert response.status_code == 422
        assert expected_error in response.text
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.name == "pytest identity limit"
            assert source.url == "https://www.vinted.es/catalog?search_text=unchanged"
            assert source.normalized_query == {"search_text": ["unchanged"]}
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


def test_update_source_api_persists_monitor_filter_definition() -> None:
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
        response = client.patch(
            f"/api/monitors/{source_id}",
            json={
                "name": "pytest forbidden active rename",
                "url": "https://www.vinted.es/catalog?search_text=forbidden-active-edit",
            },
        )

        assert response.status_code == 409
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.name == "pytest active edit source"
            assert source.url == "https://www.vinted.es/catalog?search_text="
            assert source.normalized_query == {"search_text": [""]}
    finally:
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
                db.commit()


@pytest.mark.parametrize("run_status", ["running", "finalizing"])
def test_update_source_api_rejects_configuration_change_while_stop_is_draining(run_status: str) -> None:
    client = authenticated_test_client()
    with SessionLocal() as db:
        source = SearchSource(
            name=f"pytest draining edit source {run_status}",
            url="https://www.vinted.es/catalog?search_text=draining-edit",
            normalized_query={"search_text": ["draining-edit"]},
            is_active=False,
            monitor_mode="continuous",
            scheduler_config={"interval_seconds": 60, "jitter_percent": 0, "allowed_windows": []},
            filter_definition={"blacklist_terms": []},
        )
        db.add(source)
        db.flush()
        session = MonitorSession(source_id=source.id, started_at=datetime.now(UTC))
        db.add(session)
        db.flush()
        run = Run(
            source_id=source.id,
            monitor_session_id=session.id,
            status=run_status,
            trigger="scheduler",
            runtime_metadata={},
        )
        db.add(run)
        db.commit()
        source_id = source.id

    try:
        response = client.patch(
            f"/api/monitors/{source_id}",
            json={
                "name": "pytest forbidden draining rename",
                "url": "https://www.vinted.es/catalog?search_text=forbidden-draining-edit",
            },
        )

        assert response.status_code == 409
        with SessionLocal() as db:
            source = db.get(SearchSource, source_id)
            assert source is not None
            assert source.name == f"pytest draining edit source {run_status}"
            assert source.url == "https://www.vinted.es/catalog?search_text=draining-edit"
            assert source.normalized_query == {"search_text": ["draining-edit"]}
    finally:
        with SessionLocal() as db:
            db.query(Run).filter(Run.source_id == source_id).delete(synchronize_session=False)
            db.query(MonitorSession).filter(MonitorSession.source_id == source_id).delete(synchronize_session=False)
            source = db.get(SearchSource, source_id)
            if source is not None:
                db.delete(source)
            db.commit()


def test_update_source_api_rejects_monitor_level_proxy_field() -> None:
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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
    client = authenticated_test_client()
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


def test_scheduler_api_rejects_removed_ui_gate_without_mutating_settings() -> None:
    client = authenticated_test_client()
    with SessionLocal() as db:
        original = db.get(AppSetting, SCHEDULER_SETTING_KEY)
        original_exists = original is not None
        original_value = deepcopy(original.value) if original is not None else None

    response = client.patch("/api/scheduler", json={"enabled": True})

    assert response.status_code == 422
    get_response = client.get("/api/scheduler")
    assert get_response.status_code == 200
    body = get_response.json()
    assert "enabled" not in body
    assert "runtime_enabled" in body
    assert "effective_enabled" in body

    with SessionLocal() as db:
        persisted = db.get(AppSetting, SCHEDULER_SETTING_KEY)
        assert (persisted is not None) is original_exists
        assert (deepcopy(persisted.value) if persisted is not None else None) == original_value
