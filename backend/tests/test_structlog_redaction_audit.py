import json

import pytest
import structlog

from vinted_monitor.core.logging import configure_logging
from vinted_monitor.core.redaction import (
    SafeSecretMarker,
    is_safe_secret_marker,
    redact_sensitive_text,
    redact_sensitive_value,
    safe_headers,
    safe_secret_marker,
)
from vinted_monitor.services.run_events import redact_persisted_run_event_details, redact_run_event_details


def test_sensitive_text_redaction_handles_quoted_mapping_keys() -> None:
    canary = "AUDIT-QUOTED-SECRET-45ab"

    python_mapping = redact_sensitive_text(f"{{'cookie': '{canary}'}}")
    json_mapping = redact_sensitive_text(f'{{"access_token_web":"{canary}"}}')

    assert canary not in python_mapping
    assert canary not in json_mapping


def test_sensitive_text_redaction_handles_escaped_quotes_inside_secret_values() -> None:
    canary = "AUDIT-ESCAPED-QUOTE-CANARY-9a2f"

    redacted = redact_sensitive_text(json.dumps({"cookie": f'prefix"{canary}'}))

    assert canary not in redacted


def test_sensitive_text_redaction_consumes_spaced_credentials_and_ambiguous_userinfo() -> None:
    canary = "AUDITSPACEDCREDENTIALCANARY"
    messages = [
        f"authorization: Basic {canary}",
        f"password=two words {canary}",
        f"proxy failed at https://proxy-user:part@secret-{canary}@proxy.example:823",
    ]

    for message in messages:
        assert canary not in redact_sensitive_text(message)


def test_structlog_output_redacts_sensitive_values_recursively(capsys) -> None:
    canary = "AUDIT-STRUCTLOG-CANARY-7f31"
    configure_logging("INFO")

    structlog.get_logger().error(
        "audit_exception",
        error=f"upstream failed with {{'cookie': '{canary}'}}",
        basic_error=f"authorization: Basic {canary}",
        proxy_url=f"https://proxy-user:{canary}@proxy.example:8443",
        nested={"authorization": f"Bearer {canary}"},
    )

    captured = capsys.readouterr()
    rendered = f"{captured.out}\n{captured.err}"
    assert canary not in rendered
    assert "<redacted>" in rendered
    assert "audit_exception" in rendered


def test_crafted_safe_marker_cannot_smuggle_nested_secret() -> None:
    canary = "AUDIT-MARKER-SMUGGLE-CANARY"
    crafted = {
        "kind": "header",
        "name": "authorization",
        "masked": "<masked>",
        "length": 20,
        "fingerprint": "sha256:0123456789ab",
        "token": canary,
    }

    rendered = json.dumps(redact_sensitive_value(crafted, key="authorization"))

    assert canary not in rendered
    assert rendered == '"<redacted>"'

    persisted = json.dumps(redact_run_event_details({"authorization": crafted}))
    assert canary not in persisted
    assert persisted == '{"authorization": "<redacted>"}'

    shape_only_forgery = {key: value for key, value in crafted.items() if key != "token"}
    shape_only_forgery["name"] = canary
    assert redact_sensitive_value(shape_only_forgery, key="authorization") == "<redacted>"
    assert redact_run_event_details({"authorization": shape_only_forgery}) == {
        "authorization": "<redacted>"
    }


def test_marker_container_keys_reject_raw_or_shape_only_values() -> None:
    canary = "AUDIT-RAW-MARKER-CONTAINER-CANARY"
    crafted_marker = {
        "kind": "cookie",
        "name": canary,
        "masked": "<masked>",
        "length": 20,
        "fingerprint": "sha256:0123456789ab",
    }
    details = {
        "cookies_before": [canary],
        "cookies_after": [crafted_marker],
        "http_session": canary,
        "request_headers": {"cookie": canary, "accept": "text/html"},
    }

    process_rendered = json.dumps(redact_sensitive_value(details))
    persisted_rendered = json.dumps(redact_run_event_details(details))

    assert canary not in process_rendered
    assert canary not in persisted_rendered
    assert redact_run_event_details(details) == {
        "cookies_before": "<redacted>",
        "cookies_after": "<redacted>",
        "http_session": "<redacted>",
        "request_headers": {"cookie": "<redacted>", "accept": "text/html"},
    }


def test_safe_marker_factory_canonicalizes_untrusted_name_kind_and_sensitive_header_key() -> None:
    canary = "AUDIT-MARKER-METADATA-CANARY-7f31"

    marker = safe_secret_marker(canary, "ordinary-secret-value", kind=canary)
    headers = safe_headers({f"X-Token-{canary}": "ordinary-header-value"})
    rendered = json.dumps(
        {
            "marker": marker,
            "headers": headers,
            "details": redact_run_event_details({"authorization": marker}),
            "structlog": redact_sensitive_value({"authorization": marker}),
        }
    )

    assert marker["kind"] == "secret"
    assert marker["name"].startswith("<redacted-name:sha256:")
    assert list(headers) == [next(iter(headers.values()))["name"]]
    assert canary not in rendered


def test_safe_marker_cannot_be_constructed_or_mutated_by_a_caller() -> None:
    marker = safe_secret_marker("access_token_web", "immutable-marker-value", kind="cookie")

    with pytest.raises(TypeError, match="must be created"):
        SafeSecretMarker(dict(marker))
    with pytest.raises(TypeError, match="immutable"):
        marker["name"] = "forged-name"
    with pytest.raises(TypeError, match="immutable"):
        marker.update({"kind": "forged-kind"})
    assert is_safe_secret_marker(marker)


@pytest.mark.parametrize(
    "mutation",
    [
        {"length": -1},
        {"length": 5, "masked": "four****alue"},
        {"kind": "caller-controlled-kind"},
        {"name": "caller-controlled-name"},
        {"domain": "metadata-must-not-survive"},
    ],
)
def test_persisted_marker_rejects_invalid_or_incoherent_metadata(mutation: dict[str, object]) -> None:
    marker = dict(safe_secret_marker("access_token_web", "valid-marker-value", kind="cookie"))
    marker.update(mutation)

    assert redact_persisted_run_event_details({"authorization": marker}) == {
        "authorization": "<redacted>"
    }
