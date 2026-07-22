from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import delete

from vinted_monitor.api.schemas import MonitorStatsRead
from vinted_monitor.db.models import MonitorSession, Run, SearchSource
from vinted_monitor.db.session import SessionLocal
from vinted_monitor.services.monitor_stats import (
    _proxy_traffic_summary,
    _session_traffic_runs,
    get_monitor_stats,
)


def test_proxy_traffic_summary_exposes_each_explicit_state() -> None:
    assert _proxy_traffic_summary([]).state == "no_runs"
    assert _proxy_traffic_summary([_run({"egress_mode": "direct"})]).state == "not_applicable"
    assert _proxy_traffic_summary([_run({"egress_mode": "proxy"})]).state == "not_measured"

    measured = _proxy_traffic_summary([_run(_proxy_metadata())])
    assert measured.state == "measured"
    assert measured.runs_count == 1
    assert measured.observed_requests == 2
    assert measured.unobserved_attempts == 0
    assert measured.total_observed_bytes == 1000

    partial = _proxy_traffic_summary(
        [
            _run(_proxy_metadata()),
            _run({"egress_mode": "proxy"}),
        ]
    )
    assert partial.state == "partial"
    assert partial.runs_count == 2
    assert partial.observed_requests == 2
    assert partial.total_observed_bytes == 1000


def test_proxy_traffic_summary_marks_response_less_and_malformed_estimates_partial() -> None:
    response_less = _proxy_traffic_summary(
        [
            _run(
                _proxy_metadata(
                    observed_requests=0,
                    unobserved_attempts=1,
                    request_size_bytes=0,
                    header_size_bytes=0,
                    download_size_bytes=0,
                )
            )
        ]
    )
    assert response_less.state == "partial"
    assert response_less.observed_requests == 0
    assert response_less.unobserved_attempts == 1
    assert response_less.total_observed_bytes == 0

    malformed = _proxy_traffic_summary(
        [
            _run(
                {
                    "egress_mode": "proxy",
                    "proxy_traffic_estimate": {
                        "version": 1,
                        "observed_requests": "2",
                    },
                }
            )
        ]
    )
    assert malformed.state == "partial"
    assert malformed.observed_requests is None
    assert malformed.total_observed_bytes is None

    boolean_version = _proxy_metadata()["proxy_traffic_estimate"]
    boolean_version["version"] = True
    assert _proxy_traffic_summary(
        [_run({"egress_mode": "proxy", "proxy_traffic_estimate": boolean_version})]
    ).state == "partial"


def test_session_proxy_traffic_includes_linked_baseline_and_excludes_failed_baseline() -> None:
    session = MonitorSession(id=41, source_id=1, started_at=datetime(2026, 7, 22, tzinfo=UTC))
    linked_baseline = _run(
        {
            **_proxy_metadata(),
            "baseline_reason": "session_start",
            "opened_monitor_session_id": session.id,
        },
        trigger="baseline",
    )
    session_run = _run(_proxy_metadata(total=500), monitor_session_id=session.id)
    failed_baseline = _run(
        {
            **_proxy_metadata(total=400),
            "baseline_reason": "session_start",
            "opened_monitor_session_id": session.id,
        },
        trigger="baseline",
        status="failed",
    )

    scoped, linkage_complete = _session_traffic_runs(
        [failed_baseline, linked_baseline, session_run],
        session,
    )
    summary = _proxy_traffic_summary(scoped, linkage_complete=linkage_complete)

    assert linkage_complete is True
    assert scoped == [linked_baseline, session_run]
    assert summary.state == "measured"
    assert summary.runs_count == 2
    assert summary.total_observed_bytes == 1500
    assert _proxy_traffic_summary([failed_baseline]).total_observed_bytes == 400


def test_session_proxy_traffic_does_not_guess_historical_baseline_linkage() -> None:
    session = MonitorSession(id=7, source_id=1, started_at=datetime(2026, 7, 22, tzinfo=UTC))
    historical_baseline = _run(_proxy_metadata(), trigger="baseline")
    session_run = _run(_proxy_metadata(total=500), monitor_session_id=session.id)

    scoped, linkage_complete = _session_traffic_runs([historical_baseline, session_run], session)
    summary = _proxy_traffic_summary(scoped, linkage_complete=linkage_complete)

    assert linkage_complete is False
    assert scoped == [session_run]
    assert summary.state == "partial"
    assert summary.total_observed_bytes == 500


def test_monitor_stats_aggregates_all_traffic_without_changing_business_metrics() -> None:
    token = uuid4().hex
    source_id: int | None = None
    now = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    try:
        with SessionLocal() as db:
            source = SearchSource(
                name=f"pytest proxy traffic {token}",
                url=f"https://www.vinted.es/catalog?search_text={token}",
                normalized_query={"search_text": [token]},
                is_active=True,
                monitor_mode="manual",
                scheduler_config={},
            )
            db.add(source)
            db.flush()
            session = MonitorSession(source_id=source.id, started_at=now)
            db.add(session)
            db.flush()
            source_id = source.id
            db.add_all(
                [
                    _persisted_run(
                        source.id,
                        {**_proxy_metadata(), "baseline_reason": "session_start", "opened_monitor_session_id": session.id},
                        trigger="baseline",
                        started_at=now,
                    ),
                    _persisted_run(
                        source.id,
                        _proxy_metadata(total=500),
                        monitor_session_id=session.id,
                        items_found=2,
                        opportunities_created=1,
                        started_at=now,
                    ),
                    _persisted_run(
                        source.id,
                        _proxy_metadata(
                            observed_requests=0,
                            unobserved_attempts=1,
                            request_size_bytes=0,
                            header_size_bytes=0,
                            download_size_bytes=0,
                        ),
                        trigger="baseline",
                        status="failed",
                        started_at=now,
                    ),
                    _persisted_run(source.id, {"egress_mode": "direct"}, trigger="session_prepare", started_at=now),
                    _persisted_run(source.id, {"egress_mode": "proxy"}, trigger="detail_probe", started_at=now),
                ]
            )
            db.commit()

        with SessionLocal() as db:
            stats = get_monitor_stats(db, source_id, range_name="all", now=now)
            payload = MonitorStatsRead.model_validate(stats, from_attributes=True).model_dump()

        assert stats.session_summary.runs_count == 1
        assert stats.session_summary.items_found == 2
        assert stats.session_summary.opportunities_created == 1
        assert stats.historical_summary.runs_count == 1
        assert sum(point.items_found for point in stats.chart_points) == 2
        assert payload["session_proxy_traffic"] == {
            "state": "measured",
            "runs_count": 2,
            "observed_requests": 4,
            "unobserved_attempts": 0,
            "total_observed_bytes": 1500,
        }
        assert payload["historical_proxy_traffic"] == {
            "state": "partial",
            "runs_count": 5,
            "observed_requests": 4,
            "unobserved_attempts": 1,
            "total_observed_bytes": 1500,
        }
    finally:
        if source_id is not None:
            with SessionLocal() as db:
                db.execute(delete(Run).where(Run.source_id == source_id))
                db.execute(delete(MonitorSession).where(MonitorSession.source_id == source_id))
                db.execute(delete(SearchSource).where(SearchSource.id == source_id))
                db.commit()


def _run(
    runtime_metadata: dict,
    *,
    trigger: str = "manual",
    status: str = "success",
    monitor_session_id: int | None = None,
) -> Run:
    return Run(
        source_id=1,
        monitor_session_id=monitor_session_id,
        status=status,
        trigger=trigger,
        started_at=datetime(2026, 7, 22, tzinfo=UTC),
        runtime_metadata=runtime_metadata,
    )


def _persisted_run(
    source_id: int,
    runtime_metadata: dict,
    *,
    trigger: str = "manual",
    status: str = "success",
    monitor_session_id: int | None = None,
    items_found: int = 0,
    opportunities_created: int = 0,
    started_at: datetime,
) -> Run:
    return Run(
        source_id=source_id,
        monitor_session_id=monitor_session_id,
        status=status,
        trigger=trigger,
        started_at=started_at,
        finished_at=started_at,
        items_found=items_found,
        items_filter_passed=opportunities_created,
        items_discarded_by_filters=max(items_found - opportunities_created, 0),
        items_filter_pending=0,
        opportunities_created=opportunities_created,
        runtime_metadata=runtime_metadata,
    )


def _proxy_metadata(
    *,
    total: int = 1000,
    observed_requests: int = 2,
    unobserved_attempts: int = 0,
    request_size_bytes: int = 100,
    header_size_bytes: int = 200,
    download_size_bytes: int | None = None,
) -> dict:
    download = total - request_size_bytes - header_size_bytes if download_size_bytes is None else download_size_bytes
    return {
        "egress_mode": "proxy",
        "proxy_traffic_estimate": {
            "version": 1,
            "observed_requests": observed_requests,
            "unobserved_attempts": unobserved_attempts,
            "request_size_bytes": request_size_bytes,
            "upload_size_bytes": 0,
            "header_size_bytes": header_size_bytes,
            "download_size_bytes": download,
            "total_observed_bytes": request_size_bytes + header_size_bytes + download,
        },
    }
