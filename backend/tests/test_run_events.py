import json

from vinted_monitor.core.redaction import safe_cookie_markers, safe_headers, safe_secret_marker
from vinted_monitor.services.run_events import redact_run_event_details, sanitize_url


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
