# 010 Producer-Consumer Implementation Notes

This note records implementation-specific decisions for `docs/specs/010-producer-consumer-bypass.md`. The spec remains the source of truth for behavior and acceptance criteria.

## Current State

- Scheduler atomically enqueues `MonitorTask` payloads with `LPUSH`, one pending marker per monitor and a payload reverse marker, coalescing later ticks while work is queued or reserved and counting backlog against global/proxy capacity.
- Consumers use a binary queue client and reserve FIFO work with `BLMOVE` into per-consumer processing lists; its socket timeout exceeds the blocking window. Terminal outcomes ACK the exact payload, unexpected failures requeue it, malformed/non-UTF-8 payloads go to dead-letter, and startup/thread recovery restores unacknowledged reservations. Maintenance transitions retry with backoff and are idempotent after ambiguous responses.
- `CurlCffiVintedCatalogProvider` is the only Vinted catalog HTTP provider.
- Runtime catalog traffic is monitor-owned: the run reuses a ready `vinted_sessions` row for that monitor/proxy sticky identity or prepares one automatically from the saved catalog document URL before scraping.
- Manual runs remain synchronous from the API, but use the same provider stack.
- Root-level `audit_010_producer_consumer.md` was removed to avoid duplicate planning docs.
- Item enrichment uses the public item document, structural Next/React Flight records and JSON-LD fallback. The production flow and visible detail probe no longer call the direct `/api/v2/items/{id}/details` matrix.
- Detail work is serial by default per prepared session. An explicit canary can schedule two isolated persistent lanes, but promotion requires measured speedup plus a valid final cookie context. Recoverable candidates survive outside the top-five window in Redis for three total attempts (`30s`, `120s`); only terminal outcomes become seen.
- The PWA persists no image bytes: it renders every signed `images*.vinted.net` URL directly and exposes an accessible gallery plus public availability/price breakdown while purchase remains disabled.

## Decisions

- Use `PROXY_STICKY_USERNAME_TEMPLATE` for provider-specific sticky formats. Default: `{username}-session-{session_id}`.
- For providers that require `sessid`, configure `{username}-sessid-{session_id}`.
- Residential proxy sticky identities are tied to prepared Vinted sessions for a monitor, not to a one-off task attempt.
- Do not call the Asocks refresh API from runtime scraping code; rotation is achieved by using a new session UUID per attempt.
- The pre-integration HTTP fingerprint gate uses Chrome 120 exactly: `curl_cffi.requests.Session(impersonate="chrome120")` plus matching Chrome 120 `User-Agent` and `sec-ch-ua` headers.
- Runtime catalog providers select the configured browser profile; default runtime impersonation is `chrome146`. Direct no-proxy runs remain disabled unless explicitly enabled for diagnostics.
- Store only `proxy_session_id_prefix` in runtime metadata and events; do not persist full proxy URLs, credentials, cookies or raw DataDome values.
- Redis task payloads carry `proxy_profile_id` only; the consumer/runtime resolves the profile and reuses or prepares the monitor-owned sticky session inside the attempt.
- Run rows persist indexed `task_id`. Redelivery acknowledges an existing terminal run without Vinted traffic, reconciles `finalizing`, and closes an orphan `running` row before retrying.
- Development Redis uses a persisted AOF volume. The recovery contract assumes one worker service instance with multiple in-process consumers; horizontally scaled workers require distributed reservation ownership before deployment.
- Classify DataDome and `cf-mitigated: challenge` explicitly; plain `429` remains rate limiting and detail `404/410` is terminal.
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

The roadmap item is `done` after the 2026-07-11 residential proxy, reliable queue, public-detail and PWA audit recorded below.

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
- The broader 010 pytest command passed from the Windows host after overriding service URLs for host access: `DATABASE_URL=postgresql+psycopg://vinted:vinted@localhost:5432/vinted_monitor` and `REDIS_URL=redis://localhost:6379/0`; result: `79 passed`.
- Asocks/sticky proxy preflight remains pending until credentials are configured; run `python scripts/verify_impersonation.py --proxy-url "<sticky proxy url>"`.

## Chrome 120 Runtime Direct Validation 2026-07-06

- At that checkpoint, `chrome_120_win10` was added as the then-default runtime browser profile and the example environment was aligned with Chrome 120.
- `CurlCffiVintedCatalogProvider`, worker-owned runs, manual owned-provider runs, and diagnostics now use the configured browser profile instead of random profile selection by default.
- Direct no-proxy validation passed through the API: temporary manual monitor `1106`, run `900`, status `success`, `items_found=5`, `items_new=5`, `opportunities_created=5`, `browser_profile=chrome_120_win10`, and 25 safe run events. The temporary monitor was archived after the check.
- Direct Vinted smoke passed with `scripts/check_datadome.py --url "https://www.vinted.es/catalog?search_text=nike"` using `chrome_120_win10`; bootstrap `200`, catalog API `200`, no DataDome challenge, and 5 items returned.
- Verification passed: `ruff check`, focused Chrome/runtime tests (`31 passed`), full 010 pytest suite with host DB/Redis URLs (`80 passed`), and `python scripts/verify_impersonation.py`.
- Asocks/sticky proxy validation remains pending and is the next blocker before marking 010 `done`.

## Chrome 146 Catalog-Document Runtime 2026-07-07

- Runtime defaults use `chrome146`, the highest Chrome target supported by the installed `curl_cffi` build and the profile aligned with the valid Chrome 146 catalog HAR. A later Chrome 149 browser HAR remains research input only until the dependency supports that impersonation target. Vinted bootstrap/API/collector requests use explicit ordered lowercase headers with `default_headers=False`; curl_cffi still owns TLS/HTTP2 impersonation and the session cookie jar.
- The provider no longer bootstraps against the base Vinted domain. Each run uses the monitor's saved `/catalog?...` URL as the document bootstrap, then calls `/api/v2/catalog/items` with the same session, cookies, proxy identity, referer, CSRF token and anon id when those markers are present.
- Proxy profiles now accept only connection data and country as user input. Locale, `Accept-Language`, and screen are resolved from internal country presets, stored for diagnostics, and rejected if sent through legacy create/update payloads. The ES preset uses `locale=es-ES` with the observed Chrome 146 HAR `Accept-Language` value `en-GB,en;q=0.9`.
- Empty `search_by_image_uuid` and `search_by_image_id` URL parameters are accepted and ignored; non-empty values remain unsupported because image-search filters are not translated to the fast API.
- Run events record `bootstrap_origin=catalog_document`, CSRF/anon presence booleans, and safe markers only. Raw cookies, CSRF values, anon ids and Vinted session tokens remain memory-only.

## Chrome 146 Runtime Correction 2026-07-09

- The attempted `chrome149` runtime profile was removed because the installed `curl_cffi` build rejects live requests with `Impersonating chrome149 is not supported`.
- `profile_for_impersonate()` now validates configured runtime targets against the installed `curl_cffi` impersonation literals before a proxy test, session prepare, or run reaches network I/O.
- Migration `0010_chrome146_runtime_profile` updates existing ES proxy context rows to `en-GB,en;q=0.9` and invalidates ready pre-production sessions that used `chrome149`; `0011_normalize_vinted_session_invalid_status` normalizes any already-migrated `invalidated` rows back to the canonical `invalid` status.
- The DataDome collector keeps the HAR-shaped `ch` then `le` sequence and does not stop after a `ch` cookie; the final returned cookie is kept in the same `curl_cffi.Session` cookie jar.

## Detail Probe Session Hardening 2026-07-09

- The item detail document probe reuses or prepares the monitor-owned Vinted session through the same proxy sticky identity and Chrome 146 runtime profile as catalog runs.
- A detail probe records a `detail_probe` audit run, emits `detail_probe_finished` and `run_succeeded` on terminal success, and remains excluded from monitor metrics, item persistence, opportunities and Redis seen state.
- A DataDome challenge during the detail probe invalidates the prepared Vinted session and records safe diagnostics without silently rotating into an unprepared proxy identity.
- Host verification passed with service URLs overridden for Windows host access: `ruff check backend/src backend/alembic` and the focused 010 pytest suite (`144 passed`).

## DataDome Key and Detail HTML Runtime 2026-07-09

- The mitmproxy spike showed Chrome loads `static-assets.vinted.com/datadome/5.7.0/tags.js`, posts to `dd.vinted.lt/js`, and receives a `.vinted.es` `datadome` cookie. The DataDome client key is exposed in Vinted HTML as `DATADOME_CLIENT_SIDE_KEY`, so the collector now extracts that marker before falling back to script diagnostics.
- Live headful Chrome plus sticky residential proxy obtained `datadome`, `__cf_bm`, `v_sid`, `_vinted_fr_session`, CSRF, anon id and Vinted tokens, but `/api/v2/items/{id}/details` still returned `403`. That direct endpoint was an earlier research diagnostic and has been removed from runtime and PWA probes.
- Business runs require the public `/items/...` HTML/Next detail document before creating opportunities. Recoverable failures stay pending in Redis; configured incompleteness, genuine `404/410`, blacklist decisions and exhausted retries are terminal. Anti-bot challenges fail the run, invalidate the prepared session and preserve the rolled-back batch.

## Prepared Session Hardening 2026-07-09

- A monitor-owned Vinted session is reusable only after strict context is present: CSRF token, anon id, `access_token_web`, `v_udt`, `__cf_bm`, `datadome`, target country match, locale, `Accept-Language`, viewport and `x-screen=catalog`.
- Session preparation may still call the catalog API probe after a failed DataDome collector to collect diagnostics, but a successful JSON probe no longer overrides missing `datadome` or `__cf_bm`; the saved row remains `incomplete` and the run fails clearly.
- Runtime provider selection, explicit `Preparar sesion`, silent context refreshes and item detail probes now all use the same strict prepared-session requirement.
- The provider receives the configured human pacing bounds from settings instead of hardcoded defaults.
- Recalibrating the initial catalog snapshot reuses the accepted JSON payload from the preparation probe when the run had to prepare a new session, avoiding an immediate duplicate catalog API request. The explicit `Preparar sesion` action remains non-business: it does not touch Redis seen state, baseline snapshots, items, opportunities or monitor metrics.

## Vinted API Kit Detail Research 2026-07-09

- Reviewed `https://github.com/vlymar1/vinted-api-kit` at commit `90a5655` (`2026-06-06`, release `v1.0.1`) as a reference for direct Vinted item detail extraction.
- The package does not use a hidden or alternate item detail endpoint. `ItemsAPI.get_details()` extracts the numeric item id from `/items/{id}-...` and calls `GET {base_url}/api/v2/items/{id}/details`, then reads `response.json()["item"]`.
- Its HTTP layer uses `curl_cffi.AsyncSession`, optional proxy configuration, cookie persistence, `Accept-Language` derived from the Vinted domain, and a cookie refresh path using `HEAD {base_url}` with `impersonate="chrome"`.
- Its default application headers are minimal: `Cache-Control: max-age=0`, `DNT: 1`, and `X-Money-Object: true`. It does not implement HAR-shaped header ordering, explicit CSRF/anon headers, DataDome collection, Chrome 146 coupling, item-document warmup, or a detail endpoint matrix.
- Its tests mock the detail response and therefore prove wrapper behavior only; they do not demonstrate that `/api/v2/items/{id}/details` currently works against live Vinted through Cloudflare/DataDome.
- Historical takeaway: the reference confirmed the common direct endpoint but did not unlock it. This direction is superseded by the 2026-07-11 catalog-to-item HAR, which proves that the public item document embeds the required data.

## Live Detail Probe Findings 2026-07-09

- Controlled live probes prepared one monitor-owned Chrome 146 sticky residential session and then tested two item detail references. Session preparation produced a reusable catalog session with CSRF, anon id, `access_token_web`, `v_udt`, `__cf_bm`, and a collected `datadome` cookie.
- The item document warmup returned `200` and the item-context DataDome collector could also obtain or reuse a `datadome` cookie in the same session.
- Control/support endpoints behaved differently from the detail endpoint: `/api/v2/info_banners/item` returned JSON, `/api/v2/items/{id}/services` returned JSON, and `/api/v2/items/{id}/more` returned `400`.
- The direct detail endpoint `/api/v2/items/{id}/details` remained blocked with `403` and `cf-mitigated: challenge` even after item-document warmup, DataDome presence, item referer, catalog referer, and Chrome 146 Client Hints variants.
- Superseded conclusion: DataDome remains part of the prepared-session context, but direct-detail request batteries are no longer product work. The supported detail contract is the public document parser used by both business runs and the operator probe.

## Continuous Direct Scheduler Validation 2026-07-06

- Local `.env` was cleaned for runtime testing: removed legacy `VINTED_PROXY_ENABLED`, `VINTED_PROXY_URL`, and `VINTED_USER_AGENT`; kept runtime browser identity on the then-current Chrome 120 profile.
- Scheduler and detailed process logs were enabled locally with `SCHEDULER_ENABLED=true` and `LOG_LEVEL=DEBUG`. Because Docker Compose does not refresh container environment on `restart`, `api` and `worker` had to be recreated once with `docker compose up -d --force-recreate api worker`.
- `GET /api/scheduler` after recreation returned `runtime_enabled=true`, `effective_enabled=true`, `allow_direct_without_proxy=true`, `direct_capacity=1`, and `effective_capacity=1`.
- Temporary continuous monitor `1107` used `https://www.vinted.es/catalog?search_text=&order=newest_first`, `interval_seconds=60`, `jitter_percent=0`, and no proxy. It was stopped after the smoke to avoid accidental continuous scraping.
- Runs for monitor `1107`: run `902` manual/direct failed on transient direct connection to `www.vinted.es`; scheduler then enqueued run `903` which also failed on bootstrap connection; the next scheduled run `904` succeeded through the Redis producer-consumer path with `trigger=scheduler`, `egress_mode=direct`, `browser_profile=chrome_120_win10`, `items_found=5`, `items_new=5`, and `opportunities_created=5`.
- `GET /api/monitors/1107/events` returned 39 safe run events covering bootstrap, catalog API, Redis seen cache, filter pass, opportunity creation, failures, and success. The Redis task queue drained to `0`.
- Runtime profile hardening removed the manually configured `Connection` header from bootstrap headers. Tests now assert the Chrome 120 runtime header order and assert that `Connection` and `TE` are not forced by application code; HTTP/2 pseudo-header order remains owned by `curl_cffi`.
- Live diagnostics after hardening passed: `python scripts/verify_impersonation.py`, `python scripts/check_headers.py --impersonate chrome120`, and `python scripts/check_datadome.py --url "https://www.vinted.es/catalog?search_text=tommy" --impersonate chrome120` returned Chrome 120 headers/TLS, bootstrap `200`, catalog API `200`, and 5 items.
- Playwright MCP was used only for request inventory because the browser context already had user tabs/cookies. A Vinted catalog navigation returned `GET /catalog?...` status `200`; no raw request headers or cookies were printed or persisted. The observed browser context reports a current Chrome family, so it is not a direct Chrome 120 fingerprint reference.
- Current log storage: process logs are JSON/plain stdout visible with `docker compose logs`; monitor/run diagnostics are persisted in the `run_events` table and exposed through `/api/runs/{run_id}/events`, `/api/monitors/{monitor_id}/events`, SSE `/api/monitors/events/stream`, and the PWA. There is still no file logger, Docker log rotation policy, or run-event retention job.
- Local Redis emits noisy client messages about unsupported `MAINT_NOTIFICATIONS` under the current Redis image/client combination. This did not block runs, but should be cleaned before production logging is considered final.

## Accumulated Monitor UI 2026-07-06

- The monitor detail now defaults its performance range to `Todo`, so the chart opens on the all-history view instead of the current hour.
- The monitor detail always shows accumulated historical metrics and an accumulated log timeline for the selected monitor, including stopped monitors.
- The timeline uses `/api/monitors/{monitor_id}/events` for persisted history and the existing SSE stream to append live events for active monitors. Each event is rendered as one console-style line with timestamp, level, run id, flow area/action/result, method/status/duration, compact URL, and safe context tokens. Redacted JSON `details` remains available only behind an explicit technical-details control.
- `Limpiar vista` hides only the currently visible event IDs in the browser session, so persisted history and later event IDs remain visible.
- The per-run log expansion remains available in the global runs view through `/api/runs/{run_id}/events`.

## Independent Merge Audit 2026-07-11

- Adversarial parser gates cover item identity (including rejection of unscoped JSON-LD without matching Flight identity), recommendation isolation, atomic money/currency selection, finite non-negative prices, signed image hosts, reservation/out-of-stock precedence and final redirect validation. The supplied catalog-to-item HAR parses with all required fields, five signed photos, `2.00 EUR` base, `0.80 EUR` protection, `2.80 EUR` total, `1.75 EUR` shipping, `buyable`, and parser p95 below `150 ms` across 30 runs.
- PostgreSQL/Redis gates cover concurrent item upsert, owner-checked atomic retry/terminal transitions, claim-vs-finalize races, two-phase `finalizing` convergence after SQL commit failure, retry-attempt recovery, challenge preservation, stale run recovery, archive-vs-session/source/scheduler fencing and idempotent binary task reservation/ACK/requeue/dead-letter. Redis AOF `appendfsync=always` retained a per-consumer reservation plus reverse marker across a real container restart and recovered it exactly once.
- A live residential run prepared a monitor-owned session (`catalog_api_probe=accepted_json`), seeded a five-item baseline, then processed one catalog JSON plus five sequential public item documents in `18.6 s`: five complete opportunities, zero private detail requests, zero backend image requests, no pending/retry/processing residue and `run_succeeded` only after the Redis transition.
- The 2026-07-12 performance pass added a five-minute sticky egress validation cache, `next_flight_v3` selective records, shadow/enforced head rejection and a two-lane canary. The HAR parser measured `59.7 ms` median / `68.8 ms` p95. Live C2 remained slower than the persistent C1 control (`5.034 s` vs `4.598 s` for five details), so runtime stays serial; ten early-close probes succeeded at `15-22 KB` and retained an accepted session.
- Verification passed with Ruff, `351` backend tests, Alembic `0013` at head, Docker PWA build, API health and frontend HTTP smoke. Playwright MCP timed out before opening/listing a page and Chrome DevTools reported a closed target, so the label-only event narrative change has no browser snapshot; this is an external QA-tool gap, not evidence for promoting any UI behavior.
- Playwright verified the five live opportunities at desktop and 390/320 px: direct signed CDN images, complete price/shipping breakdown, public availability/timestamp/reasons, focus trap/return, keyboard gallery, accessible unavailable actions and real Monitors/Ajustes navigation. Invalid input produced `422` with no row; valid create/archive stayed consistent across UI, API and PostgreSQL.
- Archiving the audit monitor preserved its five opportunities and run history, invalidated its prepared Vinted session and replaced the encrypted cookie/token payload with an empty context.
- The final gate passed the complete backend suite (`340 passed`), Ruff, PWA lint/build, production Compose rendering, Alembic head `0012`, API/frontend smoke and Playwright desktop/390/320 checks. Redaction rejects forged marker containers, production rejects placeholder encryption keys, and the local ignored key was rotated with existing encrypted rows re-encrypted before runtime recreation.
