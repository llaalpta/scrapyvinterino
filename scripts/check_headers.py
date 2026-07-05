#!/usr/bin/env python3
"""Check HTTP headers sent by curl_cffi and compare with real Chrome reference.

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


ECHO_URL = "https://httpbin.org/headers"
REFERENCE_FILE = Path(__file__).parent / "browser_reference.json"


def check_headers(impersonate: str) -> bool:
    print(f"Checking HTTP headers with impersonate='{impersonate}'...")
    print("=" * 60)

    headers = OrderedDict([
        ("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"),
        ("Accept-Language", "es-ES,es;q=0.9,en;q=0.8"),
        ("Accept-Encoding", "gzip, deflate, br, zstd"),
        ("Cache-Control", "max-age=0"),
        ("Connection", "keep-alive"),
    ])

    with Session(impersonate=impersonate) as session:
        try:
            response = session.get(ECHO_URL, headers=dict(headers), timeout=15)
            data = response.json()
        except Exception as exc:
            print(f"  Error: {exc}")
            return False

    received = data.get("headers", {})
    print("\n  Headers received by server (in order sent):")
    for key, value in received.items():
        print(f"    {key}: {value[:80]}")

    # Compare with reference if available
    if REFERENCE_FILE.exists():
        print(f"\n  Comparing with reference: {REFERENCE_FILE}")
        ref_data = json.loads(REFERENCE_FILE.read_text())
        ref_headers = {}
        for req in ref_data.get("request_headers_ordered", []):
            if "headers" in req:
                ref_headers = req["headers"]
                break

        if ref_headers:
            print("\n  Differences:")
            all_keys = set(list(received.keys()) + list(ref_headers.keys()))
            has_diff = False
            for key in sorted(all_keys):
                in_curl = key in received
                in_ref = key in ref_headers
                if in_curl and in_ref:
                    if received[key] != ref_headers[key]:
                        print(f"    DIFF {key}:")
                        print(f"      curl_cffi: {received[key][:60]}")
                        print(f"      chrome:    {ref_headers[key][:60]}")
                        has_diff = True
                elif in_curl and not in_ref:
                    print(f"    EXTRA (curl_cffi only): {key}")
                    has_diff = True
                elif in_ref and not in_curl:
                    print(f"    MISSING (chrome only):  {key}")
                    has_diff = True
            if not has_diff:
                print("    ✅  No differences found!")
    else:
        print(f"\n  No reference file found at {REFERENCE_FILE}")
        print("  Run scripts/inspect_vinted_session.py first to capture a reference.")

    print("\n" + "=" * 60)
    print("✅  Header check completed.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Check HTTP headers via curl_cffi")
    parser.add_argument("--impersonate", default="chrome136", help="Browser to impersonate")
    args = parser.parse_args()
    ok = check_headers(args.impersonate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
