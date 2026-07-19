from types import SimpleNamespace

from vinted_monitor.providers.transfer_metrics import (
    aggregate_proxy_traffic_estimate,
    attach_transfer_observation,
    merge_transfer_observations,
    response_transfer_observation,
    transfer_observation_from_exception,
)


def _response(**overrides: int) -> SimpleNamespace:
    values = {
        "request_size": 100,
        "upload_size": 20,
        "header_size": 40,
        "download_size": 840,
        "redirect_count": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_response_observation_uses_curl_counters_and_redirect_count() -> None:
    observation = response_transfer_observation(_response(redirect_count=2), category="catalog")

    assert observation == {
        "category": "catalog",
        "observed_requests": 3,
        "unobserved_attempts": 0,
        "request_size_bytes": 100,
        "upload_size_bytes": 20,
        "header_size_bytes": 40,
        "download_size_bytes": 840,
        "total_observed_bytes": 1000,
    }


def test_manual_redirect_observations_merge_without_losing_categories() -> None:
    first = response_transfer_observation(_response(request_size=80, download_size=20), category="detail")
    second = response_transfer_observation(_response(request_size=90, download_size=30), category="detail")

    merged = merge_transfer_observations(first, second, category="detail")

    assert merged["observed_requests"] == 2
    assert merged["request_size_bytes"] == 170
    assert merged["download_size_bytes"] == 50
    assert merged["total_observed_bytes"] == 340


def test_exception_with_response_is_observed_and_without_response_is_explicitly_partial() -> None:
    with_response = RuntimeError("transport")
    with_response.response = _response(request_size=30, upload_size=0, header_size=10, download_size=60)

    assert transfer_observation_from_exception(with_response, category="egress")["total_observed_bytes"] == 100
    assert transfer_observation_from_exception(with_response, category="egress")["unobserved_attempts"] == 0
    assert transfer_observation_from_exception(RuntimeError("no response"), category="egress") == {
        "category": "egress",
        "observed_requests": 0,
        "unobserved_attempts": 1,
        "request_size_bytes": 0,
        "upload_size_bytes": 0,
        "header_size_bytes": 0,
        "download_size_bytes": 0,
        "total_observed_bytes": 0,
    }


def test_attached_redirect_observation_takes_precedence_over_exception_response() -> None:
    exc = RuntimeError("after redirect")
    exc.response = _response(request_size=1, download_size=1)
    attached = merge_transfer_observations(
        response_transfer_observation(_response(request_size=10, download_size=10), category="session_setup"),
        response_transfer_observation(_response(request_size=20, download_size=20), category="session_setup"),
        category="session_setup",
    )
    attach_transfer_observation(exc, attached)

    observation = transfer_observation_from_exception(exc, category="session_setup")

    assert observation["observed_requests"] == 2
    assert observation["request_size_bytes"] == 30
    assert observation["download_size_bytes"] == 30


def test_run_aggregate_accumulates_totals_and_category_breakdown() -> None:
    aggregate = aggregate_proxy_traffic_estimate(
        None,
        response_transfer_observation(_response(request_size=10, download_size=90), category="catalog"),
    )
    aggregate = aggregate_proxy_traffic_estimate(
        aggregate,
        response_transfer_observation(_response(request_size=20, download_size=180), category="detail"),
    )

    assert aggregate["version"] == 1
    assert aggregate["observed_requests"] == 2
    assert aggregate["total_observed_bytes"] == 420
    assert aggregate["by_category"] == {
        "catalog": {"observed_requests": 1, "unobserved_attempts": 0, "total_observed_bytes": 160},
        "detail": {"observed_requests": 1, "unobserved_attempts": 0, "total_observed_bytes": 260},
    }


def test_invalid_negative_and_boolean_counters_normalize_to_zero() -> None:
    observation = response_transfer_observation(
        SimpleNamespace(
            request_size=-1,
            upload_size=True,
            header_size="invalid",
            download_size=None,
            redirect_count=-3,
        ),
        category="unknown",
    )

    assert observation["category"] == "session_setup"
    assert observation["observed_requests"] == 1
    assert observation["total_observed_bytes"] == 0
