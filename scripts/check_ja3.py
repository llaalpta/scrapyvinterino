#!/usr/bin/env python3
"""Verify JA3/TLS fingerprint produced by curl_cffi against public echo services.

Usage:
    python scripts/check_ja3.py [--impersonate chrome120]
"""
from __future__ import annotations

import argparse
import sys

from curl_cffi.requests import Session

from vinted_monitor.providers.browser_profiles import BROWSER_PROFILES

ECHO_SERVICES = [
    "https://tls.browserleaks.com/json",
    "https://tls.peet.ws/api/all",
]


def _profile_for_impersonate(impersonate: str):
    for profile in BROWSER_PROFILES:
        if profile.impersonate == impersonate:
            return profile
    raise ValueError(f"No browser profile configured for impersonate={impersonate!r}")


def _tls_version_label(value) -> str:
    if isinstance(value, dict):
        negotiated = value.get("tls_version_negotiated")
        if negotiated:
            return str(negotiated)
        ja4 = value.get("ja4")
        if ja4:
            return str(ja4)
        return "structured TLS data"
    return str(value)


def check_fingerprint(impersonate: str) -> bool:
    """Check JA3 fingerprint against echo services and print results."""
    profile = _profile_for_impersonate(impersonate)
    headers = dict(profile.build_bootstrap_headers())
    print(f"Checking TLS fingerprint with profile='{profile.name}' impersonate='{impersonate}'...")
    print("=" * 60)
    success = False
    warnings = 0

    with Session(impersonate=impersonate) as session:
        for url in ECHO_SERVICES:
            print(f"\n  Service: {url}")
            try:
                response = session.get(url, headers=headers, timeout=15)
                if response.status_code != 200:
                    print(f"  WARN: Status {response.status_code} (skipping)")
                    warnings += 1
                    continue
                data = response.json()
                ja3 = data.get("ja3_hash") or data.get("ja3") or data.get("ja3_text", "unknown")
                ua = data.get("user_agent") or data.get("User-Agent") or "unknown"
                http_ver = data.get("http_version") or data.get("http") or "unknown"
                tls_ver = data.get("tls_version") or data.get("tls") or data.get("version", "unknown")
                print(f"  JA3 hash:     {ja3}")
                print(f"  User-Agent:   {ua[:80]}...")
                print(f"  HTTP version: {http_ver}")
                print(f"  TLS version:  {_tls_version_label(tls_ver)}")
                success = True
            except Exception as exc:
                print(f"  WARN: {exc}")
                warnings += 1

    print("\n" + "=" * 60)
    if success and warnings:
        print("WARN: Fingerprint check completed with partial echo-service failures.")
        print("OK: At least one echo service responded. Compare JA3 hash with real Chrome.")
    elif success:
        print("OK: Fingerprint check completed. Compare JA3 hash with real Chrome.")
    else:
        print("ERROR: All echo services failed.")
    return success


def main() -> None:
    parser = argparse.ArgumentParser(description="Check TLS/JA3 fingerprint via curl_cffi")
    parser.add_argument("--impersonate", default="chrome120", help="Browser to impersonate (default: chrome120)")
    args = parser.parse_args()
    ok = check_fingerprint(args.impersonate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
