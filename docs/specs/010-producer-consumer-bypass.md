# 010 Producer-Consumer Architecture with DataDome Bypass

## Goal

Move scheduled monitor execution from an in-process scheduler/executor to a Redis-backed producer-consumer flow, and make every public Vinted catalog request use the `curl_cffi` browser impersonation stack with per-attempt proxy sticky sessions.

## Scope

- `SchedulerRunner` is a producer: it evaluates active monitors, windows and jitter, then enqueues `MonitorTask` payloads in Redis.
- `TaskConsumer` workers are consumers: they block on the Redis task queue, use the configured browser profile, create a per-attempt proxy session, and call `execute_monitor_run()` with a configured provider.
- Manual runs stay synchronous from the API, but use the same egress selection and `CurlCffiVintedCatalogProvider` stack as scheduled runs.
- Existing business logic remains unchanged: Redis seen cache, deduplication, filters, item persistence, opportunities, run events and monitor sessions.

## Interfaces

- Redis task queue: `vinted:task_queue`, `LPUSH` producer and blocking `BRPOP` consumer for FIFO processing.
- HTTP provider: `CurlCffiVintedCatalogProvider` with one `curl_cffi.requests.Session` per task attempt; catalog-document bootstrap, catalog API and detail fetches share cookies and proxy.
- Browser profiles: coherent `impersonate`, `User-Agent`, `Sec-Ch-Ua*`, ordered bootstrap headers and ordered API headers. The default runtime profile is Chrome 146, matching the current HAR-derived catalog flow; the Chrome 120 ephemeral client remains only the pre-integration fingerprint gate.
- Pre-integration fingerprint gate: `scripts/verify_impersonation.py` uses the reusable ephemeral HTTP client with `impersonate="chrome120"` against public echo services before worker integration.
- Proxy sticky sessions: `PROXY_STICKY_USERNAME_TEMPLATE` defaults to `{username}-session-{session_id}`. Use provider-specific values such as `{username}-sessid-{session_id}` for providers that require `sessid`.
- Worker retry attempts are deployment-owned through `WORKER_MAX_RETRY_ATTEMPTS`; the PWA does not expose request retry controls for producer-consumer runs.
- Task payloads include only `proxy_profile_id`; full proxy URLs, usernames, passwords and cookies are never written to Redis or run metadata.
- Runtime metadata: consumer runs include `task_id`, `consumer_id`, `browser_profile`, `proxy_session_id_prefix` and `attempt`; full proxy credentials and cookies are never persisted.
- Deployment safety gate: public Vinted catalog traffic is not sent directly from the host unless `VINTED_DIRECT_CATALOG_ENABLED=true`; the default is false.
- Geo context: Vinted ES catalog runs require an ES proxy profile by default. Proxy profiles declare country only; locale, `Accept-Language`, and screen are resolved from internal country/domain presets and stored as read-only diagnostics.

## Acceptance Criteria

- No runtime code imports or depends on `httpx`; provider tests use an injected fake `curl_cffi` session instead of `httpx.MockTransport`.
- Scheduler enqueues due monitors and does not execute HTTP or run business logic directly.
- Consumers generate a new UUID per attempt, inject it into the proxy username through the configured template, and discard the session after the attempt.
- DataDome challenges from bootstrap, catalog API or detail fetch are recorded as run events, fail the current run, bubble to the consumer, and trigger retry with the same configured browser profile plus a new proxy session UUID until `worker_max_retry_attempts` is exhausted.
- Completed runs are marked successful only when `execute_monitor_run()` returns `success`; failed runs do not reset proxy health.
- Bootstrap and catalog API requests share the same `curl_cffi.Session`, proxy and cookie jar, with a human delay between them.
- Bootstrap always uses the saved public catalog document URL for the monitor, extracts CSRF/anon/session markers into memory, and then calls the JSON catalog API with the same referer and session context.
- Proxy failures use exponential cooldown; DataDome challenges use the configured challenge penalty multiplier.
- Run events expose safe diagnostics: `browser_profile`, `impersonate`, `proxy_session_id_prefix`, target/proxy country, locale, `Accept-Language`, screen, `datadome_cookie`, `bootstrap_duration_ms`, attempt number, `bootstrap_origin=catalog_document`, CSRF/anon/access-token/v_udt presence booleans, and masked/fingerprinted session markers only.
- Before calling `/api/v2/catalog/items`, the provider must have a complete conservative session context: Chrome `curl_cffi` impersonation, same-session egress diagnostic matching the target country, CSRF token, anon id, `access_token_web`, DataDome cookie, `v_udt`, locale, `Accept-Language`, and screen. Missing or contradictory context records `catalog_session_context_incomplete` and blocks the catalog API request.
- Proxy create/update APIs reject legacy manual `locale`, `Accept-Language`, and screen fields; changing `country_code` recomputes the stored read-only context.
- Before wiring a new ephemeral HTTP client into Redis workers or live Vinted catalog traffic, `scripts/verify_impersonation.py` exits successfully with exact Chrome 120 `User-Agent`, `sec-ch-ua`, and `Accept-Encoding` echoes, no Python/curl/cffi/requests leak markers in header values or non-browser header names, and expected BrowserLeaks TLS 1.3 / HTTP/2 fields. The standard Chrome header name `Upgrade-Insecure-Requests` is allowed.
- Manual and worker-owned catalog runs use the configured browser profile; the default is `chrome146`. Direct runs without a proxy are blocked by default and are available only when explicitly enabled by deployment configuration.
- Runtime browser profiles define ordered request headers for bootstrap and catalog API calls, but do not force hop-by-hop headers such as `Connection` or `TE`; HTTP/2 pseudo-header order and SETTINGS are owned by `curl_cffi` impersonation.

## Verification

- `ruff check backend/src backend/alembic`
- `python -m pytest backend/tests/test_vinted_catalog_provider.py backend/tests/test_scheduler.py backend/tests/test_task_queue.py backend/tests/test_proxies.py backend/tests/test_consumer.py backend/tests/test_manual_runs.py backend/tests/test_ephemeral_http.py backend/tests/test_verify_impersonation_script.py`
- `python scripts/verify_impersonation.py`
- Docker smoke: `docker compose ps`, API health, Redis queue drain after scheduled work.
- Optional live diagnostics when credentials/proxies are available: `scripts/check_ja3.py`, `scripts/check_headers.py`, `scripts/check_datadome.py`, `scripts/inspect_vinted_session.py`, and `scripts/compare_fingerprints.py`.
