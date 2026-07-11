from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps
from typing import Any, Protocol

import redis
from redis.exceptions import RedisError

from vinted_monitor.core.config import Settings, get_settings
from vinted_monitor.core.redis_client import redis_client_from_url
from vinted_monitor.providers.catalog import CatalogItemCandidate

DETAIL_RETRY_PAYLOAD_VERSION = 1


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

    def claim_unseen_with_recovery(
        self,
        monitor_id: int,
        policy_hash: str,
        candidates: list[CatalogItemCandidate],
    ) -> set[str]:
        """Atomically claim catalog candidates and persist their recovery payloads."""

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

    def stage_candidate_retries(
        self,
        monitor_id: int,
        policy_hash: str,
        retries: tuple[DetailRetryRecord, ...],
    ) -> None:
        """Persist recovery payloads without releasing their processing locks."""

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
            if self.client.exists(self._detail_retry_key(monitor_id, policy_hash, vinted_item_id)):
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
        retry_index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        seen_at = self.client.time()[0]
        pipe = self.client.pipeline(transaction=True)
        for vinted_item_id in terminal_ids:
            pipe.set(self._seen_key(monitor_id, policy_hash, vinted_item_id), "1", ex=self.seen_ttl_seconds)
            pipe.zadd(seen_index_key, {vinted_item_id: seen_at})
            pipe.delete(self._detail_retry_key(monitor_id, policy_hash, vinted_item_id))
            pipe.zrem(retry_index_key, vinted_item_id)
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
            raw_payload = self._claim_due_detail_retry_payload(
                monitor_id,
                policy_hash,
                vinted_item_id,
                due_at=due_at,
            )
            if raw_payload is None:
                continue
            retry = _deserialize_detail_retry(raw_payload, expected_item_id=vinted_item_id)
            if retry is None:
                self._discard_claimed_detail_retry(monitor_id, policy_hash, vinted_item_id)
                continue
            claimed.append(retry)
        return claimed

    def _claim_due_detail_retry_payload(
        self,
        monitor_id: int,
        policy_hash: str,
        vinted_item_id: str,
        *,
        due_at: datetime,
    ) -> str | None:
        index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        seen_key = self._seen_key(monitor_id, policy_hash, vinted_item_id)
        retry_key = self._detail_retry_key(monitor_id, policy_hash, vinted_item_id)
        processing_key = self._processing_key(monitor_id, policy_hash, vinted_item_id)
        script = """
        local retry_score = redis.call('ZSCORE', KEYS[1], ARGV[1])
        if not retry_score or tonumber(retry_score) > tonumber(ARGV[2]) then
            return {0, ''}
        end
        if redis.call('EXISTS', KEYS[2]) == 1 then
            redis.call('DEL', KEYS[3])
            redis.call('ZREM', KEYS[1], ARGV[1])
            return {0, ''}
        end
        local payload = redis.call('GET', KEYS[3])
        if not payload then
            redis.call('ZREM', KEYS[1], ARGV[1])
            return {0, ''}
        end
        local locked = redis.call('SET', KEYS[4], ARGV[3], 'NX', 'EX', ARGV[4])
        if not locked then
            return {0, ''}
        end
        return {1, payload}
        """
        eval_method = getattr(self.client, "eval", None)
        if callable(eval_method):
            result = eval_method(
                script,
                4,
                index_key,
                seen_key,
                retry_key,
                processing_key,
                vinted_item_id,
                due_at.timestamp(),
                self.owner_token,
                self.processing_ttl_seconds,
            )
            if not isinstance(result, (list, tuple)) or not result or int(result[0]) != 1:
                return None
            return str(result[1])

        if self.client.exists(seen_key):
            self._remove_detail_retry(monitor_id, policy_hash, vinted_item_id)
            return None
        raw_payload = self.client.get(retry_key)
        if raw_payload is None:
            self._remove_detail_retry(monitor_id, policy_hash, vinted_item_id)
            return None
        locked = self.client.set(
            processing_key,
            self.owner_token,
            nx=True,
            ex=self.processing_ttl_seconds,
        )
        return str(raw_payload) if locked else None

    def _discard_claimed_detail_retry(self, monitor_id: int, policy_hash: str, vinted_item_id: str) -> None:
        script = """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then
            return 0
        end
        redis.call('DEL', KEYS[1])
        redis.call('DEL', KEYS[2])
        redis.call('ZREM', KEYS[3], ARGV[2])
        return 1
        """
        processing_key = self._processing_key(monitor_id, policy_hash, vinted_item_id)
        retry_key = self._detail_retry_key(monitor_id, policy_hash, vinted_item_id)
        index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        eval_method = getattr(self.client, "eval", None)
        if callable(eval_method):
            eval_method(
                script,
                3,
                processing_key,
                retry_key,
                index_key,
                self.owner_token,
                vinted_item_id,
            )
            return
        if self.client.get(processing_key) == self.owner_token:
            self.client.delete(processing_key)
            self._remove_detail_retry(monitor_id, policy_hash, vinted_item_id)

    @_translate_redis_errors
    def claim_unseen_with_recovery(
        self,
        monitor_id: int,
        policy_hash: str,
        candidates: list[CatalogItemCandidate],
    ) -> set[str]:
        self.require_available()
        claimed: set[str] = set()
        now = datetime.now(UTC)
        index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        script = """
        if redis.call('EXISTS', KEYS[1]) == 1 or redis.call('EXISTS', KEYS[2]) == 1 then
            return 0
        end
        local locked = redis.call('SET', KEYS[3], ARGV[1], 'NX', 'EX', ARGV[2])
        if not locked then
            return 0
        end
        redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[4])
        redis.call('ZADD', KEYS[4], ARGV[5], ARGV[6])
        redis.call('EXPIRE', KEYS[4], ARGV[4])
        return 1
        """
        for candidate in {candidate.vinted_item_id: candidate for candidate in candidates}.values():
            item_id = candidate.vinted_item_id
            retry = DetailRetryRecord(
                candidate=candidate,
                attempt_count=0,
                next_attempt_at=now,
                failure_kind="detail_claim_recovery",
            )
            keys = (
                self._seen_key(monitor_id, policy_hash, item_id),
                self._detail_retry_key(monitor_id, policy_hash, item_id),
                self._processing_key(monitor_id, policy_hash, item_id),
                index_key,
            )
            eval_method = getattr(self.client, "eval", None)
            if callable(eval_method):
                acquired = eval_method(
                    script,
                    len(keys),
                    *keys,
                    self.owner_token,
                    self.processing_ttl_seconds,
                    _serialize_detail_retry(retry),
                    self.seen_ttl_seconds,
                    now.timestamp(),
                    item_id,
                )
            else:
                acquired = item_id in self.claim_unseen(monitor_id, policy_hash, [item_id])
                if acquired:
                    self.stage_candidate_retries(monitor_id, policy_hash, (retry,))
            if acquired:
                claimed.add(item_id)
        return claimed

    @_translate_redis_errors
    def stage_candidate_retries(
        self,
        monitor_id: int,
        policy_hash: str,
        retries: tuple[DetailRetryRecord, ...],
    ) -> None:
        retries_by_id = {retry.candidate.vinted_item_id: retry for retry in retries}
        if len(retries_by_id) != len(retries):
            raise ValueError("duplicate candidate recovery retry")
        if not retries_by_id:
            return
        self.require_available()
        retry_index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        pipe = self.client.pipeline(transaction=True)
        for vinted_item_id, retry in retries_by_id.items():
            pipe.set(
                self._detail_retry_key(monitor_id, policy_hash, vinted_item_id),
                _serialize_detail_retry(retry),
                ex=self.seen_ttl_seconds,
            )
            pipe.zadd(retry_index_key, {vinted_item_id: retry.next_attempt_at.timestamp()})
        pipe.expire(retry_index_key, self.seen_ttl_seconds)
        pipe.execute()

    @_translate_redis_errors
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
        item_ids = [*terminal_ids, *retries_by_id]
        seen_index_key = self._seen_index_key(monitor_id, policy_hash)
        retry_index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        seen_at = self.client.time()[0]
        keys = [seen_index_key, retry_index_key]
        for vinted_item_id in item_ids:
            keys.extend(
                (
                    self._processing_key(monitor_id, policy_hash, vinted_item_id),
                    self._seen_key(monitor_id, policy_hash, vinted_item_id),
                    self._detail_retry_key(monitor_id, policy_hash, vinted_item_id),
                )
            )
        retry_args: list[str | float] = []
        for retry in retries_by_id.values():
            retry_args.extend((_serialize_detail_retry(retry), retry.next_attempt_at.timestamp()))
        script = """
        local terminal_count = tonumber(ARGV[4])
        local total_count = tonumber(ARGV[5])
        for index = 1, total_count do
            local processing_key = KEYS[3 + ((index - 1) * 3)]
            local lock_owner = redis.call('GET', processing_key)
            if lock_owner and lock_owner ~= ARGV[1] then
                return -index
            end
        end

        local retry_arg_start = 6 + total_count
        for index = 1, total_count do
            local processing_key = KEYS[3 + ((index - 1) * 3)]
            local seen_key = KEYS[4 + ((index - 1) * 3)]
            local retry_key = KEYS[5 + ((index - 1) * 3)]
            local item_id = ARGV[5 + index]
            local lock_owner = redis.call('GET', processing_key)
            if index <= terminal_count then
                redis.call('SET', seen_key, '1', 'EX', ARGV[2])
                redis.call('ZADD', KEYS[1], ARGV[3], item_id)
                redis.call('DEL', retry_key)
                redis.call('ZREM', KEYS[2], item_id)
            else
                local retry_index = index - terminal_count - 1
                local retry_payload = ARGV[retry_arg_start + (retry_index * 2)]
                local retry_score = ARGV[retry_arg_start + (retry_index * 2) + 1]
                local existing_seen = redis.call('EXISTS', seen_key) == 1
                local existing_retry = redis.call('GET', retry_key)
                if lock_owner == ARGV[1] then
                    redis.call('DEL', seen_key)
                    redis.call('ZREM', KEYS[1], item_id)
                    redis.call('SET', retry_key, retry_payload, 'EX', ARGV[2])
                    redis.call('ZADD', KEYS[2], retry_score, item_id)
                elseif existing_seen then
                    redis.call('DEL', retry_key)
                    redis.call('ZREM', KEYS[2], item_id)
                elseif not existing_retry or existing_retry == retry_payload then
                    redis.call('SET', retry_key, retry_payload, 'EX', ARGV[2])
                    redis.call('ZADD', KEYS[2], retry_score, item_id)
                end
            end
            if lock_owner == ARGV[1] then
                redis.call('DEL', processing_key)
            end
        end
        if terminal_count > 0 then
            redis.call('EXPIRE', KEYS[1], ARGV[2])
        end
        if total_count > terminal_count then
            redis.call('EXPIRE', KEYS[2], ARGV[2])
        end
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
                    len(item_ids),
                    *item_ids,
                    *retry_args,
                )
            )
            if result < 0:
                conflicted_item_id = item_ids[abs(result) - 1]
                raise SeenCacheOwnershipError(
                    f"Candidate {conflicted_item_id} is owned by another worker"
                )
        else:
            self._finalize_candidate_states_without_lua(
                monitor_id,
                policy_hash,
                terminal_ids,
                retries_by_id,
                seen_at=seen_at,
            )
        if terminal_ids:
            self._trim_seen_index(monitor_id, policy_hash)

    def _finalize_candidate_states_without_lua(
        self,
        monitor_id: int,
        policy_hash: str,
        terminal_ids: tuple[str, ...],
        retries_by_id: dict[str, DetailRetryRecord],
        *,
        seen_at: int,
    ) -> None:
        item_ids = [*terminal_ids, *retries_by_id]
        for vinted_item_id in item_ids:
            lock_owner = self.client.get(self._processing_key(monitor_id, policy_hash, vinted_item_id))
            if lock_owner and lock_owner != self.owner_token:
                raise SeenCacheOwnershipError(f"Candidate {vinted_item_id} is owned by another worker")
        seen_index_key = self._seen_index_key(monitor_id, policy_hash)
        retry_index_key = self._detail_retry_index_key(monitor_id, policy_hash)
        pipe = self.client.pipeline(transaction=True)
        for vinted_item_id in terminal_ids:
            pipe.set(self._seen_key(monitor_id, policy_hash, vinted_item_id), "1", ex=self.seen_ttl_seconds)
            pipe.zadd(seen_index_key, {vinted_item_id: seen_at})
            pipe.delete(self._detail_retry_key(monitor_id, policy_hash, vinted_item_id))
            pipe.zrem(retry_index_key, vinted_item_id)
            pipe.delete(self._processing_key(monitor_id, policy_hash, vinted_item_id))
        for vinted_item_id, retry in retries_by_id.items():
            seen_key = self._seen_key(monitor_id, policy_hash, vinted_item_id)
            retry_key = self._detail_retry_key(monitor_id, policy_hash, vinted_item_id)
            lock_owner = self.client.get(self._processing_key(monitor_id, policy_hash, vinted_item_id))
            existing_retry = self.client.get(retry_key)
            if lock_owner == self.owner_token:
                pipe.delete(seen_key)
                pipe.zrem(seen_index_key, vinted_item_id)
                pipe.set(retry_key, _serialize_detail_retry(retry), ex=self.seen_ttl_seconds)
                pipe.zadd(retry_index_key, {vinted_item_id: retry.next_attempt_at.timestamp()})
                pipe.delete(self._processing_key(monitor_id, policy_hash, vinted_item_id))
            elif self.client.exists(seen_key):
                pipe.delete(retry_key)
                pipe.zrem(retry_index_key, vinted_item_id)
            elif existing_retry in {None, _serialize_detail_retry(retry)}:
                pipe.set(retry_key, _serialize_detail_retry(retry), ex=self.seen_ttl_seconds)
                pipe.zadd(retry_index_key, {vinted_item_id: retry.next_attempt_at.timestamp()})
        if terminal_ids:
            pipe.expire(seen_index_key, self.seen_ttl_seconds)
        if retries_by_id:
            pipe.expire(retry_index_key, self.seen_ttl_seconds)
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

    @_translate_redis_errors
    def _remove_detail_retry(self, monitor_id: int, policy_hash: str, vinted_item_id: str) -> None:
        pipe = self.client.pipeline(transaction=True)
        pipe.delete(self._detail_retry_key(monitor_id, policy_hash, vinted_item_id))
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


def serialize_candidate_state_update(update: DetailCandidateStateUpdate) -> dict[str, Any]:
    return {
        "version": DETAIL_RETRY_PAYLOAD_VERSION,
        "terminal_ids": list(dict.fromkeys(update.terminal_ids)),
        "retries": [json.loads(_serialize_detail_retry(retry)) for retry in update.retries],
    }


def deserialize_candidate_state_update(payload: Any) -> DetailCandidateStateUpdate:
    if not isinstance(payload, dict) or payload.get("version") != DETAIL_RETRY_PAYLOAD_VERSION:
        raise ValueError("invalid candidate state transition payload")
    terminal_payload = payload.get("terminal_ids")
    retry_payloads = payload.get("retries")
    if not isinstance(terminal_payload, list) or not isinstance(retry_payloads, list):
        raise ValueError("invalid candidate state transition payload")
    terminal_ids = tuple(str(item_id) for item_id in terminal_payload if str(item_id))
    retries: list[DetailRetryRecord] = []
    for retry_payload in retry_payloads:
        if not isinstance(retry_payload, dict):
            raise ValueError("invalid candidate state transition payload")
        candidate_payload = retry_payload.get("candidate")
        item_id = candidate_payload.get("vinted_item_id") if isinstance(candidate_payload, dict) else None
        retry = _deserialize_detail_retry(json.dumps(retry_payload), expected_item_id=str(item_id or ""))
        if retry is None:
            raise ValueError("invalid candidate state transition payload")
        retries.append(retry)
    return DetailCandidateStateUpdate(terminal_ids=terminal_ids, retries=tuple(retries))


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
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid decimal") from None
    if not parsed.is_finite() or parsed < 0:
        raise ValueError("invalid decimal")
    return parsed


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


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
