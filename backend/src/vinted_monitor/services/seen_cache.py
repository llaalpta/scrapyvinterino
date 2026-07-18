from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Protocol

import redis
from redis.exceptions import RedisError

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.redis_client import redis_client_from_url

CANDIDATE_STATE_PAYLOAD_VERSION = 2


class SeenCacheUnavailableError(RuntimeError):
    pass


class SeenCacheOwnershipError(SeenCacheUnavailableError):
    """Raised when another worker owns a candidate being finalized."""


def _translate_redis_errors(method):
    @wraps(method)
    def translated(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except RedisError as exc:
            raise SeenCacheUnavailableError("Redis seen cache is unavailable") from exc

    return translated


@dataclass(frozen=True)
class DetailCandidateStateUpdate:
    terminal_ids: tuple[str, ...] = ()


class SeenCache(Protocol):
    def require_available(self) -> None:
        """Raise when the cache cannot be used."""

    def claim_unseen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> set[str]:
        """Return IDs reserved for processing by this caller."""

    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        """Mark candidates as processed by this monitor/policy."""

    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        """Release short-lived processing locks owned by this caller."""

    def finalize_candidate_states(
        self,
        monitor_id: int,
        policy_hash: str,
        update: DetailCandidateStateUpdate,
    ) -> None:
        """Atomically mark terminal candidates seen and release owned processing locks."""

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
    owner_token: str = field(default_factory=lambda: uuid.uuid4().hex)

    @_translate_redis_errors
    def require_available(self) -> None:
        self.client.ping()

    @_translate_redis_errors
    def claim_unseen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> set[str]:
        self.require_available()
        claimed: set[str] = set()
        for vinted_item_id in dict.fromkeys(vinted_item_ids):
            if self.client.exists(self._seen_key(monitor_id, policy_hash, vinted_item_id)):
                continue
            locked = self.client.set(
                self._processing_key(monitor_id, policy_hash, vinted_item_id),
                self.owner_token,
                nx=True,
                ex=self.processing_ttl_seconds,
            )
            if locked:
                claimed.add(vinted_item_id)
        return claimed

    @_translate_redis_errors
    def mark_seen(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        """Seed terminal state without requiring a processing claim (baseline only)."""
        self.require_available()
        terminal_ids = list(dict.fromkeys(vinted_item_ids))
        if not terminal_ids:
            return
        seen_index_key = self._seen_index_key(monitor_id, policy_hash)
        seen_at = self.client.time()[0]
        pipe = self.client.pipeline(transaction=True)
        for vinted_item_id in terminal_ids:
            pipe.set(self._seen_key(monitor_id, policy_hash, vinted_item_id), "1", ex=self.seen_ttl_seconds)
            pipe.zadd(seen_index_key, {vinted_item_id: seen_at})
        pipe.expire(seen_index_key, self.seen_ttl_seconds)
        pipe.execute()
        self.release_processing(monitor_id, policy_hash, terminal_ids)
        self._trim_seen_index(monitor_id, policy_hash)

    @_translate_redis_errors
    def release_processing(self, monitor_id: int, policy_hash: str, vinted_item_ids: list[str]) -> None:
        self.require_available()
        ids = list(dict.fromkeys(vinted_item_ids))
        if not ids:
            return
        keys = [self._processing_key(monitor_id, policy_hash, vinted_item_id) for vinted_item_id in ids]
        eval_method = getattr(self.client, "eval", None)
        if callable(eval_method):
            script = """
            for _, key in ipairs(KEYS) do
                if redis.call('GET', key) == ARGV[1] then
                    redis.call('DEL', key)
                end
            end
            return 1
            """
            eval_method(script, len(keys), *keys, self.owner_token)
        else:
            self.client.delete(*keys)

    @_translate_redis_errors
    def finalize_candidate_states(
        self,
        monitor_id: int,
        policy_hash: str,
        update: DetailCandidateStateUpdate,
    ) -> None:
        terminal_ids = tuple(dict.fromkeys(update.terminal_ids))
        if not terminal_ids:
            return

        self.require_available()
        seen_index_key = self._seen_index_key(monitor_id, policy_hash)
        seen_at = self.client.time()[0]
        keys = [seen_index_key]
        for vinted_item_id in terminal_ids:
            keys.extend(
                (
                    self._processing_key(monitor_id, policy_hash, vinted_item_id),
                    self._seen_key(monitor_id, policy_hash, vinted_item_id),
                )
            )
        script = """
        local total_count = tonumber(ARGV[4])
        for index = 1, total_count do
            local processing_key = KEYS[2 + ((index - 1) * 2)]
            local lock_owner = redis.call('GET', processing_key)
            if lock_owner and lock_owner ~= ARGV[1] then
                return -index
            end
        end

        for index = 1, total_count do
            local processing_key = KEYS[2 + ((index - 1) * 2)]
            local seen_key = KEYS[3 + ((index - 1) * 2)]
            local item_id = ARGV[4 + index]
            local lock_owner = redis.call('GET', processing_key)
            redis.call('SET', seen_key, '1', 'EX', ARGV[2])
            redis.call('ZADD', KEYS[1], ARGV[3], item_id)
            if lock_owner == ARGV[1] then
                redis.call('DEL', processing_key)
            end
        end
        redis.call('EXPIRE', KEYS[1], ARGV[2])
        return total_count
        """
        eval_method = getattr(self.client, "eval", None)
        if callable(eval_method):
            result = int(
                eval_method(
                    script,
                    len(keys),
                    *keys,
                    self.owner_token,
                    self.seen_ttl_seconds,
                    seen_at,
                    len(terminal_ids),
                    *terminal_ids,
                )
            )
            if result < 0:
                conflicted_item_id = terminal_ids[abs(result) - 1]
                raise SeenCacheOwnershipError(f"Candidate {conflicted_item_id} is owned by another worker")
        else:
            self._finalize_candidate_states_without_lua(
                monitor_id,
                policy_hash,
                terminal_ids,
                seen_at=seen_at,
            )
        self._trim_seen_index(monitor_id, policy_hash)

    def _finalize_candidate_states_without_lua(
        self,
        monitor_id: int,
        policy_hash: str,
        terminal_ids: tuple[str, ...],
        *,
        seen_at: int,
    ) -> None:
        for vinted_item_id in terminal_ids:
            lock_owner = self.client.get(self._processing_key(monitor_id, policy_hash, vinted_item_id))
            if lock_owner and lock_owner != self.owner_token:
                raise SeenCacheOwnershipError(f"Candidate {vinted_item_id} is owned by another worker")
        seen_index_key = self._seen_index_key(monitor_id, policy_hash)
        pipe = self.client.pipeline(transaction=True)
        for vinted_item_id in terminal_ids:
            pipe.set(self._seen_key(monitor_id, policy_hash, vinted_item_id), "1", ex=self.seen_ttl_seconds)
            pipe.zadd(seen_index_key, {vinted_item_id: seen_at})
            if self.client.get(self._processing_key(monitor_id, policy_hash, vinted_item_id)) == self.owner_token:
                pipe.delete(self._processing_key(monitor_id, policy_hash, vinted_item_id))
        pipe.expire(seen_index_key, self.seen_ttl_seconds)
        pipe.execute()

    @_translate_redis_errors
    def has_baseline(self, monitor_id: int, policy_hash: str) -> bool:
        self.require_available()
        return bool(self.client.exists(self._baseline_key(monitor_id, policy_hash)))

    @_translate_redis_errors
    def mark_baseline(self, monitor_id: int, policy_hash: str) -> None:
        self.require_available()
        self.client.set(self._baseline_key(monitor_id, policy_hash), "1", ex=self.seen_ttl_seconds)

    @_translate_redis_errors
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


def serialize_candidate_state_update(update: DetailCandidateStateUpdate) -> dict[str, Any]:
    return {
        "version": CANDIDATE_STATE_PAYLOAD_VERSION,
        "terminal_ids": list(dict.fromkeys(update.terminal_ids)),
    }


def deserialize_candidate_state_update(payload: Any) -> DetailCandidateStateUpdate:
    if not isinstance(payload, dict) or payload.get("version") != CANDIDATE_STATE_PAYLOAD_VERSION:
        raise ValueError("invalid candidate state transition payload")
    terminal_payload = payload.get("terminal_ids")
    if not isinstance(terminal_payload, list):
        raise ValueError("invalid candidate state transition payload")
    terminal_ids = tuple(dict.fromkeys(str(item_id) for item_id in terminal_payload if str(item_id)))
    return DetailCandidateStateUpdate(terminal_ids=terminal_ids)


def get_seen_cache(
    settings: Settings | None = None,
    *,
    socket_timeout: float | None = 5,
) -> RedisSeenCache:
    resolved = settings or get_settings()
    return RedisSeenCache(
        client=redis_client_from_url(
            resolved.redis_url,
            decode_responses=True,
            socket_timeout=socket_timeout,
        ),
        seen_ttl_seconds=resolved.seen_cache_ttl_seconds,
        processing_ttl_seconds=resolved.seen_processing_ttl_seconds,
        max_per_monitor=resolved.seen_cache_max_per_monitor,
    )
