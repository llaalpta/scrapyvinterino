from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

from vinted_monitor.api.main import app, get_manual_run_provider
from vinted_monitor.providers.catalog import (
    CatalogItemCandidate,
    CatalogItemDetail,
    CatalogSearchResult,
    CatalogSource,
)
from vinted_monitor.providers.vinted_catalog import VintedCatalogChallengeError

_QA_ITEM_ID = re.compile(r"^qa-(?:manual|recurring)-[0-9a-f]{32}-[A-F]$")


def _provider_state_path() -> Path:
    if os.getenv("APP_ENV", "").strip().lower() != "test":
        raise RuntimeError("The controlled manual-session provider is test-only")
    raw_path = os.getenv("SESSION_QA_PROVIDER_STATE") or os.getenv("MANUAL_SESSION_QA_PROVIDER_STATE")
    if not raw_path or not Path(raw_path).is_absolute():
        raise RuntimeError("SESSION_QA_PROVIDER_STATE must be an absolute path")
    return Path(raw_path).resolve()


_STATE_PATH = _provider_state_path()


class ControlledManualSessionProvider:
    def __init__(self, **kwargs: Any) -> None:
        self.settings = kwargs.get("settings")
        self.event_sink = kwargs.get("event_sink")
        self.prepared_session = kwargs.get("prepared_session")
        self.prepared_session_refreshed = False

    def search(self, source: CatalogSource, page: int | None = None) -> CatalogSearchResult:
        state = _load_state()
        delay_ms = state["delay_ms"]
        if delay_ms:
            time.sleep(delay_ms / 1000)
        if state["mode"] == "fail":
            raise RuntimeError("QA catalog provider forced failure")
        if state["mode"] == "challenge":
            raise VintedCatalogChallengeError("QA controlled Cloudflare challenge")

        self._record_transfer(category="catalog", total_bytes=1000)
        items = [_candidate(item_id) for item_id in state["ids"]]
        return CatalogSearchResult(
            items=items,
            page=page or 1,
            total_pages=1,
            total_entries=len(items),
            per_page=len(items),
            next_page=None,
            provider_metadata={"provider": "controlled_manual_session_qa", "source_url_observed": bool(source.url)},
        )

    def fetch_detail(
        self,
        candidate: CatalogItemCandidate,
        *,
        referer_url: str | None = None,
        early_filter_terms: tuple[str, ...] = (),
    ) -> CatalogItemDetail:
        del referer_url, early_filter_terms
        self._record_transfer(category="detail", total_bytes=2000)
        return CatalogItemDetail(
            vinted_item_id=candidate.vinted_item_id,
            title=candidate.title,
            brand=candidate.brand,
            size=candidate.size,
            status=candidate.status,
            price_amount=candidate.price_amount,
            currency=candidate.currency,
            description=f"Controlled detail for {candidate.vinted_item_id}",
            color="Blue",
            category="QA",
            photos=[f"http://127.0.0.1:9/assets/{candidate.vinted_item_id}.webp"],
            availability_flags={"state": "available", "buyable": True},
            observed_fields=frozenset(
                {
                    "title",
                    "description",
                    "brand",
                    "size",
                    "status",
                    "price_amount",
                    "currency",
                    "photos",
                }
            ),
            field_sources={"description": "controlled_manual_session_qa"},
        )

    def close(self) -> None:
        return None

    def _record_transfer(self, *, category: str, total_bytes: int) -> None:
        if self.event_sink is None:
            return
        self.event_sink(
            phase=f"qa_{category}_transfer_observed",
            method="GET",
            url=f"http://127.0.0.1/qa/{category}",
            status_code=200,
            details={
                "proxy_transfer": {
                    "category": category,
                    "observed_requests": 1,
                    "unobserved_attempts": 0,
                    "request_size_bytes": 100,
                    "upload_size_bytes": 0,
                    "header_size_bytes": 200,
                    "download_size_bytes": total_bytes - 300,
                    "total_observed_bytes": total_bytes,
                }
            },
        )


def _load_state() -> dict[str, Any]:
    try:
        payload = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Controlled QA catalog state is unavailable") from exc
    if not isinstance(payload, dict) or payload.get("mode") not in {"ok", "fail", "challenge"}:
        raise RuntimeError("Controlled QA catalog state has an invalid mode")
    ids = payload.get("ids", [])
    delay_ms = payload.get("delay_ms", 0)
    if (
        not isinstance(ids, list)
        or len(ids) > 6
        or len(ids) != len(set(ids))
        or any(not isinstance(item_id, str) or _QA_ITEM_ID.fullmatch(item_id) is None for item_id in ids)
    ):
        raise RuntimeError("Controlled QA catalog state has invalid item ids")
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int) or not 0 <= delay_ms <= 2000:
        raise RuntimeError("Controlled QA catalog delay must be between 0 and 2000 ms")
    return {"mode": payload["mode"], "ids": ids, "delay_ms": delay_ms}


def _candidate(item_id: str) -> CatalogItemCandidate:
    return CatalogItemCandidate(
        vinted_item_id=item_id,
        title=f"Controlled item {item_id[-1]}",
        brand="QA Brand",
        price_amount=Decimal("9.50"),
        currency="EUR",
        size="M",
        status="Very good",
        seller_login="qa_seller",
        seller_country="ES",
        favorite_count=0,
        view_count=0,
        url=f"http://127.0.0.1:9/items/{item_id}",
        image_url=f"http://127.0.0.1:9/assets/{item_id}.webp",
        raw={},
    )


def _controlled_provider() -> ControlledManualSessionProvider:
    return ControlledManualSessionProvider()


app.dependency_overrides[get_manual_run_provider] = _controlled_provider
