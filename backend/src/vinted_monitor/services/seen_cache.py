from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import redis
from redis.exceptions import RedisError

from vinted_monitor.core.config import Settings, get_settings


class SeenCacheUnavailableError(RuntimeError):
    pass


class SeenCache(Protocol):
    def require_available(self) -> None:
        """Raise when the cache cannot be used."""

    def claim_unseen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> set[str]:
        """Return IDs reserved for processing by this caller."""

    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        """Mark candidates as processed by this monitor/policy."""

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        """Release short-lived processing locks."""

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
        self.require_available()
        ids = list(dict.fromkeys(vinted_item_ids))
        if not ids:
            return
        pipe = self.client.pipeline(transaction=False)
        index_key = self._seen_index_key(monitor_id, policy_hash)
        for vinted_item_id in ids:
            seen_key = self._seen_key(monitor_id, policy_hash, vinted_item_id)
            pipe.set(seen_key, "1", ex=self.seen_ttl_seconds)
            pipe.zadd(index_key, {vinted_item_id: self.client.time()[0]})
            pipe.delete(self._processing_key(monitor_id, policy_hash, vinted_item_id))
        pipe.expire(index_key, self.seen_ttl_seconds)
        pipe.execute()
        self._trim_seen_index(monitor_id, policy_hash)

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.require_available()
        ids = list(dict.fromkeys(vinted_item_ids))
        if not ids:
            return
        self.client.delete(*[self._processing_key(monitor_id, policy_hash, vinted_item_id) for vinted_item_id in ids])

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


def get_seen_cache(settings: Settings | None = None) -> RedisSeenCache:
    resolved = settings or get_settings()
    return RedisSeenCache(
        client=redis.Redis.from_url(resolved.redis_url, decode_responses=True),
        seen_ttl_seconds=resolved.seen_cache_ttl_seconds,
        processing_ttl_seconds=resolved.seen_processing_ttl_seconds,
        max_per_monitor=resolved.seen_cache_max_per_monitor,
    )
