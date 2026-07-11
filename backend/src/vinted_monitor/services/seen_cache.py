from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

import redis
from redis.exceptions import RedisError

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.redis_client import redis_client_from_url
from vinted_monitor.providers.catalog import CatalogItemCandidate

DETAIL_RETRY_PAYLOAD_VERSION = 1


class SeenCacheUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class DetailRetryRecord:
    candidate: CatalogItemCandidate
    attempt_count: int
    next_attempt_at: datetime
    failure_kind: str

    def __post_init__(self) -> None:
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be greater than or equal to zero")
        if self.next_attempt_at.tzinfo is None:
            raise ValueError("next_attempt_at must be timezone-aware")
        if (
            not self.failure_kind
            or len(self.failure_kind) > 80
            or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in self.failure_kind)
        ):
            raise ValueError("failure_kind must be a lowercase identifier")


@dataclass(frozen=True)
class DetailCandidateStateUpdate:
    terminal_ids: tuple[str, ...] = ()
    retries: tuple[DetailRetryRecord, ...] = ()


class SeenCache(Protocol):
    def require_available(self) -> None:
        """Raise when the cache cannot be used."""

    def claim_unseen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> set[str]:
        """Return IDs reserved for processing by this caller."""

    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        """Mark candidates as processed by this monitor/policy."""

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        """Release short-lived processing locks."""

    def claim_due_detail_retries(
        self,
        monitor_id: int,
        policy_hash: str,
        *,
        due_at: datetime,
        limit: int,
    ) -> list[DetailRetryRecord]:
        """Claim due detail retries while leaving their durable payloads queued."""

    def finalize_candidate_states(
        self,
        monitor_id: int,
        policy_hash: str,
        update: DetailCandidateStateUpdate,
    ) -> None:
        """Atomically mark terminal candidates seen and schedule retry candidates."""

    def has_baseline(self, monitor_id: int, policy_hash: str) -> bool:
        """Return whether this monitor/policy has an initial catalog snapshot."""

    def mark_baseline(self, monitor_id: int, policy_hash: str) -> None:
        """Mark this monitor/policy as calibrated with an initial catalog snapshot."""


@dataclass(frozen=True)
class RedisSeenCache:
    client: redis.Redis
    seen_ttl_seconds: int
    processing_ttl_seconds: int
    max_per_monitor: int

    def require_available(self) -> None:
        try:
            self.client.ping()
        except RedisError as exc:
            raise SeenCacheUnavailableError("Redis seen cache is unavailable") from exc

    def claim_unseen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> set[str]:
        self.require_available()
        claimed: set[str] = set()
        for vinted_item_id in dict.fromkeys(vinted_item_ids):
            if self.client.exists(self._seen_key(monitor_id, policy_hash, vinted_item_id)):
                continue
            if self.client.exists(self._detail_retry_key(monitor_id, policy_hash, vinted_item_id)):
                continue
            locked = self.client.set(
                self._processing_key(monitor_id, policy_hash, vinted_item_id),
                "1",
                nx=True,
                ex=self.processing_ttl_seconds,
            )
            if locked:
                claimed.add(vinted_item_id)
        return claimed

    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.finalize_candidate_states(
            monitor_id,
            policy_hash,
            DetailCandidateStateUpdate(terminal_ids=tuple(dict.fromkeys(vinted_item_ids))),
        )

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.require_available()
        ids = list(dict.fromkeys(vinted_item_ids))
        if not ids:
            return
        self.client.delete(*[self._processing_key(monitor_id, policy_hash, vinted_item_id) for vinted_item_id in ids])

    def claim_due_detail_retries(
        self,
        monitor_id: int,
        policy_hash: str,
        *,
        due_at: datetime,
        limit: int,
    ) -> list[DetailRetryRecord]:
        self.require_available()
        if due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        if limit <= 0:
            return []

        index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        retry_ids = self.client.zrangebyscore(index_key, "-inf", due_at.timestamp(), start=0, num=limit)
        claimed: list[DetailRetryRecord] = []
        for raw_vinted_item_id in retry_ids:
            vinted_item_id = str(raw_vinted_item_id)
            retry_key = self._detail_retry_key(monitor_id, policy_hash, vinted_item_id)
            if self.client.exists(self._seen_key(monitor_id, policy_hash, vinted_item_id)):
                self._remove_detail_retry(monitor_id, policy_hash, vinted_item_id)
                continue
            raw_payload = self.client.get(retry_key)
            retry = _deserialize_detail_retry(raw_payload, expected_item_id=vinted_item_id)
            if retry is None:
                self._remove_detail_retry(monitor_id, policy_hash, vinted_item_id)
                continue
            locked = self.client.set(
                self._processing_key(monitor_id, policy_hash, vinted_item_id),
                "1",
                nx=True,
                ex=self.processing_ttl_seconds,
            )
            if locked:
                claimed.append(retry)
        return claimed

    def finalize_candidate_states(
        self,
        monitor_id: int,
        policy_hash: str,
        update: DetailCandidateStateUpdate,
    ) -> None:
        terminal_ids = tuple(dict.fromkeys(update.terminal_ids))
        retries_by_id: dict[str, DetailRetryRecord] = {}
        for retry in update.retries:
            vinted_item_id = retry.candidate.vinted_item_id
            if vinted_item_id in retries_by_id:
                raise ValueError(f"duplicate detail retry for item {vinted_item_id}")
            retries_by_id[vinted_item_id] = retry
        overlap = set(terminal_ids).intersection(retries_by_id)
        if overlap:
            raise ValueError(f"candidate states overlap for items: {', '.join(sorted(overlap))}")
        if not terminal_ids and not retries_by_id:
            return

        self.require_available()
        seen_index_key = self._seen_index_key(monitor_id, policy_hash)
        retry_index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        seen_at = self.client.time()[0]
        pipe = self.client.pipeline(transaction=True)
        for vinted_item_id in terminal_ids:
            pipe.set(self._seen_key(monitor_id, policy_hash, vinted_item_id), "1", ex=self.seen_ttl_seconds)
            pipe.zadd(seen_index_key, {vinted_item_id: seen_at})
            pipe.delete(self._processing_key(monitor_id, policy_hash, vinted_item_id))
            pipe.delete(self._detail_retry_key(monitor_id, policy_hash, vinted_item_id))
            pipe.zrem(retry_index_key, vinted_item_id)
        for vinted_item_id, retry in retries_by_id.items():
            pipe.set(
                self._detail_retry_key(monitor_id, policy_hash, vinted_item_id),
                _serialize_detail_retry(retry),
                ex=self.seen_ttl_seconds,
            )
            pipe.zadd(retry_index_key, {vinted_item_id: retry.next_attempt_at.timestamp()})
            pipe.delete(self._processing_key(monitor_id, policy_hash, vinted_item_id))
        if terminal_ids:
            pipe.expire(seen_index_key, self.seen_ttl_seconds)
        pipe.expire(retry_index_key, self.seen_ttl_seconds)
        pipe.execute()
        if terminal_ids:
            self._trim_seen_index(monitor_id, policy_hash)

    def has_baseline(self, monitor_id: int, policy_hash: str) -> bool:
        self.require_available()
        return bool(self.client.exists(self._baseline_key(monitor_id, policy_hash)))

    def mark_baseline(self, monitor_id: int, policy_hash: str) -> None:
        self.require_available()
        self.client.set(self._baseline_key(monitor_id, policy_hash), "1", ex=self.seen_ttl_seconds)

    def _trim_seen_index(self, monitor_id: int, policy_hash: str) -> None:
        if self.max_per_monitor <= 0:
            return
        index_key = self._seen_index_key(monitor_id, policy_hash)
        overflow = self.client.zcard(index_key) - self.max_per_monitor
        if overflow <= 0:
            return
        removed = self.client.zrange(index_key, 0, overflow - 1)
        if removed:
            self.client.delete(*[self._seen_key(monitor_id, policy_hash, str(vinted_item_id)) for vinted_item_id in removed])
            self.client.zrem(index_key, *removed)

    def _remove_detail_retry(self, monitor_id: int, policy_hash: str, vinted_item_id: str) -> None:
        pipe = self.client.pipeline(transaction=True)
        pipe.delete(self._detail_retry_key(monitor_id, policy_hash, vinted_item_id))
        pipe.delete(self._processing_key(monitor_id, policy_hash, vinted_item_id))
        pipe.zrem(self._detail_retry_index_key(monitor_id, policy_hash), vinted_item_id)
        pipe.execute()

    @staticmethod
    def _seen_key(monitor_id: int, policy_hash: str, vinted_item_id: str) -> str:
        return f"seen:monitor:{monitor_id}:policy:{policy_hash}:item:{vinted_item_id}"

    @staticmethod
    def _processing_key(monitor_id: int, policy_hash: str, vinted_item_id: str) -> str:
        return f"processing:monitor:{monitor_id}:policy:{policy_hash}:item:{vinted_item_id}"

    @staticmethod
    def _seen_index_key(monitor_id: int, policy_hash: str) -> str:
        return f"seen-index:monitor:{monitor_id}:policy:{policy_hash}"

    @staticmethod
    def _baseline_key(monitor_id: int, policy_hash: str) -> str:
        return f"baseline:monitor:{monitor_id}:policy:{policy_hash}"

    @staticmethod
    def _detail_retry_key(monitor_id: int, policy_hash: str, vinted_item_id: str) -> str:
        return f"detail-retry:monitor:{monitor_id}:policy:{policy_hash}:item:{vinted_item_id}"

    @staticmethod
    def _detail_retry_index_key(monitor_id: int, policy_hash: str) -> str:
        return f"detail-retry-index:monitor:{monitor_id}:policy:{policy_hash}"


def _serialize_detail_retry(retry: DetailRetryRecord) -> str:
    candidate = retry.candidate
    payload = {
        "version": DETAIL_RETRY_PAYLOAD_VERSION,
        "candidate": {
            "vinted_item_id": candidate.vinted_item_id,
            "title": candidate.title,
            "brand": candidate.brand,
            "price_amount": str(candidate.price_amount) if candidate.price_amount is not None else None,
            "currency": candidate.currency,
            "size": candidate.size,
            "status": candidate.status,
            "seller_login": candidate.seller_login,
            "seller_country": candidate.seller_country,
            "favorite_count": candidate.favorite_count,
            "url": candidate.url,
            "image_url": candidate.image_url,
        },
        "attempt_count": retry.attempt_count,
        "next_attempt_at": retry.next_attempt_at.astimezone(UTC).isoformat(),
        "failure_kind": retry.failure_kind,
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _deserialize_detail_retry(raw_payload: Any, *, expected_item_id: str) -> DetailRetryRecord | None:
    if not isinstance(raw_payload, str):
        return None
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict) or payload.get("version") != DETAIL_RETRY_PAYLOAD_VERSION:
            return None
        candidate_payload = payload.get("candidate")
        if not isinstance(candidate_payload, dict) or str(candidate_payload.get("vinted_item_id")) != expected_item_id:
            return None
        title = candidate_payload.get("title")
        url = candidate_payload.get("url")
        if not isinstance(title, str) or not title or not isinstance(url, str) or not url:
            return None
        price_amount = _optional_decimal(candidate_payload.get("price_amount"))
        candidate_without_raw = CatalogItemCandidate(
            vinted_item_id=expected_item_id,
            title=title,
            brand=_optional_string(candidate_payload.get("brand")),
            price_amount=price_amount,
            currency=_optional_string(candidate_payload.get("currency")),
            size=_optional_string(candidate_payload.get("size")),
            status=_optional_string(candidate_payload.get("status")),
            seller_login=_optional_string(candidate_payload.get("seller_login")),
            seller_country=_optional_string(candidate_payload.get("seller_country")),
            favorite_count=_optional_int(candidate_payload.get("favorite_count")),
            url=url,
            image_url=_optional_string(candidate_payload.get("image_url")),
            raw={},
        )
        candidate = replace(candidate_without_raw, raw=_sanitized_candidate_raw(candidate_without_raw))
        return DetailRetryRecord(
            candidate=candidate,
            attempt_count=int(payload["attempt_count"]),
            next_attempt_at=datetime.fromisoformat(str(payload["next_attempt_at"])),
            failure_kind=str(payload["failure_kind"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _sanitized_candidate_raw(candidate: CatalogItemCandidate) -> dict[str, Any]:
    return {
        "id": candidate.vinted_item_id,
        "title": candidate.title,
        "brand_title": candidate.brand,
        "price": {
            "amount": str(candidate.price_amount) if candidate.price_amount is not None else None,
            "currency_code": candidate.currency,
        },
        "path": candidate.url,
        "size_title": candidate.size,
        "status": candidate.status,
        "favourite_count": candidate.favorite_count,
        "photo": {"url": candidate.image_url},
        "user": {"login": candidate.seller_login},
    }


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid decimal") from None


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def get_seen_cache(settings: Settings | None = None) -> RedisSeenCache:
    resolved = settings or get_settings()
    return RedisSeenCache(
        client=redis_client_from_url(resolved.redis_url, decode_responses=True),
        seen_ttl_seconds=resolved.seen_cache_ttl_seconds,
        processing_ttl_seconds=resolved.seen_processing_ttl_seconds,
        max_per_monitor=resolved.seen_cache_max_per_monitor,
    )
