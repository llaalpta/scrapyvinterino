from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api_client import authenticated_test_client
from sqlalchemy import select

from vinted_monitor.core.config import get_settings
from vinted_monitor.db.models import ProxyProfile, SearchSource, VintedSession
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.providers.browser_profiles import profile_for_impersonate
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession
from vinted_monitor.services.proxies import create_proxy_profile
from vinted_monitor.services.vinted_sessions import (
    VintedSessionImportError,
    get_ready_vinted_session,
    resolve_vinted_session_eligibility,
    save_prepared_vinted_session,
)


def _metadata_eligibility(changes: dict[str, object]):
    settings = get_settings()
    profile = profile_for_impersonate(settings.curl_impersonate_browser)
    now = datetime.now(UTC)
    proxy = SimpleNamespace(
        id=7, name="proxy", country_code="ES", locale="es-ES", accept_language="en-GB,en;q=0.9",
        screen="1920x1080", vinted_screen="catalog",
    )
    values = {
        "id": 11, "source_id": 3, "proxy_profile_id": 7, "proxy_identity_generation": "generation-1",
        "status": "ready", "browser_profile": profile.name, "impersonate": profile.impersonate,
        "country_code": proxy.country_code, "locale": proxy.locale, "accept_language": proxy.accept_language,
        "viewport_size": proxy.screen, "vinted_screen": proxy.vinted_screen, "expires_at": now + timedelta(hours=1),
        "request_count": 0, "max_requests": 50, "last_used_at": None, "prepared_at": now, "created_at": now,
    }
    values.update(changes)
    return resolve_vinted_session_eligibility(
        [SimpleNamespace(**values)], source_id=3, proxy_profile=proxy, current_generation="generation-1",
        profile=profile, settings=settings, now=now,
    )


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"status": "incomplete", "proxy_identity_generation": "wrong"}, "status_incomplete"),
        ({"status": "invalid", "proxy_identity_generation": "wrong"}, "status_invalid"),
        ({"status": "future", "proxy_identity_generation": "wrong"}, "status_unrecognized"),
        ({"proxy_identity_generation": "wrong", "browser_profile": "wrong"}, "proxy_identity_mismatch"),
        ({"browser_profile": "wrong", "viewport_size": "1x1"}, "browser_profile_mismatch"),
        ({"viewport_size": "1365x768", "expires_at": datetime.min.replace(tzinfo=UTC)}, "request_context_mismatch"),
        ({"expires_at": datetime.min.replace(tzinfo=UTC), "request_count": 50}, "expired"),
        ({"request_count": 50}, "exhausted"),
    ),
)
def test_metadata_reason_precedence_includes_viewport(changes: dict[str, object], reason: str) -> None:
    eligibility = _metadata_eligibility(changes)
    assert eligibility is not None
    assert eligibility.usable_now is False
    assert eligibility.unusable_reason == reason


@pytest.fixture
def unreadable_rows():
    prefix = f"pytest prepared read {uuid4()}"
    raw_marker = "not-fernet-raw-ciphertext-marker"
    secret_marker = "prepared-context-secret-marker"
    settings = get_settings()
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        with SessionLocal() as db:
            source = SearchSource(
                name=f"{prefix} source", url="https://www.vinted.es/catalog?order=newest_first",
                normalized_query={"order": ["newest_first"]}, is_active=False, scheduler_config={}, monitor_mode="manual",
            )
            db.add(source)
            db.flush()
            proxy = create_proxy_profile(
                db, name=f"{prefix} proxy", scheme="http", kind="residential", host="proxy.example", port=8910,
                username="customer", password="test-password", country_code="ES", settings=settings,
            )
            profile = profile_for_impersonate(settings.curl_impersonate_browser)
            rows = []
            for sticky, age in (("unreadable-first", 10), ("valid-second", 1)):
                context = PreparedCatalogSession(
                    proxy_session_id=sticky, cookies={}, csrf_token=secret_marker, anon_id=secret_marker,
                    access_token_web=secret_marker, datadome=secret_marker, cf_bm=secret_marker, v_udt=secret_marker,
                    user_iso_locale=proxy.locale, vinted_screen=proxy.vinted_screen,
                )
                row = save_prepared_vinted_session(
                    db, source, proxy, proxy_session_id=sticky, profile=profile, context=context, settings=settings,
                )
                row.prepared_at = now - timedelta(minutes=age)
                row.expires_at = now + timedelta(hours=1)
                rows.append(row)
            rows[0].context_encrypted = raw_marker
            db.commit()
            yield source.id, proxy.id, rows[0].id, rows[1].id, raw_marker, secret_marker, now
    finally:
        with SessionLocal() as db:
            source_ids = list(db.scalars(select(SearchSource.id).where(SearchSource.name.like(f"{prefix}%"))))
            proxy_ids = list(db.scalars(select(ProxyProfile.id).where(ProxyProfile.name.like(f"{prefix}%"))))
            if source_ids:
                db.query(VintedSession).filter(VintedSession.source_id.in_(source_ids)).delete(synchronize_session=False)
                db.query(SearchSource).filter(SearchSource.id.in_(source_ids)).delete(synchronize_session=False)
            if proxy_ids:
                db.query(ProxyProfile).filter(ProxyProfile.id.in_(proxy_ids)).delete(synchronize_session=False)
            db.commit()


def _state(session_ids: tuple[int, int]):
    with SessionLocal() as db:
        rows = db.scalars(select(VintedSession).where(VintedSession.id.in_(session_ids)))
        return {row.id: (row.status, row.request_count, row.failure_count, row.context_encrypted) for row in rows}


def test_unreadable_canonical_is_read_only_safe_and_runtime_does_not_fall_through(unreadable_rows) -> None:
    source_id, proxy_id, unreadable_id, fallback_id, raw_marker, secret_marker, now = unreadable_rows
    ids = (unreadable_id, fallback_id)
    before = _state(ids)
    response = authenticated_test_client().get("/api/monitors")
    assert response.status_code == 200, response.text
    monitor = next(row for row in response.json() if row["id"] == source_id)
    assert len(monitor["prepared_sessions"]) == 1
    summary = monitor["prepared_sessions"][0]
    assert (summary["id"], summary["usable_now"], summary["unusable_reason"]) == (
        unreadable_id, False, "context_unreadable",
    )
    assert all(value is False for value in summary["context"].values())
    assert raw_marker not in response.text and secret_marker not in response.text
    assert _state(ids) == before

    with SessionLocal() as db:
        source, proxy = db.get(SearchSource, source_id), db.get(ProxyProfile, proxy_id)
        assert source is not None and proxy is not None
        with pytest.raises(VintedSessionImportError, match="unreadable"):
            get_ready_vinted_session(db, source, proxy, settings=get_settings(), now=now)
        db.rollback()
    assert _state(ids)[fallback_id] == before[fallback_id]
