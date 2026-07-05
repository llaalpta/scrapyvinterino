#!/usr/bin/env python3
"""Smoke test: bootstrap + catalog request against Vinted with DataDome detection.

Usage:
    python scripts/check_datadome.py --url "https://www.vinted.es/catalog?search_text=nike" [--proxy socks5://...] [--impersonate chrome136]
"""
from __future__ import annotations

import argparse
import sys
import time

from curl_cffi.requests import Session

from vinted_monitor.providers.browser_profiles import BROWSER_PROFILES, select_random_profile
from vinted_monitor.providers.datadome import has_datadome_cookie, human_delay, is_datadome_challenge


def smoke_test(url: str, proxy: str | None, impersonate: str | None) -> bool:
    profile = select_random_profile()
    if impersonate:
        # Override impersonate if specified
        for p in BROWSER_PROFILES:
            if p.impersonate == impersonate:
                profile = p
                break

    print(f"Profile:     {profile.name}")
    print(f"Impersonate: {profile.impersonate}")
    print(f"User-Agent:  {profile.user_agent[:70]}...")
    print(f"Proxy:       {proxy or 'direct'}")
    print(f"URL:         {url}")
    print("=" * 60)

    proxy_dict = {"https": proxy, "http": proxy} if proxy else None
    with Session(impersonate=profile.impersonate, proxies=proxy_dict) as session:
        # Step 1: Bootstrap
        print("\n[1/3] Bootstrap (HTML page)...")
        headers = dict(profile.build_bootstrap_headers())
        started_at = time.perf_counter()
        try:
            response = session.get(url, headers=headers, timeout=15)
        except Exception as exc:
            print(f"  ❌ Bootstrap failed: {exc}")
            return False

        elapsed = round((time.perf_counter() - started_at) * 1000)
        print(f"  Status:  {response.status_code}")
        print(f"  Latency: {elapsed}ms")

        # Check DataDome
        if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
            print("  ❌ DataDome CHALLENGE detected!")
            return False

        cookies = dict(session.cookies) if session.cookies else {}
        dd_present = has_datadome_cookie(cookies)
        print(f"  DataDome cookie: {'YES ✅' if dd_present else 'NO ⚠️'}")
        print(f"  Cookies: {list(cookies.keys())}")

        # Step 2: Human delay
        print("\n[2/3] Human delay...")
        delay = human_delay(1.2, 3.8)
        print(f"  Delay: {delay:.2f}s")

        # Step 3: Catalog API
        print("\n[3/3] Catalog API (JSON)...")
        api_url = url.split("/catalog")[0] + "/api/v2/catalog/items"
        api_headers = dict(profile.build_api_headers(referer=url))
        api_params = {"page": 1, "per_page": 5, "order": "newest_first", "search_text": "nike"}

        started_at = time.perf_counter()
        try:
            response = session.get(api_url, params=api_params, headers=api_headers, timeout=15)
        except Exception as exc:
            print(f"  ❌ Catalog API failed: {exc}")
            return False

        elapsed = round((time.perf_counter() - started_at) * 1000)
        print(f"  Status:  {response.status_code}")
        print(f"  Latency: {elapsed}ms")
        print(f"  Content-Type: {response.headers.get('content-type', 'unknown')}")

        if is_datadome_challenge(response.status_code, dict(response.headers), response.text[:3000]):
            print("  ❌ DataDome CHALLENGE detected on API request!")
            return False

        try:
            data = response.json()
            items = data.get("items", [])
            print(f"  Items returned: {len(items)}")
            if items:
                first = items[0]
                print(f"  First item: {first.get('title', 'N/A')} — {first.get('price', {}).get('amount', '?')} {first.get('price', {}).get('currency_code', '')}")
        except Exception:
            print(f"  ⚠️ Could not parse JSON (Content-Type: {response.headers.get('content-type')})")
            print(f"  Body preview: {response.text[:200]}")

    print("\n" + "=" * 60)
    print("✅  Smoke test PASSED — bootstrap + catalog flow works!")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test: Vinted bootstrap + catalog with DataDome detection")
    parser.add_argument("--url", required=True, help="Vinted catalog URL")
    parser.add_argument("--proxy", default=None, help="Proxy URL (e.g. socks5://user:pass@host:port)")
    parser.add_argument("--impersonate", default=None, help="Override impersonate value")
    args = parser.parse_args()
    ok = smoke_test(args.url, args.proxy, args.impersonate)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
