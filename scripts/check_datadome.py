#!/usr/bin/env python3
"""Smoke test: bootstrap + catalog request against Vinted with DataDome detection.

Usage:
    python scripts/check_datadome.py --url "https://www.vinted.es/catalog?search_text=nike" [--proxy socks5://...] [--impersonate chrome120]
"""
from __future__ import annotations

import argparse
import sys
import time
from urllib.parse import urljoin

from curl_cffi.requests import Session

from vinted_monitor.providers.browser_profiles import BROWSER_PROFILES, profile_for_impersonate
from vinted_monitor.providers.datadome import has_datadome_cookie, human_delay, is_datadome_challenge
from vinted_monitor.providers.vinted_catalog import build_catalog_api_params


def _profile_for_impersonate(impersonate: str | None):
    if not impersonate:
        return profile_for_impersonate("chrome120")
    for profile in BROWSER_PROFILES:
        if profile.impersonate == impersonate:
            return profile
    raise ValueError(f"No browser profile configured for impersonate={impersonate!r}")


def smoke_test(url: str, proxy: str | None, impersonate: str | None) -> bool:
    profile = _profile_for_impersonate(impersonate)

    print(f"Profile:     {profile.name}")
    print(f"Impersonate: {profile.impersonate}")
    print(f"User-Agent:  {profile.user_agent[:70]}...")
    print(f"Proxy:       {proxy or 'direct'}")
    print(f"URL:         {url}")
    print("=" * 60)

    proxy_dict = {"https": proxy, "http": proxy} if proxy else None
    with Session(impersonate=profile.impersonate, proxies=proxy_dict) as session:
        print("\n[1/3] Bootstrap (HTML page)...")
        headers = dict(profile.build_bootstrap_headers())
        started_at = time.perf_counter()
        try:
            response = session.get(url, headers=headers, timeout=15)
        except Exception as exc:
            print(f"  ERROR: Bootstrap failed: {exc}")
            return False

        elapsed = round((time.perf_counter() - started_at) * 1000)
        print(f"  Status:  {response.status_code}")
        print(f"  Latency: {elapsed}ms")

        if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
            print("  ERROR: DataDome challenge detected during bootstrap.")
            return False

        cookies = dict(session.cookies) if session.cookies else {}
        dd_present = has_datadome_cookie(cookies)
        print(f"  DataDome cookie: {'YES' if dd_present else 'NO'}")
        print(f"  Cookies: {list(cookies.keys())}")

        print("\n[2/3] Human delay...")
        delay = human_delay(1.2, 3.8)
        print(f"  Delay: {delay:.2f}s")

        print("\n[3/3] Catalog API (JSON)...")
        api_url = urljoin(url, "/api/v2/catalog/items")
        api_headers = dict(profile.build_api_headers(referer=url))
        api_params = build_catalog_api_params(url, page=1, per_page=5)
        print(f"  Params:  {api_params}")

        started_at = time.perf_counter()
        try:
            response = session.get(api_url, params=api_params, headers=api_headers, timeout=15)
        except Exception as exc:
            print(f"  ERROR: Catalog API failed: {exc}")
            return False

        elapsed = round((time.perf_counter() - started_at) * 1000)
        print(f"  Status:  {response.status_code}")
        print(f"  Latency: {elapsed}ms")
        print(f"  Content-Type: {response.headers.get('content-type', 'unknown')}")

        if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
            print("  ERROR: DataDome challenge detected on API request.")
            return False

        try:
            data = response.json()
            items = data.get("items", [])
            print(f"  Items returned: {len(items)}")
            if items:
                first = items[0]
                price = first.get("price", {})
                print(
                    "  First item: "
                    f"{first.get('title', 'N/A')} - {price.get('amount', '?')} {price.get('currency_code', '')}"
                )
        except Exception:
            print(f"  WARN: Could not parse JSON (Content-Type: {response.headers.get('content-type')})")
            print(f"  Body preview: {response.text[:200]}")
            return False

    print("\n" + "=" * 60)
    print("OK: Smoke test passed; bootstrap + catalog flow works.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test: Vinted bootstrap + catalog with DataDome detection")
    parser.add_argument("--url", required=True, help="Vinted catalog URL")
    parser.add_argument("--proxy", default=None, help="Proxy URL (e.g. socks5://user:pass@host:port)")
    parser.add_argument("--impersonate", default=None, help="Override impersonate value (default: chrome120)")
    args = parser.parse_args()
    ok = smoke_test(args.url, args.proxy, args.impersonate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
