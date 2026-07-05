#!/usr/bin/env python3
"""Check HTTP headers sent by curl_cffi using the configured browser profile.

Usage:
    python scripts/check_headers.py [--impersonate chrome136]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import OrderedDict
from pathlib import Path

from curl_cffi.requests import Session

from vinted_monitor.providers.browser_profiles import BROWSER_PROFILES


ECHO_URL = "https://httpbin.org/headers"
REFERENCE_FILE = Path(__file__).parent / "browser_reference.json"
HOP_BY_HOP_HEADERS = {"connection"}


def _profile_for_impersonate(impersonate: str):
    for profile in BROWSER_PROFILES:
        if profile.impersonate == impersonate:
            return profile
    raise ValueError(f"No browser profile configured for impersonate={impersonate!r}")


def check_headers(impersonate: str) -> bool:
    profile = _profile_for_impersonate(impersonate)
    print(f"Checking HTTP headers with profile='{profile.name}' impersonate='{impersonate}'...")
    print("=" * 60)

    headers = OrderedDict(profile.build_bootstrap_headers())
    with Session(impersonate=impersonate) as session:
        try:
            response = session.get(ECHO_URL, headers=dict(headers), timeout=15)
            data = response.json()
        except Exception as exc:
            print(f"  Error: {exc}")
            return False

    received = data.get("headers", {})
    print("\n  Headers received by server:")
    for key, value in received.items():
        print(f"    {key}: {str(value)[:80]}")

    expected = {key.lower(): value for key, value in headers.items()}
    received_lower = {key.lower(): value for key, value in received.items()}
    print("\n  Configured profile header check:")
    issues = 0
    for key, expected_value in expected.items():
        if key in HOP_BY_HOP_HEADERS:
            print(f"    SKIP: {key} (hop-by-hop header may not be echoed)")
            continue
        actual_value = received_lower.get(key)
        if actual_value is None:
            print(f"    MISSING: {key}")
            issues += 1
        elif actual_value != expected_value:
            print(f"    DIFF: {key}")
            print(f"      expected: {expected_value[:80]}")
            print(f"      actual:   {actual_value[:80]}")
            issues += 1
        else:
            print(f"    OK: {key}")

    if REFERENCE_FILE.exists():
        print(f"\n  Comparing with reference: {REFERENCE_FILE}")
        ref_data = json.loads(REFERENCE_FILE.read_text())
        ref_headers = {}
        for req in ref_data.get("request_headers_ordered", []):
            if "headers" in req:
                ref_headers = req["headers"]
                break

        if ref_headers:
            print("\n  Differences against browser reference:")
            all_keys = set(list(received.keys()) + list(ref_headers.keys()))
            has_diff = False
            for key in sorted(all_keys):
                in_curl = key in received
                in_ref = key in ref_headers
                if in_curl and in_ref and received[key] != ref_headers[key]:
                    print(f"    DIFF {key}:")
                    print(f"      curl_cffi: {str(received[key])[:60]}")
                    print(f"      chrome:    {str(ref_headers[key])[:60]}")
                    has_diff = True
                elif in_curl and not in_ref:
                    print(f"    EXTRA (curl_cffi only): {key}")
                    has_diff = True
                elif in_ref and not in_curl:
                    print(f"    MISSING (chrome only):  {key}")
                    has_diff = True
            if not has_diff:
                print("    OK: no differences found.")
    else:
        print(f"\n  No reference file found at {REFERENCE_FILE}")
        print("  Run scripts/inspect_vinted_session.py first to capture a reference.")

    print("\n" + "=" * 60)
    if issues:
        print(f"WARN: Header check completed with {issues} configured-profile differences.")
    else:
        print("OK: Header check completed.")
    return issues == 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check HTTP headers via curl_cffi")
    parser.add_argument("--impersonate", default="chrome136", help="Browser to impersonate")
    args = parser.parse_args()
    ok = check_headers(args.impersonate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
