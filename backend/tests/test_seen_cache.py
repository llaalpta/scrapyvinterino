from __future__ import annotations

import pytest

from vinted_monitor.services.seen_cache import (
    DetailCandidateStateUpdate,
    RedisSeenCache,
    SeenCacheOwnershipError,
    deserialize_candidate_state_update,
    serialize_candidate_state_update,
)


class FakePipeline:
    def __init__(self, client: FakeRedis, *, transaction: bool) -> None:
        self.client = client
        self.transaction = transaction
        self.commands: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def enqueue(*args, **kwargs):
            self.commands.append((name, args, kwargs))
            return self

        return enqueue

    def execute(self) -> list:
        self.client.pipeline_transactions.append(self.transaction)
        return [getattr(self.client, name)(*args, **kwargs) for name, args, kwargs in self.commands]


class FakeRedis:
    def __init__(self, *, now: int = 1_000) -> None:
        self.now = now
        self.strings: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}
        self.pipeline_transactions: list[bool] = []

    def ping(self) -> bool:
        return True

    def exists(self, key: str) -> int:
        return int(key in self.strings or key in self.sorted_sets)

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.strings:
            return False
        self.strings[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    def get(self, key: str) -> str | None:
        return self.strings.get(key)

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += int(key in self.strings or key in self.sorted_sets)
            self.strings.pop(key, None)
            self.sorted_sets.pop(key, None)
            self.expirations.pop(key, None)
        return removed

    def time(self) -> tuple[int, int]:
        return self.now, 0

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        target = self.sorted_sets.setdefault(key, {})
        added = sum(member not in target for member in mapping)
        target.update({member: float(score) for member, score in mapping.items()})
        return added

    def zrem(self, key: str, *members: str) -> int:
        target = self.sorted_sets.get(key, {})
        removed = 0
        for member in members:
            removed += int(member in target)
            target.pop(member, None)
        if not target:
            self.sorted_sets.pop(key, None)
        return removed

    def zcard(self, key: str) -> int:
        return len(self.sorted_sets.get(key, {}))

    def zrange(self, key: str, start: int, end: int) -> list[str]:
        ordered = sorted(self.sorted_sets.get(key, {}), key=lambda member: (self.sorted_sets[key][member], member))
        stop = len(ordered) if end == -1 else end + 1
        return ordered[start:stop]

    def expire(self, key: str, seconds: int) -> bool:
        if not self.exists(key):
            return False
        self.expirations[key] = seconds
        return True

    def pipeline(self, *, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self, transaction=transaction)


def build_cache(client: FakeRedis, *, owner_token: str = "owner-a") -> RedisSeenCache:
    return RedisSeenCache(
        client=client,
        seen_ttl_seconds=86_400,
        processing_ttl_seconds=120,
        max_per_monitor=10_000,
        owner_token=owner_token,
    )


def test_claim_unseen_uses_only_seen_and_short_processing_state() -> None:
    client = FakeRedis()
    cache = build_cache(client)

    assert cache.claim_unseen(7, "policy", ["1", "1", "2"]) == {"1", "2"}
    assert cache.claim_unseen(7, "policy", ["1", "2"]) == set()
    assert client.expirations[cache._processing_key(7, "policy", "1")] == 120

    cache.release_processing(7, "policy", ["1"])

    assert cache.claim_unseen(7, "policy", ["1"]) == {"1"}


def test_terminal_transition_marks_seen_and_releases_owned_processing_lock() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    assert cache.claim_unseen(7, "policy", ["1"]) == {"1"}

    cache.finalize_candidate_states(7, "policy", DetailCandidateStateUpdate(terminal_ids=("1", "1")))

    assert client.exists(cache._seen_key(7, "policy", "1"))
    assert not client.exists(cache._processing_key(7, "policy", "1"))
    assert cache.claim_unseen(7, "policy", ["1"]) == set()
    assert client.pipeline_transactions == [True]


def test_terminal_transition_rejects_a_foreign_processing_owner() -> None:
    client = FakeRedis()
    first = build_cache(client, owner_token="owner-a")
    second = build_cache(client, owner_token="owner-b")
    assert first.claim_unseen(7, "policy", ["1"]) == {"1"}

    with pytest.raises(SeenCacheOwnershipError, match="owned by another worker"):
        second.finalize_candidate_states(7, "policy", DetailCandidateStateUpdate(terminal_ids=("1",)))

    assert not client.exists(first._seen_key(7, "policy", "1"))
    assert client.get(first._processing_key(7, "policy", "1")) == "owner-a"


def test_baseline_isolated_by_policy_hash_and_marks_catalog_ids_seen() -> None:
    cache = build_cache(FakeRedis())

    cache.mark_seen(7, "policy-a", ["1", "2"])
    cache.mark_baseline(7, "policy-a")

    assert cache.has_baseline(7, "policy-a") is True
    assert cache.has_baseline(7, "policy-b") is False
    assert cache.claim_unseen(7, "policy-a", ["1", "2"]) == set()
    assert cache.claim_unseen(7, "policy-b", ["1", "2"]) == {"1", "2"}


def test_candidate_transition_serialization_has_no_deferred_payload() -> None:
    update = DetailCandidateStateUpdate(terminal_ids=("1", "1", "2"))

    payload = serialize_candidate_state_update(update)

    assert payload == {"version": 2, "terminal_ids": ["1", "2"]}
    assert deserialize_candidate_state_update(payload) == DetailCandidateStateUpdate(terminal_ids=("1", "2"))
    with pytest.raises(ValueError, match="invalid candidate state transition payload"):
        deserialize_candidate_state_update({"version": 1, "terminal_ids": [], "retries": []})
