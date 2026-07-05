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
- Asocks is treated as ephemeral sticky-by-username egress: each task attempt gets a fresh UUID in the proxy username, and the UUID is discarded after the attempt.
- Do not call the Asocks refresh API from runtime scraping code; rotation is achieved by using a new session UUID per attempt.
- The pre-integration HTTP fingerprint gate uses Chrome 120 exactly: `curl_cffi.requests.Session(impersonate="chrome120")` plus matching Chrome 120 `User-Agent` and `sec-ch-ua` headers.
- Store only `proxy_session_id_prefix` in runtime metadata and events; do not persist full proxy URLs, credentials, cookies or raw DataDome values.
- Redis task payloads carry `proxy_profile_id` only; the consumer resolves the profile and builds the sticky URL inside the attempt.
- Treat `403` and `429` from Vinted as DataDome-style challenge responses for retry purposes.
- Keep retry escalation in `TaskConsumer`; `execute_monitor_run()` records the failed run and re-raises `DataDomeChallengeError`.

## Pre-Integration Impersonation Plan

Before connecting the new ephemeral HTTP client to Redis workers or sending live Vinted catalog traffic, run an independent fingerprint gate through public echo services only.

- Add `EphemeralVintedHttpClient` as a small reusable transport wrapper that creates exactly one `curl_cffi.requests.Session(impersonate="chrome120")` with optional proxy URL.
- Export the Chrome 120 constants and header builder used by the client so diagnostics and runtime code validate the same fingerprint inputs.
- Add `scripts/verify_impersonation.py` to call `https://httpbin.org/headers` and `https://tls.browserleaks.com/json` with the same client.
- The script must fail non-zero if echoed headers do not match Chrome 120 exactly (`User-Agent`, `sec-ch-ua`, and `Accept-Encoding`), if header values or non-browser header names contain `python`, `curl`, `cffi` or `requests`, or if BrowserLeaks omits expected TLS 1.3 / HTTP/2 fields. The standard Chrome header name `Upgrade-Insecure-Requests` is allowed.
- Keep proxy optional through `--proxy-url` or `VERIFY_PROXY_URL`; the script must be safe to run before Asocks credentials are configured.
- This gate does not call Vinted and does not touch Redis, database state, scheduler state, or worker queues.

## Verification Evidence

- `ruff check backend/src backend/alembic`
- `python -m pytest backend/tests/test_vinted_catalog_provider.py backend/tests/test_scheduler.py backend/tests/test_task_queue.py backend/tests/test_proxies.py backend/tests/test_consumer.py backend/tests/test_manual_runs.py backend/tests/test_ephemeral_http.py backend/tests/test_verify_impersonation_script.py`
- `docker compose up -d --build api worker`
- `docker compose ps`
- `GET http://localhost:8000/health`
- `docker compose exec -T worker python -c "import curl_cffi; print(curl_cffi.__version__)"`
- `python scripts/verify_impersonation.py`

The roadmap item remains `in-progress` until live Vinted/proxy diagnostics are run with the chosen provider and current Vinted response behavior.

## Audit 2026-07-05

- Backend checks passed: `ruff check backend/src backend/alembic`, focused producer/consumer pytest suite (`64 passed`), Docker service status, API health, frontend HTTP smoke, Redis task queue length `0`, and no `processing:*` keys.
- DataDome diagnostics passed on direct egress with `chrome136`: `scripts/check_headers.py`, `scripts/check_ja3.py`, and `scripts/check_datadome.py --url "https://www.vinted.es/catalog?search_text=nike"`. The smoke flow returned bootstrap `200`, catalog API `200`, no challenge, and 5 catalog items.
- `scripts/check_datadome.py` now uses `build_catalog_api_params()` so diagnostics exercise the same public catalog URL-to-API parameter mapping as `CurlCffiVintedCatalogProvider`.
- `scripts/compare_fingerprints.py` is intentionally pending until `scripts/inspect_vinted_session.py` captures a local browser reference at `scripts/browser_reference.json`.
- Playwright QA against the running PWA passed for desktop navigation, mobile navigation, invalid monitor URL rejection (`422` with no persisted row), valid monitor creation, API visibility, UI archive flow, and DB archive state for temporary monitor `950`.
- No residential proxy credentials were configured for this audit. The item remains `in-progress` until the same diagnostics pass through the chosen residential/sticky proxy provider and any DataDome challenge behavior is observed with that egress.

## Chrome 120 Preflight 2026-07-05

- Added `EphemeralVintedHttpClient` as an isolated Chrome 120 transport wrapper for pre-integration diagnostics only.
- Added `scripts/verify_impersonation.py` to validate headers through `https://httpbin.org/headers` and TLS/browser fields through `https://tls.browserleaks.com/json` before touching Vinted.
- Direct egress preflight passed with exact Chrome 120 `User-Agent`, exact Chrome 120 `sec-ch-ua`, exact Chrome 120 `Accept-Encoding`, no forbidden leak markers in header values or non-browser header names, BrowserLeaks `ja3_hash`, and BrowserLeaks `ja4` containing TLS 1.3 plus HTTP/2 markers.
- The standard browser header name `Upgrade-Insecure-Requests` is intentionally allowed even though it contains the substring `requests`.
- Focused checks passed: `ruff check src alembic tests/test_ephemeral_http.py tests/test_verify_impersonation_script.py ..\scripts\verify_impersonation.py`, `python -m pytest tests/test_ephemeral_http.py tests/test_verify_impersonation_script.py`, `python -m py_compile scripts/verify_impersonation.py`, and `python scripts/verify_impersonation.py`.
- The broader 010 pytest command was attempted from the Windows host and failed because the host cannot resolve Docker service hostname `postgres`; run it from a Docker-networked environment or override `DATABASE_URL` to `localhost` when a disposable test database is available.
- Asocks/sticky proxy preflight remains pending until credentials are configured; run `python scripts/verify_impersonation.py --proxy-url "<sticky proxy url>"`.
