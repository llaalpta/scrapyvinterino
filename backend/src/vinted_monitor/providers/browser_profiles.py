from __future__ import annotations

import random
from collections import OrderedDict
from dataclasses import dataclass, field


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
    accept_encoding: str = "gzip, deflate, br, zstd",
    cache_control: str | None = "max-age=0",
    pragma: str | None = None,
    priority: str | None = None,
) -> dict[str, str]:
    """Chrome-ordered headers for a top-level navigation (document) request."""
    headers = OrderedDict([
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
        ("Accept-Encoding", accept_encoding),
        ("Accept-Language", accept_language),
    ])
    if cache_control:
        headers["Cache-Control"] = cache_control
    if pragma:
        headers["Pragma"] = pragma
    if priority:
        headers["Priority"] = priority
    return dict(headers)


def _chrome_api_headers(
    user_agent: str,
    sec_ch_ua: str,
    sec_ch_ua_platform: str,
    accept_language: str,
    accept_encoding: str = "gzip, deflate, br, zstd",
    accept: str = "application/json, text/plain, */*",
    locale: str | None = None,
    priority: str | None = None,
) -> dict[str, str]:
    """Chrome-ordered headers for an XHR/fetch JSON API request."""
    headers = OrderedDict([
        ("sec-ch-ua", sec_ch_ua),
        ("Accept", accept),
        ("sec-ch-ua-mobile", "?0"),
        ("User-Agent", user_agent),
        ("sec-ch-ua-platform", sec_ch_ua_platform),
        ("Sec-Fetch-Site", "same-origin"),
        ("Sec-Fetch-Mode", "cors"),
        ("Sec-Fetch-Dest", "empty"),
        ("Accept-Encoding", accept_encoding),
        ("Accept-Language", accept_language),
    ])
    if locale:
        headers["Locale"] = locale
    if priority:
        headers["Priority"] = priority
    return dict(headers)


# ---------------------------------------------------------------------------
# Profile definitions
# ---------------------------------------------------------------------------
# Keep these updated when curl_cffi adds new impersonate targets.
# Run ``curl-cffi list`` to see available fingerprints.
# Run ``python scripts/inspect_vinted_session.py`` to capture real Chrome
# headers as reference.

_LANG_ES = "es-ES,es;q=0.9,en;q=0.8"
_LANG_CHROME146 = "en-GB,en;q=0.9"
_CHROME120_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
_CHROME120_SEC_CH_UA = '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
_CHROME120_ACCEPT_ENCODING = "gzip, deflate, br"

BROWSER_PROFILES: list[BrowserProfile] = [
    BrowserProfile(
        name="chrome_120_win10",
        impersonate="chrome120",
        user_agent=_CHROME120_UA,
        sec_ch_ua=_CHROME120_SEC_CH_UA,
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"Windows"',
        accept_language=_LANG_ES,
        bootstrap_headers=_chrome_bootstrap_headers(
            user_agent=_CHROME120_UA,
            sec_ch_ua=_CHROME120_SEC_CH_UA,
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
            accept_encoding=_CHROME120_ACCEPT_ENCODING,
        ),
        api_headers=_chrome_api_headers(
            user_agent=_CHROME120_UA,
            sec_ch_ua=_CHROME120_SEC_CH_UA,
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_ES,
            accept_encoding=_CHROME120_ACCEPT_ENCODING,
        ),
    ),
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
        sec_ch_ua='"Not-A.Brand";v="24", "Chromium";v="146"',
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform='"Windows"',
        accept_language=_LANG_CHROME146,
        bootstrap_headers=_chrome_bootstrap_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Not-A.Brand";v="24", "Chromium";v="146"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_CHROME146,
            cache_control=None,
            priority="u=0, i",
        ),
        api_headers=_chrome_api_headers(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/146.0.0.0 Safari/537.36"
            ),
            sec_ch_ua='"Not-A.Brand";v="24", "Chromium";v="146"',
            sec_ch_ua_platform='"Windows"',
            accept_language=_LANG_CHROME146,
            accept="application/json,text/plain,*/*,image/webp",
            locale="es-ES",
            priority="u=3",
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
        if profile.name == name:
            return profile
    return None


def get_profile_by_impersonate(impersonate: str) -> BrowserProfile | None:
    """Look up a profile by curl_cffi impersonate target."""
    for profile in BROWSER_PROFILES:
        if profile.impersonate == impersonate:
            return profile
    return None


def profile_for_impersonate(impersonate: str) -> BrowserProfile:
    """Return the configured profile or fail clearly for invalid deployments."""
    profile = get_profile_by_impersonate(impersonate)
    if profile is None:
        supported = ", ".join(profile.impersonate for profile in BROWSER_PROFILES)
        raise ValueError(f"No browser profile configured for impersonate={impersonate!r}. Supported: {supported}")
    return profile
