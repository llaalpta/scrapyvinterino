#!/usr/bin/env python3
"""Verify JA3/TLS fingerprint produced by curl_cffi against public echo services.

Usage:
    python scripts/check_ja3.py [--impersonate chrome136]
"""
from __future__ import annotations

import argparse
import json
import sys

from curl_cffi.requests import Session


ECHO_SERVICES = [
    "https://tls.browserleaks.com/json",
    "https://tls.peet.ws/api/all",
]


def check_fingerprint(impersonate: str) -> bool:
    """Check JA3 fingerprint against echo services and print results."""
    print(f"Checking TLS fingerprint with impersonate='{impersonate}'...")
    print("=" * 60)
    success = False

    with Session(impersonate=impersonate) as session:
        for url in ECHO_SERVICES:
            print(f"\n  Service: {url}")
            try:
                response = session.get(url, timeout=15)
                if response.status_code != 200:
                    print(f"  Status: {response.status_code} (skipping)")
                    continue
                data = response.json()
                ja3 = data.get("ja3_hash") or data.get("ja3") or data.get("ja3_text", "unknown")
                ua = data.get("user_agent") or data.get("User-Agent") or "unknown"
                http_ver = data.get("http_version") or data.get("http") or "unknown"
                tls_ver = data.get("tls_version") or data.get("tls") or data.get("version", "unknown")
                print(f"  JA3 hash:     {ja3}")
                print(f"  User-Agent:   {ua[:80]}...")
                print(f"  HTTP version: {http_ver}")
                print(f"  TLS version:  {tls_ver}")
                success = True
            except Exception as exc:
                print(f"  Error: {exc}")

    print("\n" + "=" * 60)
    if success:
        print("✅  Fingerprint check completed. Compare JA3 hash with real Chrome.")
    else:
        print("❌  All echo services failed.")
    return success


def main() -> None:
    parser = argparse.ArgumentParser(description="Check TLS/JA3 fingerprint via curl_cffi")
    parser.add_argument("--impersonate", default="chrome136", help="Browser to impersonate (default: chrome136)")
    args = parser.parse_args()
    ok = check_fingerprint(args.impersonate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
