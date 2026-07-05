from __future__ import annotations

import random
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class BrowserProfile:
    """Coherent browser identity for a single scraping session.

    Every field within a profile must be internally consistent:
    ``impersonate`` drives the TLS/JA3 and HTTP/2 fingerprint,
    ``user_agent`` + ``sec_ch_ua*`` must match the same browser version,
    and header dicts must use the exact order a real browser sends them.
    """

    name: str
    impersonate: str
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    accept_language: str
    bootstrap_headers: dict[str, str] = field(default_factory=dict)
    api_headers: dict[str, str] = field(default_factory=dict)

    def build_bootstrap_headers(self, referer: str | None = None) -> OrderedDict[str, str]:
        """Return ordered headers for the HTML bootstrap request."""
        headers = OrderedDict(self.bootstrap_headers)
        if referer:
            headers["Referer"] = referer
        return headers

    def build_api_headers(self, referer: str) -> OrderedDict[str, str]:
        """Return ordered headers for the JSON catalog API request."""
        headers = OrderedDict(self.api_headers)
        headers["Referer"] = referer
        return headers


def _chrome_bootstrap_headers(
    user_agent: str,
    sec_ch_ua: str,
    sec_ch_ua_platform: str,
    accept_language: str,
) -> dict[str, str]:
    """Chrome-ordered headers for a top-level navigation (document) request."""
    return dict(
        OrderedDict([
            ("sec-ch-ua", sec_ch_ua),
            ("sec-ch-ua-mobile", "?0"),
            ("sec-ch-ua-platform", sec_ch_ua_platform),
            ("Upgrade-Insecure-Requests", "1"),
            ("User-Agent", user_agent),
            (
                "Accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7",
            ),
            ("Sec-Fetch-Site", "none"),
            ("Sec-Fetch-Mode", "navigate"),
            ("Sec-Fetch-User", "?1"),
            ("Sec-Fetch-Dest", "document"),
            ("Accept-Encoding", "gzip, deflate, br, zstd"),
            ("Accept-Language", accept_language),
            ("Cache-Control", "max-age=0"),
            ("Connection", "keep-alive"),
        ])
    )


def _chrome_api_headers(
    user_agent: str,
    sec_ch_ua: str,
    sec_ch_ua_platform: str,
    accept_language: str,
) -> dict[str, str]:
    """Chrome-ordered headers for an XHR/fetch JSON API request."""
    return dict(
        OrderedDict([
            ("sec-ch-ua", sec_ch_ua),
            ("Accept", "application/json, text/plain, */*"),
            ("sec-ch-ua-mobile", "?0"),
            ("User-Agent", user_agent),
            ("sec-ch-ua-platform", sec_ch_ua_platform),
            ("Sec-Fetch-Site", "same-origin"),
            ("Sec-Fetch-Mode", "cors"),
            ("Sec-Fetch-Dest", "empty"),
            ("Accept-Encoding", "gzip, deflate, br, zstd"),
            ("Accept-Language", accept_language),
        ])
    )


# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------
# Keep these updated when curl_cffi adds new impersonate targets.
# Run ``curl-cffi list`` to see available fingerprints.
# Run ``python scripts/inspect_vinted_session.py`` to capture real Chrome
# headers as reference.

_LANG_ES = "es-ES,es;q=0.9,en;q=0.8"

BROWSER_PROFILES: list[BrowserProfile] = [
    BrowserProfile(
        name="chrome_136_win10",
        impersonate="chrome136",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"Windows"',
        accept_language=_LANG_ES,
        bootstrap_headers=_chrome_bootstrap_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
        ),
        api_headers=_chrome_api_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
        ),
    ),
    BrowserProfile(
        name="chrome_142_win10",
        impersonate="chrome142",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/142.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="142", "Google Chrome";v="142", "Not.A/Brand";v="24"',
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"Windows"',
        accept_language=_LANG_ES,
        bootstrap_headers=_chrome_bootstrap_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Chromium";v="142", "Google Chrome";v="142", "Not.A/Brand";v="24"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
        ),
        api_headers=_chrome_api_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/142.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Chromium";v="142", "Google Chrome";v="142", "Not.A/Brand";v="24"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
        ),
    ),
    BrowserProfile(
        name="chrome_146_win10",
        impersonate="chrome146",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        sec_ch_ua='"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="24"',
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"Windows"',
        accept_language=_LANG_ES,
        bootstrap_headers=_chrome_bootstrap_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="24"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
        ),
        api_headers=_chrome_api_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="24"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
        ),
    ),
]


def select_random_profile(rng: random.Random | None = None) -> BrowserProfile:
    """Select a random browser profile for a scraping session."""
    generator = rng or random.Random()
    return generator.choice(BROWSER_PROFILES)


def get_profile_by_name(name: str) -> BrowserProfile | None:
    """Look up a profile by its unique name."""
    for profile in BROWSER_PROFILES:
        return profile if profile.name == name else None
    return None


# ---------------------------------------------------------------------------
# Navigation flow selection
# ---------------------------------------------------------------------------

NAVIGATION_FLOWS: list[dict[str, Any]] = [
    {
        "name": "google_referral",
        "weight": 40,
        "bootstrap_referer": "https://www.google.com/",
        "needs_home_visit": False,
    },
    {
        "name": "home_navigation",
        "weight": 30,
        "bootstrap_referer": None,
        "needs_home_visit": True,
    },
    {
        "name": "internal_referral",
        "weight": 30,
        "bootstrap_referer": None,
        "needs_home_visit": False,
    },
]


@dataclass(frozen=True)
class NavigationFlow:
    name: str
    bootstrap_referer: str | None
    needs_home_visit: bool


def select_navigation_flow(rng: random.Random | None = None) -> NavigationFlow:
    """Select a weighted random navigation flow for realistic browsing."""
    generator = rng or random.Random()
    names = []
    weights = []
    for flow in NAVIGATION_FLOWS:
        names.append(flow["name"])
        weights.append(flow["weight"])
    chosen_name = generator.choices(names, weights=weights, k=1)[0]
    chosen = next(f for f in NAVIGATION_FLOWS if f["name"] == chosen_name)
    return NavigationFlow(
        name=chosen["name"],
        bootstrap_referer=chosen["bootstrap_referer"],
        needs_home_visit=chosen["needs_home_visit"],
    )
