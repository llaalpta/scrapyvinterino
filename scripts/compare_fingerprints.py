#!/usr/bin/env python3
"""Compare curl_cffi fingerprint with real Chrome reference.

Reads scripts/browser_reference.json (captured by inspect_vinted_session.py)
and compares field by field with curl_cffi output.

Usage:
    python scripts/compare_fingerprints.py [--impersonate chrome136]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from curl_cffi.requests import Session


REFERENCE_FILE = Path(__file__).parent / "browser_reference.json"
ECHO_URL = "https://httpbin.org/headers"


def compare_fingerprints(impersonate: str) -> bool:
    if not REFERENCE_FILE.exists():
        print(f"❌  No reference file found at {REFERENCE_FILE}")
        print("   Run scripts/inspect_vinted_session.py first.")
        return False

    reference = json.loads(REFERENCE_FILE.read_text())
    ref_ua = reference.get("navigator", {}).get("userAgent", "")

    print(f"Comparing impersonate='{impersonate}' with Chrome reference...")
    print("=" * 60)

    # Get curl_cffi headers
    with Session(impersonate=impersonate) as session:
        try:
            response = session.get(ECHO_URL, timeout=15)
            curl_headers = response.json().get("headers", {})
        except Exception as exc:
            print(f"  ❌ Failed to get curl_cffi headers: {exc}")
            return False

    # Compare User-Agent
    curl_ua = curl_headers.get("User-Agent", "")
    print(f"\n  User-Agent comparison:")
    print(f"    curl_cffi: {curl_ua[:70]}...")
    print(f"    Chrome:    {ref_ua[:70]}...")
    ua_match = curl_ua.split("Chrome/")[1][:3] == ref_ua.split("Chrome/")[1][:3] if "Chrome/" in curl_ua and "Chrome/" in ref_ua else False
    print(f"    Match:     {'✅ Major version match' if ua_match else '⚠️  Version mismatch'}")

    # Compare header presence
    ref_headers = {}
    for req in reference.get("request_headers_ordered", []):
        if "headers" in req:
            ref_headers = req["headers"]
            break

    if ref_headers:
        print(f"\n  Header comparison:")
        critical_headers = [
            "accept", "accept-encoding", "accept-language",
            "sec-ch-ua", "sec-ch-ua-mobile", "sec-ch-ua-platform",
            "sec-fetch-dest", "sec-fetch-mode", "sec-fetch-site",
            "upgrade-insecure-requests",
        ]

        issues = 0
        for header in critical_headers:
            in_curl = any(k.lower() == header for k in curl_headers)
            in_ref = any(k.lower() == header for k in ref_headers)
            status = "✅" if in_curl else ("⚠️  MISSING" if in_ref else "—")
            print(f"    {header:35s} {status}")
            if in_ref and not in_curl:
                issues += 1

        print(f"\n  Critical headers missing: {issues}")
    else:
        print("  ⚠️  No reference headers found.")

    # DataDome cookie presence
    dd = reference.get("datadome_cookies", [])
    print(f"\n  DataDome cookie in reference: {'YES' if dd else 'NO'}")
    if dd:
        print(f"    Value length: {dd[0].get('value_length', '?')}")
        print(f"    Domain: {dd[0].get('domain', '?')}")

    print("\n" + "=" * 60)
    print("✅  Comparison completed.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare curl_cffi with Chrome reference")
    parser.add_argument("--impersonate", default="chrome136", help="Browser to impersonate")
    args = parser.parse_args()
    ok = compare_fingerprints(args.impersonate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
