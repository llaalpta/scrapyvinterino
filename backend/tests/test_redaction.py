from vinted_monitor.core.redaction import redact_sensitive_text


def test_redact_sensitive_text_removes_url_userinfo() -> None:
    message = "proxy failed at http://proxy-user:proxy-pass@residential.example:8000/path"

    redacted = redact_sensitive_text(message)

    assert "proxy-user" not in redacted
    assert "proxy-pass" not in redacted
    assert "http://<redacted>:<redacted>@residential.example:8000/path" in redacted
