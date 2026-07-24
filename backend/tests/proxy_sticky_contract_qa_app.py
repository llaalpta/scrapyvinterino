from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import unquote, urlsplit

import vinted_monitor.services.runs as runs_module
from vinted_monitor.api.main import app as app
from vinted_monitor.providers.catalog import CatalogSearchResult, CatalogSource
from vinted_monitor.providers.vinted_catalog import PreparedCatalogSession


class ControlledStickyContractProvider:
    def __init__(self, **kwargs: Any) -> None:
        if os.getenv("APP_ENV", "").strip().lower() != "test":
            raise RuntimeError("The controlled sticky-contract provider is test-only")
        proxy_url = kwargs.get("proxy_url")
        if not isinstance(proxy_url, str):
            raise AssertionError("Sticky-contract provider requires proxy transport")
        sticky_username = unquote(urlsplit(proxy_url).username or "")
        if ";sessid." not in sticky_username and "-qa-" not in sticky_username:
            raise AssertionError("Sticky-contract provider received an unexpected username format")
        self.settings = kwargs["settings"]
        self.event_sink = kwargs.get("event_sink")
        self.prepared_session = kwargs.get("prepared_session")
        self.prepared_session_refreshed = False

    def bootstrap_for_session(
        self,
        _source_url: str,
        *,
        collect_datadome: bool = False,
    ) -> dict[str, object]:
        if not collect_datadome:
            raise AssertionError("Sticky-contract preparation must collect DataDome context")
        return {
            "bootstrap": "controlled",
            "datadome_cookie": True,
            "cf_bm_cookie": True,
        }

    def probe_catalog_api(
        self,
        _source_url: str,
        *,
        include_payload: bool = False,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "outcome": "accepted_json",
            "status_code": 200,
            "duration_ms": 1,
            "missing_required": [],
        }
        if include_payload:
            result["payload"] = {
                "items": [],
                "pagination": {
                    "current_page": 1,
                    "total_pages": 1,
                    "total_entries": 0,
                    "per_page": 5,
                },
            }
        return result

    def export_prepared_session(
        self,
        *,
        proxy_session_id: str | None = None,
    ) -> PreparedCatalogSession:
        sticky_id = proxy_session_id or getattr(self.prepared_session, "proxy_session_id", None)
        if not sticky_id:
            raise AssertionError("Sticky-contract provider requires a proxy session ID")
        return PreparedCatalogSession(
            proxy_session_id=sticky_id,
            cookies={
                "access_token_web": "qa-access",
                "v_udt": "qa-v-udt",
                "anon_id": "qa-anon",
                "datadome": "qa-datadome",
                "__cf_bm": "qa-cf-bm",
            },
            csrf_token="qa-csrf",
            anon_id="qa-anon",
            access_token_web="qa-access",
            datadome="qa-datadome",
            cf_bm="qa-cf-bm",
            v_udt="qa-v-udt",
            user_iso_locale="es-ES",
            vinted_screen="catalog",
            egress_ip="192.0.2.20",
            egress_country_code="ES",
            egress_validated_at=datetime.now(UTC),
        )

    def search(
        self,
        _source: CatalogSource,
        page: int | None = None,
    ) -> CatalogSearchResult:
        return CatalogSearchResult(
            items=[],
            page=page or 1,
            total_pages=1,
            total_entries=0,
            per_page=5,
            next_page=None,
            provider_metadata={"provider": "controlled_sticky_contract"},
        )

    def close(self) -> None:
        return None


runs_module.CurlCffiVintedCatalogProvider = ControlledStickyContractProvider
