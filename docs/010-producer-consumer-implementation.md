# 010 Producer-Consumer Implementation Notes

This note records implementation-specific decisions for `docs/specs/010-producer-consumer-bypass.md`. The spec remains the source of truth for behavior and acceptance criteria.

## Current State

- Scheduler is a producer and enqueues `MonitorTask` payloads to Redis with `LPUSH`.
- Consumers block with `BRPOP`, create a browser profile plus per-attempt proxy sticky session, then execute monitor business logic through `execute_monitor_run()`.
- `CurlCffiVintedCatalogProvider` is the only Vinted catalog HTTP provider.
- Manual runs remain synchronous from the API, but use the same provider stack.
- Root-level `audit_010_producer_consumer.md` was removed to avoid duplicate planning docs.

## Decisions

- Use `PROXY_STICKY_USERNAME_TEMPLATE` for provider-specific sticky formats. Default: `{username}-session-{session_id}`.
- For providers that require `sessid`, configure `{username}-sessid-{session_id}`.
- Store only `proxy_session_id_prefix` in runtime metadata and events; do not persist full proxy URLs, credentials, cookies or raw DataDome values.
- Redis task payloads carry `proxy_profile_id` only; the consumer resolves the profile and builds the sticky URL inside the attempt.
- Treat `403` and `429` from Vinted as DataDome-style challenge responses for retry purposes.
- Keep retry escalation in `TaskConsumer`; `execute_monitor_run()` records the failed run and re-raises `DataDomeChallengeError`.

## Verification Evidence

- `ruff check backend/src backend/alembic`
- `python -m pytest backend/tests/test_vinted_catalog_provider.py backend/tests/test_scheduler.py backend/tests/test_task_queue.py backend/tests/test_proxies.py backend/tests/test_consumer.py backend/tests/test_manual_runs.py`
- `docker compose up -d --build api worker`
- `docker compose ps`
- `GET http://localhost:8000/health`
- `docker compose exec -T worker python -c "import curl_cffi; print(curl_cffi.__version__)"`

The roadmap item remains `in-progress` until live Vinted/proxy diagnostics are run with the chosen provider and current Vinted response behavior.

## Audit 2026-07-05

- Backend checks passed: `ruff check backend/src backend/alembic`, focused producer/consumer pytest suite (`64 passed`), Docker service status, API health, frontend HTTP smoke, Redis task queue length `0`, and no `processing:*` keys.
- DataDome diagnostics passed on direct egress with `chrome136`: `scripts/check_headers.py`, `scripts/check_ja3.py`, and `scripts/check_datadome.py --url "https://www.vinted.es/catalog?search_text=nike"`. The smoke flow returned bootstrap `200`, catalog API `200`, no challenge, and 5 catalog items.
- `scripts/check_datadome.py` now uses `build_catalog_api_params()` so diagnostics exercise the same public catalog URL-to-API parameter mapping as `CurlCffiVintedCatalogProvider`.
- `scripts/compare_fingerprints.py` is intentionally pending until `scripts/inspect_vinted_session.py` captures a local browser reference at `scripts/browser_reference.json`.
- Playwright QA against the running PWA passed for desktop navigation, mobile navigation, invalid monitor URL rejection (`422` with no persisted row), valid monitor creation, API visibility, UI archive flow, and DB archive state for temporary monitor `950`.
- No residential proxy credentials were configured for this audit. The item remains `in-progress` until the same diagnostics pass through the chosen residential/sticky proxy provider and any DataDome challenge behavior is observed with that egress.
