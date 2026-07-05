from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from curl_cffi.requests import Session

CHROME120_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
CHROME120_SEC_CH_UA = '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
CHROME120_ACCEPT_LANGUAGE = "es-ES,es;q=0.9,en;q=0.8"
CHROME120_ACCEPT_ENCODING = "gzip, deflate, br"


class EphemeralHttpClientError(RuntimeError):
    pass


def chrome120_bootstrap_headers(referer: str | None = None) -> OrderedDict[str, str]:
    """Return Chrome 120 navigation headers used by the ephemeral preflight client."""
    headers = OrderedDict(
        [
            ("sec-ch-ua", CHROME120_SEC_CH_UA),
            ("sec-ch-ua-mobile", "?0"),
            ("sec-ch-ua-platform", '"Windows"'),
            ("Upgrade-Insecure-Requests", "1"),
            ("User-Agent", CHROME120_UA),
            (
                "Accept",
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7",
            ),
            ("Sec-Fetch-Site", "none" if referer is None else "same-origin"),
            ("Sec-Fetch-Mode", "navigate"),
            ("Sec-Fetch-User", "?1"),
            ("Sec-Fetch-Dest", "document"),
            ("Accept-Encoding", CHROME120_ACCEPT_ENCODING),
            ("Accept-Language", CHROME120_ACCEPT_LANGUAGE),
        ]
    )
    if referer:
        headers["Referer"] = referer
    return headers


class EphemeralVintedHttpClient:
    """One-run HTTP client with Chrome 120 TLS impersonation and optional proxy egress."""

    def __init__(
        self,
        *,
        base_url: str,
        proxy_url: str | None,
        timeout_ms: int,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        self.base_url = base_url.rstrip("/")
        self.proxy_url = proxy_url
        self.timeout_seconds = timeout_ms / 1000
        self.session_factory = session_factory or Session
        self.session = self.session_factory(impersonate="chrome120", proxies=proxies)

    def __enter__(self) -> EphemeralVintedHttpClient:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def get_json(self, url: str, *, headers: Mapping[str, str] | None = None) -> dict[str, Any]:
        response = self.session.get(
            url,
            headers=dict(headers or chrome120_bootstrap_headers()),
            timeout=self.timeout_seconds,
        )
        status_code = int(getattr(response, "status_code", 0))
        if status_code >= 400:
            raise EphemeralHttpClientError(f"GET {url} failed with HTTP {status_code}")

        try:
            payload = response.json()
        except Exception as exc:
            raise EphemeralHttpClientError(f"GET {url} did not return valid JSON") from exc
        if not isinstance(payload, dict):
            raise EphemeralHttpClientError(f"GET {url} returned {type(payload).__name__}, expected object")
        return payload
