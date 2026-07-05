#!/usr/bin/env python3
"""Verify Chrome 120 curl_cffi impersonation before live Vinted integration.

This script only calls public echo services. It does not call Vinted, Redis, or
the database.

Usage:
    python scripts/verify_impersonation.py
    python scripts/verify_impersonation.py --proxy-url "http://user-session-test:pass@host:port"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

from vinted_monitor.core.redaction import redact_sensitive_text  # noqa: E402
from vinted_monitor.providers.ephemeral_http import (  # noqa: E402
    CHROME120_ACCEPT_ENCODING,
    CHROME120_SEC_CH_UA,
    CHROME120_UA,
    EphemeralVintedHttpClient,
    chrome120_bootstrap_headers,
)

HTTPBIN_HEADERS_URL = "https://httpbin.org/headers"
TLS_BROWSERLEAKS_URL = "https://tls.browserleaks.com/json"
LEAK_TERMS = ("python", "curl", "cffi", "requests")
ALLOWED_BROWSER_HEADER_NAMES = {"upgrade-insecure-requests"}


class VerificationError(AssertionError):
    pass


def _lower_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _assert_equal(label: str, actual: str | None, expected: str) -> None:
    if actual != expected:
        raise VerificationError(f"{label} mismatch\nexpected: {expected}\nactual:   {actual}")


def _assert_no_leak_terms(label: str, value: Any) -> None:
    blob = json.dumps(value, sort_keys=True, default=str).lower()
    found = [term for term in LEAK_TERMS if term in blob]
    if found:
        raise VerificationError(f"{label} contains forbidden leak terms: {', '.join(found)}")


def _assert_no_header_leak_terms(headers: dict[str, Any]) -> None:
    leaks: list[str] = []
    for key, value in headers.items():
        key_text = str(key).lower()
        value_text = str(value).lower()
        if key_text not in ALLOWED_BROWSER_HEADER_NAMES:
            leaks.extend(f"{key}:{term}" for term in LEAK_TERMS if term in key_text)
        leaks.extend(f"{key}:{term}" for term in LEAK_TERMS if term in value_text)
    if leaks:
        raise VerificationError(f"httpbin headers contain forbidden leak terms: {', '.join(leaks)}")


def _validate_httpbin(payload: dict[str, Any]) -> dict[str, str]:
    headers = payload.get("headers")
    if not isinstance(headers, dict):
        raise VerificationError("httpbin response does not contain a headers object")

    lowered = _lower_headers(headers)
    _assert_equal("User-Agent", lowered.get("user-agent"), CHROME120_UA)
    _assert_equal("sec-ch-ua", lowered.get("sec-ch-ua"), CHROME120_SEC_CH_UA)
    _assert_equal("Accept-Encoding", lowered.get("accept-encoding"), CHROME120_ACCEPT_ENCODING)
    _assert_no_header_leak_terms(headers)
    return lowered


def _validate_tls(payload: dict[str, Any]) -> None:
    tls_ua = str(payload.get("user_agent") or payload.get("User-Agent") or "")
    _assert_equal("TLS user_agent", tls_ua, CHROME120_UA)

    for key in ("ja3_hash", "ja3_text", "ja4"):
        if not payload.get(key):
            raise VerificationError(f"TLS payload is missing {key}")

    ja4 = str(payload["ja4"])
    if not ja4.startswith("t13"):
        raise VerificationError(f"Expected a TLS 1.3-like JA4 prefix, got {ja4}")
    if "h2" not in ja4:
        raise VerificationError(f"Expected an HTTP/2-like JA4 marker, got {ja4}")

    warning_fields = {
        key: value
        for key, value in payload.items()
        if any(marker in str(key).lower() for marker in ("warn", "error", "script")) and value
    }
    if warning_fields:
        raise VerificationError(f"TLS service returned warning/error/script fields: {warning_fields}")

    _assert_no_leak_terms("tls payload", payload)


def run_verification(*, proxy_url: str | None, timeout_ms: int, headers_url: str, tls_url: str) -> None:
    with EphemeralVintedHttpClient(
        base_url="https://www.vinted.es",
        proxy_url=proxy_url,
        timeout_ms=timeout_ms,
    ) as client:
        headers = dict(chrome120_bootstrap_headers())

        httpbin_payload = client.get_json(headers_url, headers=headers)
        echoed_headers = _validate_httpbin(httpbin_payload)
        print("OK httpbin headers")
        print(f"   user-agent: {echoed_headers.get('user-agent')}")
        print(f"   sec-ch-ua:  {echoed_headers.get('sec-ch-ua')}")

        tls_payload = client.get_json(tls_url, headers=headers)
        _validate_tls(tls_payload)
        print("OK tls.browserleaks fingerprint")
        print(f"   ja3_hash: {tls_payload.get('ja3_hash')}")
        print(f"   ja4:      {tls_payload.get('ja4')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Chrome 120 curl_cffi impersonation before Vinted integration")
    parser.add_argument("--proxy-url", default=os.getenv("VERIFY_PROXY_URL"), help="Optional sticky proxy URL")
    parser.add_argument("--timeout-ms", type=int, default=15000)
    parser.add_argument("--headers-url", default=HTTPBIN_HEADERS_URL)
    parser.add_argument("--tls-url", default=TLS_BROWSERLEAKS_URL)
    args = parser.parse_args()

    try:
        run_verification(
            proxy_url=args.proxy_url,
            timeout_ms=args.timeout_ms,
            headers_url=args.headers_url,
            tls_url=args.tls_url,
        )
    except VerificationError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR {redact_sensitive_text(str(exc))}", file=sys.stderr)
        return 1

    print("PASS Chrome 120 impersonation preflight passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
