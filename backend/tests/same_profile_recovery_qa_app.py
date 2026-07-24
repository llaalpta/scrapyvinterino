from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import vinted_monitor.services.runs as runs_module
from vinted_monitor.api.main import app as app
from vinted_monitor.providers.catalog import CatalogSearchResult, CatalogSource
from vinted_monitor.providers.vinted_catalog import (
    PreparedCatalogSession,
    VintedCatalogChallengeError,
)

_STATE_LOCK = threading.Lock()


def _state_path() -> Path:
    if os.getenv("APP_ENV", "").strip().lower() != "test":
        raise RuntimeError("The controlled same-profile recovery provider is test-only")
    raw_path = os.getenv("SAME_PROFILE_QA_STATE")
    if not raw_path or not Path(raw_path).is_absolute():
        raise RuntimeError("SAME_PROFILE_QA_STATE must be an absolute path")
    return Path(raw_path).resolve()


_STATE_PATH = _state_path()


def _read_state() -> dict[str, Any]:
    with _STATE_LOCK:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))


def _increment(name: str, value: str | None = None) -> None:
    with _STATE_LOCK:
        state = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if value is None:
            state[name] = int(state.get(name, 0)) + 1
        else:
            entries = list(state.get(name) or [])
            entries.append(value)
            state[name] = entries
        temporary = _STATE_PATH.with_suffix(f"{_STATE_PATH.suffix}.tmp")
        temporary.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, _STATE_PATH)


class ControlledSameProfileRecoveryProvider:
    def __init__(self, **kwargs: Any) -> None:
        proxy_url = str(kwargs.get("proxy_url") or "")
        if urlsplit(proxy_url).hostname != "127.0.0.1":
            raise AssertionError("Controlled recovery provider requires the loopback proxy")
        self.settings = kwargs["settings"]
        self.event_sink = kwargs.get("event_sink")
        self.prepared_session = kwargs.get("prepared_session")
        self.prevalidated_egress = kwargs.get("prevalidated_egress")
        self.prepared_session_refreshed = False
        self.egress_ip = (
            self.prepared_session.egress_ip
            if self.prepared_session is not None
            else self.prevalidated_egress.context.ip
            if self.prevalidated_egress is not None
            else "192.0.2.10"
        )
        role = (
            "execution"
            if self.prepared_session is not None
            else "forced_preparation"
            if self.prevalidated_egress is not None
            else "initial_preparation"
        )
        _increment("constructions", role)

    def bootstrap_for_session(
        self,
        _source_url: str,
        *,
        collect_datadome: bool = False,
    ) -> dict[str, Any]:
        if not collect_datadome:
            raise AssertionError("Controlled preparation must collect anonymous context")
        _increment("bootstrap_calls")
        if self.prevalidated_egress is None:
            raise VintedCatalogChallengeError("QA controlled initial Cloudflare challenge")
        return {"bootstrap": "ok", "datadome_cookie": True, "cf_bm_cookie": True}

    def probe_catalog_api(
        self,
        _source_url: str,
        *,
        include_payload: bool = False,
    ) -> dict[str, Any]:
        _increment("catalog_probe_calls")
        state = _read_state()
        result: dict[str, Any] = {
            "outcome": "accepted_json",
            "status_code": 200,
            "duration_ms": 1,
            "missing_required": [],
        }
        if include_payload:
            item_id = int(state["item_id"])
            result["payload"] = {
                "items": [
                    {
                        "id": item_id,
                        "title": "QA same-profile recovery baseline",
                        "brand_title": "QA",
                        "price": {"amount": "3.00", "currency_code": "EUR"},
                        "path": f"/items/{item_id}-qa-same-profile-recovery",
                        "size_title": "M",
                        "status": "Muy bueno",
                        "favourite_count": 0,
                        "photo": {"url": "http://127.0.0.1:9/qa-image.webp"},
                        "user": {"login": "qa_recovery"},
                    }
                ],
                "pagination": {
                    "current_page": 1,
                    "total_pages": 1,
                    "total_entries": 1,
                    "per_page": 5,
                },
            }
        return result

    def export_prepared_session(
        self,
        *,
        proxy_session_id: str | None = None,
    ) -> PreparedCatalogSession:
        if not proxy_session_id or not self.egress_ip:
            raise AssertionError("Controlled preparation requires sticky and egress identities")
        return PreparedCatalogSession(
            proxy_session_id=proxy_session_id,
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
            egress_ip=self.egress_ip,
            egress_country_code="ES",
            egress_validated_at=datetime.now(UTC),
        )

    def search(
        self,
        _source: CatalogSource,
        page: int | None = None,
    ) -> CatalogSearchResult:
        del page
        raise AssertionError("Baseline must reuse the controlled preparation payload")

    def close(self) -> None:
        _increment("closes")


runs_module.CurlCffiVintedCatalogProvider = ControlledSameProfileRecoveryProvider
