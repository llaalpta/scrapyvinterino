from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from vinted_monitor.core.config import Settings
from vinted_monitor.providers.catalog import CatalogItemCandidate
from vinted_monitor.services.seen_cache import (
    DetailCandidateStateUpdate,
    DetailRetryRecord,
    RedisSeenCache,
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

    def zrangebyscore(
        self,
        key: str,
        minimum: str | float,
        maximum: str | float,
        *,
        start: int,
        num: int,
    ) -> list[str]:
        lower = float("-inf") if minimum == "-inf" else float(minimum)
        upper = float(maximum)
        members = [
            member
            for member, score in sorted(self.sorted_sets.get(key, {}).items(), key=lambda entry: (entry[1], entry[0]))
            if lower <= score <= upper
        ]
        return members[start : start + num]

    def expire(self, key: str, seconds: int) -> bool:
        if not self.exists(key):
            return False
        self.expirations[key] = seconds
        return True

    def pipeline(self, *, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self, transaction=transaction)


def build_candidate(item_id: str = "9370050898") -> CatalogItemCandidate:
    return CatalogItemCandidate(
        vinted_item_id=item_id,
        title="Camiseta de prueba",
        brand="Marca",
        price_amount=Decimal("3.50"),
        currency="EUR",
        size="M",
        status="Muy bueno",
        seller_login="seller",
        seller_country="ES",
        favorite_count=2,
        url=f"https://www.vinted.es/items/{item_id}-camiseta",
        image_url=f"https://images1.vinted.net/t/{item_id}/f800/01.webp?s=signed",
        view_count=4,
        raw={"cookie": "must-not-be-persisted", "access_token_web": "secret"},
    )


def build_cache(client: FakeRedis) -> RedisSeenCache:
    return RedisSeenCache(
        client=client,
        seen_ttl_seconds=86_400,
        processing_ttl_seconds=120,
        max_per_monitor=10_000,
    )


def retry_record(*, item_id: str = "9370050898", due_at: int = 1_030) -> DetailRetryRecord:
    return DetailRetryRecord(
        candidate=build_candidate(item_id),
        attempt_count=1,
        next_attempt_at=datetime.fromtimestamp(due_at, UTC),
        failure_kind="transport_error",
    )


def test_pending_detail_retry_is_not_reclaimed_from_catalog_and_payload_is_sanitized() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    retry = retry_record()

    cache.finalize_candidate_states(7, "policy", DetailCandidateStateUpdate(retries=(retry,)))

    assert cache.claim_unseen(7, "policy", [retry.candidate.vinted_item_id]) == set()
    retry_key = cache._detail_retry_key(7, "policy", retry.candidate.vinted_item_id)
    payload = json.loads(client.strings[retry_key])
    serialized = client.strings[retry_key]
    assert payload["attempt_count"] == 1
    assert payload["candidate"]["view_count"] == 4
    assert "cookie" not in serialized
    assert "access_token_web" not in serialized
    assert client.expirations[retry_key] == 86_400
    assert client.pipeline_transactions == [True]


def test_due_detail_retry_is_claimed_once_and_rehydrates_normalized_candidate() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    retry = retry_record()
    cache.finalize_candidate_states(7, "policy", DetailCandidateStateUpdate(retries=(retry,)))

    assert cache.claim_due_detail_retries(
        7,
        "policy",
        due_at=datetime.fromtimestamp(1_029, UTC),
        limit=5,
    ) == []

    claimed = cache.claim_due_detail_retries(
        7,
        "policy",
        due_at=datetime.fromtimestamp(1_030, UTC),
        limit=5,
    )
    claimed_again = cache.claim_due_detail_retries(
        7,
        "policy",
        due_at=datetime.fromtimestamp(1_030, UTC),
        limit=5,
    )

    assert len(claimed) == 1
    assert claimed_again == []
    assert claimed[0].candidate.price_amount == Decimal("3.50")
    assert claimed[0].candidate.seller_login == "seller"
    assert claimed[0].candidate.seller_country == "ES"
    assert claimed[0].candidate.view_count == 4
    assert claimed[0].candidate.raw["id"] == retry.candidate.vinted_item_id
    assert claimed[0].candidate.raw["view_count"] == 4
    assert claimed[0].candidate.raw["user"]["login"] == "seller"
    assert "cookie" not in claimed[0].candidate.raw


def test_baseline_and_zero_view_retry_are_isolated_by_policy_hash() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    item_id = "9370050898"
    candidate = replace(build_candidate(item_id), view_count=0)
    retry = DetailRetryRecord(
        candidate=candidate,
        attempt_count=1,
        next_attempt_at=datetime.fromtimestamp(1_000, UTC),
        failure_kind="transport_error",
    )

    cache.mark_baseline(7, "legacy-policy")
    cache.stage_candidate_retries(7, "legacy-policy", (retry,))

    assert cache.has_baseline(7, "legacy-policy") is True
    assert cache.has_baseline(7, "description-only-policy") is False
    assert cache.claim_due_detail_retries(
        7,
        "description-only-policy",
        due_at=datetime.fromtimestamp(1_000, UTC),
        limit=1,
    ) == []

    claimed = cache.claim_due_detail_retries(
        7,
        "legacy-policy",
        due_at=datetime.fromtimestamp(1_000, UTC),
        limit=1,
    )

    assert len(claimed) == 1
    assert claimed[0].candidate.view_count == 0


def test_terminal_transition_marks_seen_and_removes_retry_and_processing_lock() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    retry = retry_record()
    item_id = retry.candidate.vinted_item_id
    cache.finalize_candidate_states(7, "policy", DetailCandidateStateUpdate(retries=(retry,)))
    cache.claim_due_detail_retries(7, "policy", due_at=datetime.fromtimestamp(1_030, UTC), limit=5)

    cache.finalize_candidate_states(7, "policy", DetailCandidateStateUpdate(terminal_ids=(item_id,)))

    assert client.exists(cache._seen_key(7, "policy", item_id))
    assert not client.exists(cache._processing_key(7, "policy", item_id))
    assert not client.exists(cache._detail_retry_key(7, "policy", item_id))
    assert item_id not in client.sorted_sets.get(cache._detail_retry_index_key(7, "policy"), {})
    assert cache.claim_unseen(7, "policy", [item_id]) == set()
    assert client.pipeline_transactions == [True, True]


def test_mark_seen_also_cleans_pending_detail_retry() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    retry = retry_record()
    item_id = retry.candidate.vinted_item_id
    cache.finalize_candidate_states(7, "policy", DetailCandidateStateUpdate(retries=(retry,)))

    cache.mark_seen(7, "policy", [item_id])

    assert client.exists(cache._seen_key(7, "policy", item_id))
    assert not client.exists(cache._detail_retry_key(7, "policy", item_id))


def test_corrupt_due_retry_is_removed_instead_of_reaching_worker() -> None:
    client = FakeRedis()
    cache = build_cache(client)
    item_id = "corrupt"
    retry_key = cache._detail_retry_key(7, "policy", item_id)
    retry_index_key = cache._detail_retry_index_key(7, "policy")
    client.set(retry_key, "not-json", ex=86_400)
    client.zadd(retry_index_key, {item_id: 900})

    claimed = cache.claim_due_detail_retries(7, "policy", due_at=datetime.fromtimestamp(1_000, UTC), limit=5)

    assert claimed == []
    assert not client.exists(retry_key)
    assert item_id not in client.sorted_sets.get(retry_index_key, {})


def test_candidate_cannot_be_terminal_and_retry_in_same_transition() -> None:
    cache = build_cache(FakeRedis())
    retry = retry_record()

    with pytest.raises(ValueError, match="candidate states overlap"):
        cache.finalize_candidate_states(
            7,
            "policy",
            DetailCandidateStateUpdate(terminal_ids=(retry.candidate.vinted_item_id,), retries=(retry,)),
        )


def test_detail_retry_settings_default_to_sequential_three_attempt_policy() -> None:
    settings = Settings(_env_file=None)

    assert settings.vinted_detail_concurrency == 1
    assert settings.vinted_detail_max_attempts == 3
    assert settings.vinted_detail_retry_backoffs_seconds == (30, 120)

    with pytest.raises(ValidationError, match="one delay per retry"):
        Settings(
            _env_file=None,
            vinted_detail_max_attempts=3,
            vinted_detail_retry_backoffs_seconds=(30,),
        )
