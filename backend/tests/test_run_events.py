from vinted_monitor.services.run_events import redact_run_event_details


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
