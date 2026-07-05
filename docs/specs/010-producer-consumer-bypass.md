# 010 Producer-Consumer Architecture with DataDome Bypass

## Goal

Move scheduled monitor execution from an in-process scheduler/executor to a Redis-backed producer-consumer flow, and make every public Vinted catalog request use the `curl_cffi` browser impersonation stack with per-attempt proxy sticky sessions.

## Scope

- `SchedulerRunner` is a producer: it evaluates active monitors, windows and jitter, then enqueues `MonitorTask` payloads in Redis.
- `TaskConsumer` workers are consumers: they block on the Redis task queue, create a per-attempt browser profile and proxy session, and call `execute_monitor_run()` with a configured provider.
- Manual runs stay synchronous/direct from the API, but use the same `CurlCffiVintedCatalogProvider` stack.
- Existing business logic remains unchanged: Redis seen cache, deduplication, filters, item persistence, opportunities, run events and monitor sessions.

## Interfaces

- Redis task queue: `vinted:task_queue`, `LPUSH` producer and blocking `BRPOP` consumer for FIFO processing.
- HTTP provider: `CurlCffiVintedCatalogProvider` with one `curl_cffi.requests.Session` per task attempt; bootstrap, catalog API and detail fetches share cookies and proxy.
- Browser profiles: coherent `impersonate`, `User-Agent`, `Sec-Ch-Ua*`, ordered bootstrap headers and ordered API headers.
- Proxy sticky sessions: `PROXY_STICKY_USERNAME_TEMPLATE` defaults to `{username}-session-{session_id}`. Use provider-specific values such as `{username}-sessid-{session_id}` for providers that require `sessid`.
- Task payloads include only `proxy_profile_id`; full proxy URLs, usernames, passwords and cookies are never written to Redis or run metadata.
- Runtime metadata: consumer runs include `task_id`, `consumer_id`, `browser_profile`, `proxy_session_id_prefix` and `attempt`; full proxy credentials and cookies are never persisted.

## Acceptance Criteria

- No runtime code imports or depends on `httpx`; provider tests use an injected fake `curl_cffi` session instead of `httpx.MockTransport`.
- Scheduler enqueues due monitors and does not execute HTTP or run business logic directly.
- Consumers generate a new UUID per attempt, inject it into the proxy username through the configured template, and discard the session after the attempt.
- DataDome challenges from bootstrap, catalog API or detail fetch are recorded as run events, fail the current run, bubble to the consumer, and trigger retry with a new browser profile/proxy session until `worker_max_retry_attempts` is exhausted.
- Completed runs are marked successful only when `execute_monitor_run()` returns `success`; failed runs do not reset proxy health.
- Bootstrap and catalog API requests share the same `curl_cffi.Session`, proxy and cookie jar, with a human delay between them.
- Navigation flow is selected per provider instance: Google referral, home navigation, or internal Vinted referral.
- Proxy failures use exponential cooldown; DataDome challenges use the configured challenge penalty multiplier.
- Run events expose safe diagnostics: `browser_profile`, `proxy_session_id_prefix`, `datadome_cookie`, `bootstrap_duration_ms`, attempt number and navigation flow.

## Verification

- `ruff check backend/src backend/alembic`
- `python -m pytest backend/tests/test_vinted_catalog_provider.py backend/tests/test_scheduler.py backend/tests/test_task_queue.py backend/tests/test_proxies.py backend/tests/test_consumer.py backend/tests/test_manual_runs.py`
- Docker smoke: `docker compose ps`, API health, Redis queue drain after scheduled work.
- Optional live diagnostics when credentials/proxies are available: `scripts/check_ja3.py`, `scripts/check_headers.py`, `scripts/check_datadome.py`, `scripts/inspect_vinted_session.py`, and `scripts/compare_fingerprints.py`.
