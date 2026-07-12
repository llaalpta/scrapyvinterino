import hashlib
import json
from types import SimpleNamespace

from vinted_monitor.db.models import SearchSource
from vinted_monitor.services.filters import monitor_filter_snapshot
from vinted_monitor.services.runs import (
    EVALUATION_CONTRACT_VERSION,
    _run_runtime_metadata,
    monitor_baseline_ready,
    monitor_policy_hash,
)
from vinted_monitor.services.scheduler import RunEgress


class PolicyBaselineCache:
    def __init__(self, baselines: set[tuple[int, str]]) -> None:
        self.baselines = baselines

    def has_baseline(self, monitor_id: int, policy_hash: str) -> bool:
        return (monitor_id, policy_hash) in self.baselines


def build_source() -> SearchSource:
    return SearchSource(
        id=41,
        name="Policy contract monitor",
        url="https://www.vinted.es/catalog?order=newest_first",
        normalized_query={"order": ["newest_first"]},
        filter_definition={"blacklist_terms": ["prohibido"]},
    )


def legacy_policy_hash(source: SearchSource) -> str:
    payload = {
        "url": source.url,
        "normalized_query": source.normalized_query or {},
        "filters": monitor_filter_snapshot(source.filter_definition),
    }
    serialized = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]


def test_description_only_contract_invalidates_legacy_baseline() -> None:
    source = build_source()
    old_hash = legacy_policy_hash(source)
    current_hash = monitor_policy_hash(source)

    ready, observed_hash = monitor_baseline_ready(source, PolicyBaselineCache({(source.id, old_hash)}))

    assert current_hash != old_hash
    assert observed_hash == current_hash
    assert ready is False


def test_runtime_metadata_identifies_description_only_contract() -> None:
    source = build_source()
    runtime_config = SimpleNamespace(
        catalog_per_page=5,
        detail_max_candidates_per_run=5,
        request_timeout_ms=20_000,
        proxy_cooldown_minutes=10,
        stop_monitor_after_consecutive_failures=3,
    )

    metadata = _run_runtime_metadata(source, RunEgress(mode="direct"), runtime_config)

    assert metadata["evaluation_contract"] == EVALUATION_CONTRACT_VERSION == "description_only_v2"
