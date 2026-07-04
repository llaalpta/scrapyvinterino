import json

from vinted_monitor.core.redaction import fingerprint_secret, mask_secret, safe_secret_marker
from vinted_monitor.services.run_events import _redacted_details, sanitize_url


def test_safe_secret_marker_masks_and_fingerprints_without_raw_value() -> None:
    marker = safe_secret_marker("access_token_web", "very-secret-token-value", kind="cookie")

    serialized = json.dumps(marker)
    assert marker == {
        "kind": "cookie",
        "name": "access_token_web",
        "masked": "ver****lue",
        "length": 23,
        "fingerprint": fingerprint_secret("very-secret-token-value"),
    }
    assert "very-secret-token-value" not in serialized


def test_short_secret_mask_does_not_reveal_characters() -> None:
    assert mask_secret("secret") == "<masked>"


def test_run_event_details_are_redacted_recursively() -> None:
    details = _redacted_details(
        {
            "headers": {"Authorization": "Bearer raw-token"},
            "nested": [{"set-cookie": "access_token_web=raw-cookie; Path=/"}],
            "message": "token=raw-token",
            "session_markers": [{"name": "access_token_web", "masked": "abc****xyz", "fingerprint": "sha256:123"}],
        }
    )

    serialized = json.dumps(details)
    assert "raw-token" not in serialized
    assert "raw-cookie" not in serialized
    assert details["headers"]["Authorization"] == "<redacted>"
    assert details["nested"][0]["set-cookie"] == "<redacted>"
    assert details["message"] == "token=<redacted>"
    assert details["session_markers"][0]["masked"] == "abc****xyz"
    assert details["session_markers"][0]["fingerprint"] == "sha256:123"


def test_sanitize_url_removes_userinfo_and_sensitive_query_values() -> None:
    sanitized = sanitize_url("https://user:pass@example.test/catalog?token=secret&search_text=polo")

    assert sanitized == "https://<redacted>:<redacted>@example.test/catalog?token=%3Credacted%3E&search_text=polo"
