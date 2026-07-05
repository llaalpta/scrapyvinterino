from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def load_verify_impersonation_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "verify_impersonation.py"
    spec = importlib.util.spec_from_file_location("verify_impersonation", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


verify_impersonation = load_verify_impersonation_module()


def test_httpbin_validator_allows_standard_browser_requests_header_name() -> None:
    headers = {
        "User-Agent": verify_impersonation.CHROME120_UA,
        "Sec-Ch-Ua": verify_impersonation.CHROME120_SEC_CH_UA,
        "Accept-Encoding": verify_impersonation.CHROME120_ACCEPT_ENCODING,
        "Upgrade-Insecure-Requests": "1",
    }

    lowered = verify_impersonation._validate_httpbin({"headers": headers})

    assert lowered["user-agent"] == verify_impersonation.CHROME120_UA


def test_httpbin_validator_rejects_leaky_header_values() -> None:
    headers = {
        "User-Agent": verify_impersonation.CHROME120_UA,
        "Sec-Ch-Ua": verify_impersonation.CHROME120_SEC_CH_UA,
        "Accept-Encoding": verify_impersonation.CHROME120_ACCEPT_ENCODING,
        "X-Debug": "python-requests",
    }

    with pytest.raises(verify_impersonation.VerificationError, match="python"):
        verify_impersonation._validate_httpbin({"headers": headers})


def test_httpbin_validator_rejects_wrong_accept_encoding() -> None:
    headers = {
        "User-Agent": verify_impersonation.CHROME120_UA,
        "Sec-Ch-Ua": verify_impersonation.CHROME120_SEC_CH_UA,
        "Accept-Encoding": "gzip, deflate, br, zstd",
    }

    with pytest.raises(verify_impersonation.VerificationError, match="Accept-Encoding"):
        verify_impersonation._validate_httpbin({"headers": headers})


def test_tls_validator_accepts_browser_like_tls_payload() -> None:
    verify_impersonation._validate_tls(
        {
            "user_agent": verify_impersonation.CHROME120_UA,
            "ja3_hash": "abc",
            "ja3_text": "771,...",
            "ja4": "t13d1516h2_abc_def",
        }
    )


def test_tls_validator_rejects_warning_fields() -> None:
    with pytest.raises(verify_impersonation.VerificationError, match="warning"):
        verify_impersonation._validate_tls(
            {
                "user_agent": verify_impersonation.CHROME120_UA,
                "ja3_hash": "abc",
                "ja3_text": "771,...",
                "ja4": "t13d1516h2_abc_def",
                "warning": "script detected",
            }
        )


def test_redaction_hides_proxy_credentials_in_errors() -> None:
    raw = "failed for http://user-session-test:secret@proxy.example:8000"

    redacted = verify_impersonation.redact_sensitive_text(raw)

    assert "secret" not in redacted
    assert "<redacted>:<redacted>@proxy.example:8000" in redacted
