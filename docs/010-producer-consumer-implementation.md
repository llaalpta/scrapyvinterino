# 010 Producer-Consumer Implementation Notes

This note records implementation-specific decisions for `docs/specs/010-producer-consumer-bypass.md`. The spec remains the source of truth for behavior and acceptance criteria.

## Current State

- Scheduler atomically enqueues `MonitorTask` payloads with `LPUSH`, one pending marker per monitor and a payload reverse marker, coalescing later ticks while work is queued or reserved and counting backlog against global/proxy capacity.
- Consumers use a binary queue client and reserve FIFO work with `BLMOVE` into per-consumer processing lists; its socket timeout exceeds the blocking window. Terminal outcomes ACK the exact payload, unexpected failures requeue it, malformed/non-UTF-8 payloads go to dead-letter, and startup/thread recovery restores unacknowledged reservations. Maintenance transitions retry with backoff and are idempotent after ambiguous responses.
- `CurlCffiVintedCatalogProvider` is the only Vinted catalog HTTP provider.
- Runtime catalog traffic is monitor-owned: the run selects a `vinted_sessions` row by monitor, `proxy_profile_id`, effective identity token and logical browser/context fields, then uses the sticky ID stored in that selected row; otherwise it prepares one automatically from the saved catalog document URL. Stale-generation rows are invalidated and their encrypted context is replaced with `{}` before replacement.
- Manual runs remain synchronous from the API, but use the same provider stack.
- Root-level `audit_010_producer_consumer.md` was removed to avoid duplicate planning docs.
- Item enrichment uses the public item document, structural Next/React Flight records and JSON-LD fallback. The production flow and visible detail probe no longer call the direct `/api/v2/items/{id}/details` matrix.
- Detail work is serial by default per prepared session. An explicit canary can schedule two isolated persistent lanes, but promotion requires measured speedup plus a valid final cookie context. Recoverable candidates survive outside the top-five window in Redis for three total attempts (`30s`, `120s`); only terminal outcomes become seen.
- The PWA persists no image bytes: it renders every signed `images*.vinted.net` URL directly and exposes an accessible gallery plus public availability/price breakdown while purchase remains disabled.

## Decisions

- Use `PROXY_STICKY_USERNAME_TEMPLATE` for provider-specific sticky formats. Default: `{username}-session-{session_id}`.
- For providers that require `sessid`, configure `{username}-sessid-{session_id}`.
- Residential proxy sticky IDs are stored with prepared Vinted sessions for a monitor, not with one-off task attempts. A monotonic generation plus keyed identity digest binds each row to transport, credentials, country preset and sticky template. Profile-field edits invalidate old context in their own transaction; global template drift is reconciled and invalidated transactionally by the first fenced selector after restart.
- Do not call the Asocks refresh API from runtime scraping code; an authorized new preparation/rotation creates a new session UUID, which normal runs then reuse while eligible.
- The pre-integration HTTP fingerprint gate uses Chrome 120 exactly: `curl_cffi.requests.Session(impersonate="chrome120")` plus matching Chrome 120 `User-Agent` and `sec-ch-ua` headers.
- Runtime catalog providers select the configured browser profile; default runtime impersonation is `chrome146`. Direct no-proxy runs remain disabled unless explicitly enabled for diagnostics.
- Runtime metadata and events may store `proxy_session_id_prefix` plus the redactor-created masked/fingerprinted `proxy_sticky_session` marker; they never persist the full sticky value, proxy URL, credentials, cookies or raw DataDome values.
- Redis task payloads carry only `proxy_profile_id` plus `proxy_identity_generation` as proxy-related data, alongside safe task/source/trigger identity and scheduling fields. A proxy payload without the versioned token is malformed rather than legacy-compatible. The consumer resolves PostgreSQL state and takes a shared advisory fence before run events/provider construction; profile edits take the exclusive side of the same fence. That ownership lasts through the first durable commit after the last provider call, not through the later provider-free `finalizing` reconciliation.
- Egress selection removes candidates already saturated in its capacity snapshot before taking an identity fence and acquires ownership for at most one candidate per transaction. If that candidate's durable capacity decreases while the fence is acquired, selection fails for a later transaction instead of retaining one advisory lock while trying another; this preserves direct fallback when every proxy was already saturated and prevents opposite telemetry orders from creating a multiprofile deadlock during template reconciliation.
- Run rows persist indexed `task_id`. Redelivery acknowledges an existing terminal run without Vinted traffic, reconciles `finalizing`, and closes an orphan `running` row before retrying.
- Development Redis uses a persisted AOF volume. The recovery contract assumes one worker service instance with multiple in-process consumers; horizontally scaled workers require distributed reservation ownership before deployment.
- Classify DataDome and `cf-mitigated: challenge` explicitly; plain `429` remains rate limiting and detail `404/410` is terminal.
- Keep retry escalation in `TaskConsumer`; the detail path records the failed run and re-raises both `DataDomeChallengeError` and `VintedCatalogChallengeError`. A first catalog Cloudflare challenge can still enter generic refresh and a later one can be absorbed as a failed run before the consumer sees it; 14.12.3 closes that gap.

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
- `python -m pytest backend/tests/test_vinted_catalog_provider.py backend/tests/test_scheduler.py backend/tests/test_task_queue.py backend/tests/test_proxies.py backend/tests/test_consumer.py backend/tests/test_manual_runs.py backend/tests/test_proxy_identity_fence.py backend/tests/test_ephemeral_http.py backend/tests/test_verify_impersonation_script.py`
- `docker compose up -d --build api worker`
- `docker compose ps`
- `GET http://localhost:8000/health`
- `docker compose exec -T worker python -c "import curl_cffi; print(curl_cffi.__version__)"`
- `python scripts/verify_impersonation.py`

The roadmap item is `done` after the 2026-07-11 residential proxy, reliable queue, public-detail and PWA audit recorded below.

## Proxy Identity Fence 2026-07-13

- FastAPI route boundaries, PostgreSQL and Redis DB 14 exercised stale manual and queued work across scheme, host, port, username/password set and clear, country/preset, active state, cooldown and sticky-template drift. Every stale path remained terminal with zero local provider constructions/calls; password canaries were absent from queue payloads, run metadata/errors and persisted events/error rows. A fresh generation prepared and executed only through the expected loopback proxy host and new template.
- The template case captured the old command in the pytest process and consumed it in a fresh Python worker process whose settings loaded the replacement template from environment. The child process contained a fail-fast provider trap; it ACKed the stale task without reaching that trap, while a subsequent fresh task succeeded through the local instrumented provider.
- Real PostgreSQL advisory races proved shared fences coexist, identity edits wait through the final provider I/O, scheduler selection uses advisory-before-source order, saturated candidates receive no fence, and selection never accumulates ownership across proxy candidates. A two-profile opposite-order race and a capacity-drop/fallback matrix close both deadlock paths found during adversarial review.
- Alembic passed zero-to-`0019`, `0018` with a legacy session to `0019`, generation-aware `0019` down to `0018`, and re-upgrade to head. Both directions deliberately purged incompatible sessions; columns, defaults, nullability and old/new indexes matched the contract.
- Final verification passed `ruff check src alembic tests`, the complete backend suite (`491 passed, 1 skipped` opt-in live-API test), `pnpm lint`, `pnpm build` and `git diff --check`. Before cleanup the isolated graph had zero sources, profiles, prepared sessions, runs, events, errors or active monitor sessions and Redis DB 14 had zero keys. API/PostgreSQL/Redis remained the only running Compose services; worker/watchdog stayed stopped and no Vinted or proxy endpoint was called.

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
- Run events record `bootstrap_origin=catalog_document`, CSRF/anon presence booleans, and safe markers only. The active raw values live only in provider memory/the cookie jar, and a serialized copy is stored inside encrypted `vinted_sessions.context_encrypted`; events, runtime metadata and API responses never expose them.

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
- Business runs require the public `/items/...` HTML/Next detail document before creating opportunities. Recoverable failures stay pending in Redis; configured incompleteness, genuine `404/410`, blacklist decisions and exhausted retries are terminal. DataDome and detail-document anti-bot challenges fail the run, invalidate the prepared session and preserve the rolled-back batch. A first catalog Cloudflare challenge can still enter the generic session-refresh branch; 14.12.3 closes that divergence and its consumer propagation.

## Prepared Session Hardening 2026-07-09

- A monitor-owned Vinted session is reusable only after strict cookie/token context is present and country, locale, `Accept-Language` and `x-screen=catalog` match. The viewport is persisted but the current selector does not compare it; 14.12.5 adds that missing predicate and reconciles runtime/API/PWA eligibility.
- Session preparation may still call the catalog API probe after a failed DataDome collector to collect diagnostics, but a successful JSON probe no longer overrides missing `datadome` or `__cf_bm`; the saved row remains `incomplete` and the run fails clearly.
- Runtime provider selection, explicit `Preparar sesion` and item detail probes share the strict prepared-session requirement. Detail rotations mark context for persistence, but ordinary catalog/probe rotations and a successful refresh followed by a failed retry can still leave stale encrypted context; 14.12.4 owns that durability gap.
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
- The notification-ready opportunity pass changed blacklist semantics to public description only, versioned Redis policy as `description_only_v2`, added nullable catalog-only `view_count`, and promoted safe single-request early rejection. The semantic gate covered the supplied HAR plus 29 live documents with zero false positives; ten paced enforced closes used `16-22 KB`, produced no challenge/`429`, and retained an accepted catalog session.
- Implementer self-review confirmed unavailable/unknown public states still create honest opportunities, non-description fields never filter, optional prices remain nullable, no image bytes or notification behavior were added, and logs contain only safe counts/timings. Verification passed with Ruff, `364` backend tests, Alembic `0014` at head, PWA lint/build, API/frontend smoke and Playwright desktop/mobile checks. The live `description_only_v2` baseline marked five current IDs without opportunities; the following manual run made one catalog request, skipped all five in Redis, issued no detail request and left no retry residue.
- Verification passed with Ruff, `351` backend tests, Alembic `0013` at head, Docker PWA build, API health and frontend HTTP smoke. Playwright MCP timed out before opening/listing a page and Chrome DevTools reported a closed target, so the label-only event narrative change has no browser snapshot; this is an external QA-tool gap, not evidence for promoting any UI behavior.
- Playwright verified the five live opportunities at desktop and 390/320 px: direct signed CDN images, complete price/shipping breakdown, public availability/timestamp/reasons, focus trap/return, keyboard gallery, accessible unavailable actions and real Monitors/Ajustes navigation. Invalid input produced `422` with no row; valid create/archive stayed consistent across UI, API and PostgreSQL.
- Archiving the audit monitor preserved its five opportunities and run history, invalidated its prepared Vinted session and replaced the encrypted cookie/token payload with an empty context.
- The final gate passed the complete backend suite (`340 passed`), Ruff, PWA lint/build, production Compose rendering, Alembic head `0012`, API/frontend smoke and Playwright desktop/390/320 checks. Redaction rejects forged marker containers, production rejects placeholder encryption keys, and the local ignored key was rotated with existing encrypted rows re-encrypted before runtime recreation.

## SSE and Activation Stabilization 2026-07-12

- The stream cursor is now the monotonic position in `run_event_publications`, assigned only after a `run_events` row is visible as committed. This prevents a transaction that reserved a lower event ID but committed later from being skipped. `stream_ready` carries that position in both `id:` and JSON; synchronous PostgreSQL polling runs off the async event loop.
- Persisted safe session/cookie markers are restored only for the strict marker-only fields after their JSONB round trip, then pass through the normal redactor. A caller-supplied marker-shaped mapping is still redacted before persistence.
- The dashboard waits for `stream_ready` before loading per-monitor REST history and tracks history-loaded state independently from live presence. It owns one manually reconnected `EventSource`, resumes with the current publication cursor, preserves pending terminal batches across navigation, applies partial terminal refresh successes, and rejects stale run/stat responses with per-monitor request generations.
- Terminal batches refresh sources, affected monitor runs and affected monitor stats. They do not refresh the global run list and reload opportunities only when `opportunities_created` is positive or unavailable.
- Recurring activation selects available egress and commits active state, session, deadline and the initial running row atomically. Failure before that run rolls back the whole activation; there is no partial state to compensate. A second activation is rejected. The producer rechecks `next_run_at` after locking and commits window deferrals before cache or egress work can roll them back.
- Verification passed on an isolated PostgreSQL database migrated from zero through `0016`: Ruff, `135` focused tests and all `391` backend tests, plus PWA ESLint/build, API health and frontend HTTP smoke. The worker stayed stopped and no Vinted/proxy request was made.
- Playwright against the live PWA observed one SSE across statistics renders, close on navigation, and one resumed connection with `last_event_id=21713`; the intervening synthetic event rendered exactly once. A two-terminal zero-opportunity batch issued exactly one monitors, one affected-run and one affected-stats request, with no opportunity or global-run request. Historical/live fusion and tail-follow/new-event behavior also passed. The temporary QA monitor/events and the previously archived `audit-live-detail-20260711` residue were deleted afterward; no QA-named monitor or matching Redis key remained.

## Real Recurring Cadence Closure 2026-07-12

- A temporary copy of the existing public catalog monitor ran in `continuous` mode with `interval_seconds=60`, `jitter_percent=10` and no allowed-window restriction through the live PWA, API, worker, PostgreSQL, Redis and event timeline using the single active residential ES proxy. Direct egress remained disabled. A temporary Compose override limited both API and worker to `per_page=1`, zero detail candidates, zero HTTP retries, one consumer and one consumer attempt; the override never changed `.env` and was removed afterward.
- One explicit PWA recalibration created baseline run `2156`: success, one catalog item, zero opportunities and one monitor-owned prepared session. Its accepted catalog probe recorded `per_page=1`; preparation reused that result instead of issuing a second business search.
- Playwright sent exactly one `POST /api/monitors/3467/start`. Activation was persisted at `21:14:03.588739Z` with first deadline `21:15:05.588739Z`, exactly `62` seconds later. Immediate run `2157` succeeded, followed by scheduler runs `2158` and `2159` starting at `21:15:06.742374Z` and `21:16:07.195196Z`. The first scheduler start was `63.154` seconds after activation including poll latency; the next start was `60.453` seconds later.
- Exactly three business runs existed: one immediate plus two distinct scheduler tasks, all associated with monitor session `849`, all terminal successes and no running residue. Each run emitted one catalog request start/success pair, found one item, used proxy `1`, produced no detail fetch and created no opportunity. The prepared session recorded four catalog uses total including the baseline.
- The live PWA rendered all three business run IDs and terminal events. Playwright stopped the session once after run three; PostgreSQL cleared active/deadline state and closed the session with reason `stopped` before another due time.
- Cleanup deleted the complete temporary source/run/event/publication/session/prepared-session graph and its three monitor-scoped Redis keys. Queue, processing and pending markers were empty; scheduler `app_settings`, proxy telemetry, producer heartbeat, API environment and initial stopped worker/watchdog state were restored exactly. No QA source or active session remained.
- Ruff, all `407` backend tests, PWA lint/build and both Compose renders passed after the live check. The backend suite still reads host integration settings and changed the real proxy telemetry after the first cleanup; the final state audit detected and restored it again, then verified the restored values without another DB-writing suite run. Removing that host-state dependency remains the explicit scope of roadmap task 14.18.

## Transactional SSE Outbox 2026-07-12

- Alembic `0017` adds indexed `run_event_outbox` work for every monitor event. `record_run_event` creates the event and pending row in the caller transaction; the serialized publisher creates one monotonic publication cursor and removes the pending row in its own atomic transaction. Runtime polling no longer rescans historical events with an anti-join.
- Isolated PostgreSQL verification passed both `0016 -> 0017` and zero-to-head. The upgrade backfilled only the committed monitor event without a publication, preserved the already-published event, created the ordered index and cascade FK, and passed `0017 -> 0016 -> 0017`; both temporary databases were dropped.
- A live HTTP SSE check used the running API and production event writer with only local `example.invalid` QA data. Tail startup skipped prior history, cursor resume delivered new events once, an unpublished event survived a real API restart, and a 105-event backlog crossed the 100-event transport boundary with no polling pause (`0.0 s` measured between events 100 and 101). All 109 QA events, their outbox/publications and their source were deleted.
- The independent audit found that an event with a lower ID reserved before tail startup could commit during a multi-batch drain and slip below `stream_ready`. Tail fencing now uses one `REPEATABLE READ` snapshot under a session-level publication lock. Barrier tests prove both the lower-ID event and a concurrent publisher remain after the snapshot cursor; normal polls yield immediately on lock contention so heartbeat/disconnect checks continue. A second live pass drained a 1005-event tail, delivered the post-tail event and recovered another pending event after an API restart.
- PostgreSQL tests cover producer commit/rollback, events outside monitor scope, inverted commits, two concurrent publishers, 1001-row bounded draining, publisher rollback, stale outbox reconciliation, a repeatable tail snapshot with a lower ID that commits late, and session-lock release after a forced acquisition-commit failure. Verification passed Ruff, `23` focused tests, all `418` backend tests, PWA lint/build, Compose render, API health and Alembic head `0017`; worker and watchdog remained stopped and no Vinted/proxy request was made.
- The first full-suite run passed `409` tests and failed seven existing integration cases because pytest launched from `backend/` did not load the root encryption key. Complete reruns passed after injecting that already-local value only into the pytest process. Suite-created proxy and heartbeat telemetry was captured and restored automatically around the final run; deterministic settings and fixture-owned telemetry remain task 14.18.

## Persisted Run-Event Redaction Parity 2026-07-13

- `RunEventRead` and SSE now share the persisted-event redactor. After PostgreSQL JSONB removes the runtime marker subclass, the read path restores only structurally strict markers under marker-only containers, sensitive fields and sensitive request/response headers, then reapplies the complete recursive redaction pass.
- The writer remains the trust boundary: plain marker-shaped maps, shapes with extra fields, mixed marker collections, raw sensitive values, historical response-content fields and sensitive assignments in text are redacted before persistence. The independent audit additionally found raw metadata could be smuggled through a factory-created marker; marker kind/name and unknown sensitive header names are now canonicalized, markers cannot be constructed or mutated through their public API, and read validation rejects extra/incoherent metadata.
- Real API verification persisted one legitimate runtime-marker event and one forged/raw-canary event through `record_run_event` in a local QA run. Direct JSONB, monitor REST, run REST and SSE returned exactly the same two safe `details` once per ID; all unique raw canaries were absent from PostgreSQL-visible JSON, HTTP/SSE payloads and API logs.
- The focused audit reproduction passed a hostile marker `name`/`kind`, hostile `X-Token-*` header, direct constructor and mutation attempts through PostgreSQL, REST, SSE and API logs without exposing its canary. Cleanup removed every event, publication/outbox row, run and source from all attempts.
- Ruff, `38` focused redaction/stream tests, all `428` backend tests, PWA lint/build, Compose render and API health passed. The backend gates captured and restored existing proxy/heartbeat telemetry automatically; worker and watchdog stayed stopped and no Vinted/proxy request was made. No migration, alternate format or compatibility adapter was added.

## PWA EventSource Ownership 2026-07-13

- Every `open`, `error`, `stream_ready`, `stream_heartbeat` and `monitor_event` callback verifies that the `EventSource` which registered it is still current. The backend emits the named cursor-neutral heartbeat alongside its transport comment every 15 idle seconds. The PWA timer starts in `CONNECTING`, is rearmed only by signals from the current stream and closes a silent connection after 22.5 seconds before one bounded auth revalidation and sequential reconnect; callbacks from replaced or closed instances are inert.
- Playwright exercised the live PWA and native SSE transport while restarting the real API. API startup took longer than the three-second retry delay, so multiple attempts occurred sequentially, but every failed instance was closed once and there was never more than one current connection. After another controlled replacement, late `open`, `stream_ready`, `monitor_event` and `error` callbacks from closed instance 8 left instance 9 as the only open stream, rendered no fake event and created no extra connection after 3.5 seconds. A final reconnect retained cursor `122396` instead of the injected stale cursor, proving the late callbacks changed neither event state nor resume position.
- Leaving Monitors closed instance 6 exactly once and left zero active streams. While away, production `record_run_event` persisted local event `58003` for QA source `4408`; the publisher assigned cursor `122396`. Returning opened one stream with the prior explicit cursor `97005` and rendered the event's unique phase exactly once. Leaving again closed that stream exactly once.
- Cleanup removed the exact QA source, event, outbox and publication rows, restored the pre-QA scheduler heartbeat timestamp/value and left the pre-existing host Vite process untouched. API, PostgreSQL and Redis remained healthy; the worker stayed stopped. No Vinted or proxy request was made.
- The `ba4b9cc` EventSource hunk is applied literally. Its unused `scripts/qa-pwa.ps1 -SkipWorker` hunk is rejected: skipping startup does not stop or prove the absence of an already-running worker, while this workflow explicitly inspects and stops the worker before local event QA. All other reference hunks were integrated or superseded by the stricter 14.3-14.8 implementations.
- Verification passed Ruff, PWA lint/build, API health, deterministic stale-callback instrumentation and the backend-produced PostgreSQL/outbox/SSE/browser path above. The backend product code did not change; the inherited `428`-test backend gate remains the base commit evidence for that unchanged surface.
- Independent audit found no defects at any severity. Accepted residual risks are that the callback-race probe is manual instrumentation rather than a permanent frontend regression test, HTTP teardown can lag behind the single logical `EventSource`, synchronous constructor failure for the fixed stream URL remains unhandled, and worker-off QA still requires an explicit runtime check.
