from __future__ import annotations

import random
import time

DATADOME_CHALLENGE_MARKERS = [
    "geo.captcha-delivery.com",
    "interstitial",
    "dd.js",
    "t.datadome.co",
    "datadome.co/captcha",
]


class DataDomeChallengeError(RuntimeError):
    """Raised when DataDome serves a challenge instead of real content."""


def is_datadome_challenge(status_code: int, headers: dict[str, str], body_snippet: str) -> bool:
    """Detect whether the response is a DataDome challenge rather than real content.

    Args:
        status_code: HTTP status code of the response.
        headers: Response headers (case-insensitive dict recommended).
        body_snippet: First ~3000 characters of the response body.

    Returns:
        True if the response appears to be a DataDome challenge.
    """
    if status_code in (403, 429):
        return True

    server = _header_value(headers, "server")
    if server and "datadome" in server.lower():
        return True

    x_dd = _header_value(headers, "x-datadome")
    if x_dd:
        return True

    content_type = _header_value(headers, "content-type")
    if content_type and "text/html" in content_type.lower() and status_code == 200:
        lower_snippet = body_snippet.lower()
        return any(marker in lower_snippet for marker in DATADOME_CHALLENGE_MARKERS)

    return False


def extract_datadome_cookie_value(cookies: dict[str, str]) -> str | None:
    """Extract the ``datadome`` cookie value from a cookie dict.

    Returns None if the cookie is not present.
    """
    return cookies.get("datadome")


def has_datadome_cookie(cookies: dict[str, str]) -> bool:
    """Check whether the datadome cookie is present and non-empty."""
    value = extract_datadome_cookie_value(cookies)
    return bool(value)


def human_delay(
    min_seconds: float = 1.2,
    max_seconds: float = 3.8,
    rng: random.Random | None = None,
) -> float:
    """Sleep for a human-like duration between requests.

    Uses a Beta distribution skewed toward the lower-center range to
    simulate realistic page-load wait times rather than a uniform
    distribution.

    Returns the actual delay applied in seconds.
    """
    generator = rng or random.Random()
    # Beta(2, 5) produces values skewed toward the lower end of [0,1]
    # with mean ~0.286, which maps to a natural 1.2–2.5s center.
    normalized = generator.betavariate(2, 5)
    delay = normalized * (max_seconds - min_seconds) + min_seconds
    time.sleep(delay)
    return delay


def _header_value(headers: dict[str, str], key: str) -> str | None:
    """Case-insensitive header lookup."""
    lower_key = key.lower()
    for header_key, value in headers.items():
        if header_key.lower() == lower_key:
            return value
    return None
