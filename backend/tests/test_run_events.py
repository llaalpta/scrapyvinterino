import json
from datetime import UTC, datetime

from vinted_monitor.api.schemas import RunEventRead
from vinted_monitor.core.redaction import safe_cookie_markers, safe_headers, safe_secret_marker
from vinted_monitor.services.run_events import (
    redact_persisted_run_event_details,
    redact_run_event_details,
    sanitize_url,
)


def test_run_event_redaction_preserves_scalar_diagnostics_for_sensitive_keys() -> None:
    details = redact_run_event_details(
        {
            "datadome_cookie": False,
            "csrf_token_found": True,
            "ddk_length": 30,
            "jspl_length": 450,
            "csrf_token": "raw-csrf-secret",
            "datadome": "raw-datadome-secret",
        }
    )

    assert details["datadome_cookie"] is False
    assert details["csrf_token_found"] is True
    assert details["ddk_length"] == 30
    assert details["jspl_length"] == 450
    assert details["csrf_token"] == "<redacted>"
    assert details["datadome"] == "<redacted>"


def test_run_event_redaction_keeps_only_safe_secret_markers() -> None:
    secrets = {
        "authorization": "Bearer audit-authorization-secret",
        "cookie": "audit-cookie-secret-value",
        "csrf": "audit-csrf-secret-value",
        "proxy": "audit-proxy-session-secret",
    }

    details = redact_run_event_details(
        {
            "request_headers": safe_headers(
                {
                    "Authorization": secrets["authorization"],
                    "Cookie": f"datadome={secrets['cookie']}",
                }
            ),
            "cookies_before": safe_cookie_markers({"datadome": secrets["cookie"]}),
            "proxy_session": safe_secret_marker("proxy_session", secrets["proxy"], kind="proxy_session"),
            "nested": {"csrf_token": secrets["csrf"]},
        }
    )
    serialized = json.dumps(details)

    assert all(secret not in serialized for secret in secrets.values())
    assert details["nested"]["csrf_token"] == "<redacted>"
    assert details["cookies_before"][0]["fingerprint"].startswith("sha256:")
    assert details["proxy_session"]["masked"] != secrets["proxy"]


def test_sanitize_url_redacts_userinfo_and_sensitive_query_values() -> None:
    sanitized = sanitize_url(
        "http://proxy-user:proxy-pass@example.test/path?access_token_web=raw-token&order=newest_first"
    )

    assert "proxy-user" not in sanitized
    assert "proxy-pass" not in sanitized
    assert "raw-token" not in sanitized
    assert "access_token_web=%3Credacted%3E" in sanitized
    assert "order=newest_first" in sanitized


def test_run_event_redaction_never_returns_response_content() -> None:
    details = redact_run_event_details(
        {
            "body_snippet": "<html>direct body</html>",
            "response": {"body_snippet": "<html>nested body</html>"},
        }
    )

    assert details == {
        "body_snippet": "<redacted>",
        "response": {"body_snippet": "<redacted>"},
    }


def test_rest_schema_matches_persisted_redaction_after_jsonb_marker_roundtrip() -> None:
    marker = safe_secret_marker("access_token_web", "safe-marker-for-jsonb", kind="cookie")
    invalid_marker = {**marker, "unexpected": "not-a-marker-field"}
    persisted = json.loads(
        json.dumps(
            {
                "http_session": marker,
                "csrf_token": marker,
                "request_headers": {
                    "Authorization": marker,
                    "Cookie": [marker],
                    "X-Request-ID": "qa-request",
                },
                "response_headers": {"Set-Cookie": [marker]},
                "refresh_token": invalid_marker,
                "session_markers": [marker, invalid_marker],
                "response_body": "raw-response-canary",
                "note": "authorization=raw-note-canary",
            }
        )
    )
    expected = redact_persisted_run_event_details(persisted)

    event = RunEventRead.model_validate(
        {
            "id": 1,
            "run_id": None,
            "source_id": 3,
            "phase": "pytest_persisted_redaction",
            "level": "info",
            "method": None,
            "url": None,
            "status_code": None,
            "duration_ms": None,
            "proxy_profile_id": None,
            "egress_ip": None,
            "user_agent": None,
            "auth_mode": None,
            "message": None,
            "details": persisted,
            "created_at": datetime.now(UTC),
        }
    )

    assert event.details == expected
    assert event.details["http_session"]["masked"] == marker["masked"]
    assert event.details["csrf_token"]["fingerprint"] == marker["fingerprint"]
    assert event.details["request_headers"]["Authorization"]["length"] == marker["length"]
    assert event.details["request_headers"]["Cookie"][0]["masked"] == marker["masked"]
    assert event.details["response_headers"]["Set-Cookie"][0]["fingerprint"] == marker["fingerprint"]
    assert event.details["request_headers"]["X-Request-ID"] == "qa-request"
    assert event.details["refresh_token"] == "<redacted>"
    assert event.details["session_markers"] == "<redacted>"
    assert event.details["response_body"] == "<redacted>"
    assert event.details["note"] == "authorization=<redacted>"
    assert "raw-response-canary" not in json.dumps(event.details)
    assert "raw-note-canary" not in json.dumps(event.details)


def test_caller_forged_marker_shapes_and_mixed_collections_are_redacted_before_persistence() -> None:
    marker = safe_secret_marker("access_token_web", "forged-marker-source", kind="cookie")
    forged = dict(marker)

    redacted = redact_run_event_details(
        {
            "http_session": forged,
            "csrf_token": forged,
            "request_headers": {"Authorization": forged},
            "session_markers": [forged, {"raw": "token=raw-mixed-canary"}],
        }
    )

    assert redacted == {
        "http_session": "<redacted>",
        "csrf_token": "<redacted>",
        "request_headers": {"Authorization": "<redacted>"},
        "session_markers": "<redacted>",
    }
