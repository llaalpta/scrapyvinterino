import json

from vinted_monitor.core.redaction import fingerprint_secret, mask_secret, safe_headers, safe_secret_marker
from vinted_monitor.services.run_events import _redacted_details, sanitize_url


def test_safe_secret_marker_masks_and_fingerprints_without_raw_value() -> None:
    marker = safe_secret_marker("access_token_web", "very-secret-token-value", kind="cookie")

    serialized = json.dumps(marker)
    assert marker == {
        "kind": "cookie",
        "name": "access_token_web",
        "masked": "very****alue",
        "length": 23,
        "fingerprint": fingerprint_secret("very-secret-token-value"),
    }
    assert "very-secret-token-value" not in serialized


def test_short_secret_mask_does_not_reveal_characters() -> None:
    assert mask_secret("secret") == "<masked>"


def test_run_event_details_are_redacted_recursively() -> None:
    session_marker = safe_secret_marker("access_token_web", "very-secret-token-value", kind="cookie")
    details = _redacted_details(
        {
            "headers": {"Authorization": "Bearer raw-token"},
            "response_headers": {"x-v-udt": "raw-vinted-udt-value", "x-request-id": "public-request-id"},
            "nested": [{"set-cookie": "access_token_web=raw-cookie; Path=/"}],
            "message": "token=raw-token",
            "session_markers": [session_marker],
        }
    )

    serialized = json.dumps(details)
    assert "raw-token" not in serialized
    assert "raw-cookie" not in serialized
    assert details["headers"]["Authorization"] == "<redacted>"
    assert details["response_headers"]["x-v-udt"] == "<redacted>"
    assert details["response_headers"]["x-request-id"] == "public-request-id"
    assert details["nested"][0]["set-cookie"] == "<redacted>"
    assert details["message"] == "token=<redacted>"
    assert details["session_markers"][0]["masked"] == session_marker["masked"]
    assert details["session_markers"][0]["fingerprint"] == session_marker["fingerprint"]


def test_safe_headers_expose_only_safe_markers_for_cookies_and_tokens() -> None:
    headers = safe_headers(
        {
            "Authorization": "Bearer secret-token-value",
            "Set-Cookie": "access_token_web=anonymous-secret-value; Path=/",
            "User-Agent": "pytest-browser",
        }
    )

    serialized = json.dumps(headers)
    assert headers["Authorization"]["masked"] == "Bear****alue"
    assert headers["Set-Cookie"][0]["masked"] == "anon****alue"
    assert headers["User-Agent"] == "pytest-browser"
    assert "secret-token-value" not in serialized
    assert "anonymous-secret-value" not in serialized


def test_safe_headers_masks_vinted_session_identity_headers() -> None:
    headers = safe_headers(
        {
            "x-v-udt": "raw-vinted-udt-value",
            "x-anon-id": "00000000-1111-2222-3333-444444444444",
            "x-request-id": "public-request-id",
        }
    )

    serialized = json.dumps(headers)
    assert headers["x-v-udt"]["masked"] == "raw-****alue"
    assert headers["x-anon-id"]["masked"] == "0000****4444"
    assert headers["x-request-id"] == "public-request-id"
    assert "raw-vinted-udt-value" not in serialized
    assert "00000000-1111-2222-3333-444444444444" not in serialized


def test_sanitize_url_removes_userinfo_and_sensitive_query_values() -> None:
    sanitized = sanitize_url("https://user:pass@example.test/catalog?token=secret&search_text=polo")

    assert sanitized == "https://<redacted>:<redacted>@example.test/catalog?token=%3Credacted%3E&search_text=polo"
