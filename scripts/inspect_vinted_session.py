#!/usr/bin/env python3
"""Capture real Chrome browser fingerprint via Playwright + CDP for reference.

Navigates to Vinted with a real Chrome browser and captures:
- navigator properties (userAgent, platform, languages)
- Request headers in exact order (via CDP Network.requestWillBeSent)
- DataDome cookie presence, value length, TTL
- Timing of first request

Exports to scripts/browser_reference.json for comparison with curl_cffi.

Usage:
    python scripts/inspect_vinted_session.py --url "https://www.vinted.es/"

Prerequisites:
    pip install playwright
    playwright install chromium
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright is not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)


OUTPUT_FILE = Path(__file__).parent / "browser_reference.json"


def inspect_session(url: str) -> bool:
    print(f"Launching Chrome to capture fingerprint...")
    print(f"URL: {url}")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context(locale="es-ES")
        page = context.new_page()

        # CDP session for capturing headers in exact order
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")

        captured_requests: list[dict] = []

        def on_request(params):
            req = params.get("request", {})
            captured_requests.append({
                "url": req.get("url", ""),
                "method": req.get("method", ""),
                "headers": req.get("headers", {}),
            })

        cdp.on("Network.requestWillBeSent", on_request)

        print("\n  Navigating...")
        page.goto(url, wait_until="networkidle", timeout=30000)

        # Capture navigator properties
        nav = page.evaluate("""() => ({
            userAgent: navigator.userAgent,
            platform: navigator.platform,
            languages: navigator.languages,
            hardwareConcurrency: navigator.hardwareConcurrency,
            deviceMemory: navigator.deviceMemory,
            maxTouchPoints: navigator.maxTouchPoints,
        })""")

        # Capture cookies
        cookies = context.cookies()
        datadome_cookies = [c for c in cookies if c["name"] == "datadome"]

        # Build reference
        reference = {
            "navigator": nav,
            "datadome_cookies": [
                {
                    "name": c["name"],
                    "value_length": len(c.get("value", "")),
                    "domain": c.get("domain"),
                    "path": c.get("path"),
                    "expires": c.get("expires"),
                    "httpOnly": c.get("httpOnly"),
                    "secure": c.get("secure"),
                }
                for c in datadome_cookies
            ],
            "request_headers_ordered": captured_requests[:5],
            "total_requests_captured": len(captured_requests),
        }

        OUTPUT_FILE.write_text(json.dumps(reference, indent=2, default=str))

        print(f"\n  User-Agent:     {nav['userAgent'][:80]}...")
        print(f"  Platform:       {nav['platform']}")
        print(f"  Languages:      {nav['languages']}")
        print(f"  DataDome:       {'YES' if datadome_cookies else 'NO'}")
        print(f"  Requests:       {len(captured_requests)}")
        print(f"\n  Reference saved to: {OUTPUT_FILE}")

        # Print header order from first request
        if captured_requests:
            first = captured_requests[0]
            print(f"\n  First request headers ({first['url'][:60]}):")
            for key, value in first["headers"].items():
                print(f"    {key}: {str(value)[:60]}")

        browser.close()

    print("\n" + "=" * 60)
    print("✅  Reference captured. Run scripts/compare_fingerprints.py to compare.")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Chrome fingerprint via Playwright")
    parser.add_argument("--url", default="https://www.vinted.es/", help="URL to visit")
    args = parser.parse_args()
    ok = inspect_session(args.url)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
