# 008 Bounded Concurrent Scheduler and Runtime Cache

## 14.19 Worker Redis availability

Status: `done`. This is a contained fail-stop correction for the current local worker, not a general dependency-readiness platform.

After startup, the worker supervisor probes Redis on its existing supervision cadence. A failed probe is a process-level dependency loss: it is logged safely and the worker exits non-zero without an internal reconnect loop or fallback. The producer heartbeat is not deleted because a replacement worker may already own a newer signal; instead, process exit stops renewal and the existing timeout makes scheduler availability false. Docker Compose owns restart, and a replacement cannot publish a heartbeat until Redis is reachable at startup.

Acceptance criteria:

1. Losing Redis after a successful worker start causes a sanitized critical log and non-zero worker exit; `restart: unless-stopped` starts a replacement attempt, which remains unavailable until Redis returns.
2. The last PostgreSQL heartbeat is not refreshed after the loss. `GET /api/scheduler` becomes `worker_available=false` and `effective_enabled=false` after the configured timeout, and the existing PWA Settings poll shows unavailable without a new readiness field.
3. With Redis healthy, the idle worker remains available and ordinary non-Redis consumer failure keeps the existing per-thread restart behavior instead of terminating the whole process.

Representative integration: use a migrated disposable PostgreSQL database, a disposable Redis container and a disposable worker container with Docker restart enabled. Keep the scheduler without sources or queued work and block every outbound destination. Observe a fresh heartbeat, stop only the disposable Redis, prove worker restart/non-zero failure plus heartbeat expiry and the live API/PWA unavailable state, restore Redis and prove the worker and PWA become available again. Remove the containers, database, role, Redis state, API/Vite processes and logs, and verify the operational PostgreSQL/Redis fingerprints and initial service state are unchanged. The negative variation keeps Redis healthy while a focused supervisor test crashes a consumer and observes only its thread restart. External Vinted, proxy and Telegram allowance is zero.

Excluded: API-wide readiness, explicit heartbeat deletion, queue/drain redesign, Redis restart ownership and production lifecycle hardening.

Verification: 17 focused scheduler/supervisor tests and one live Playwright outage/recovery case passed against a migrated disposable PostgreSQL database plus disposable Redis/worker containers on an internal Docker network. The real worker exited non-zero, Docker restarted it, the heartbeat stopped advancing, API/PWA became unavailable after timeout and recovered after Redis returned. Cleanup left no QA containers, networks, roles, databases, Redis keys, ports or logs; operational PostgreSQL/Redis fingerprints and service state were unchanged. No Vinted, proxy or Telegram traffic was allowed.

## Planned 14.34 session program

Status: `done`. Slices 14.34.1, 14.34.2 and 14.34.3 are implemented and verified. Their current clauses replace the temporary calibration/stop contracts across specs 001, 003, 005, 008 and 010 plus architecture/data-model prose.

The user saves mode, cadence, duration/window and filters while stopped. Every session start observes one internal `baseline` run while the monitor remains inactive. It adds current catalog IDs to the existing monitor/policy seen state, preserves older seen markers, creates no item/opportunity and is returned as `201 RunRead`. Validation failures create no run; operational baseline failure returns its failed run and leaves no active session/deadline. If the Redis baseline marker later expires during an active session, the next ordinary run fails visibly and closes/stops that session; it never recalibrates silently. Restarting is the manual recovery.

No slice adds a migration, compatibility adapter, automatic retry, hidden fallback or hard network cancellation. Baseline events and policy hashes remain internal diagnostics. Configuration stays read-only while a session is active and, after 14.34.3, while it is draining. Vinted, proxy and Telegram traffic allowance is zero; every QA slice cleans its rows, Redis keys and temporary processes and restores initial services.

### 14.34.1 Manual session-start baseline

Status: `done`. This standard vertical slice has no migration: manual session start, its later user-triggered runs and their existing PostgreSQL/Redis state form one outcome. Recurring start and graceful in-flight stop remain in 14.34.2 and 14.34.3. Verification passed the isolated live PWA/API/PostgreSQL/Redis scenario, `515` backend tests plus `3` loopback-only cases, Ruff and PWA lint/build without external traffic or QA residue.

`POST /api/monitors/{id}/start` in manual mode performs the baseline and, only on success, creates one active monitor session with `next_run_at=null`. `POST /api/monitors/{id}/runs` requires that active manual session, attaches each single-flight `Ejecutar ahora` run to it and no longer creates/closes a punctual session per run. The 14.34.3 contract keeps stop available during non-terminal session work, makes the source inactive immediately and lets normal drain close the session after every admitted run reaches terminal; stronger fail-stop reasons remain dominant.

Acceptance criteria:

1. Manual start creates exactly one zero-opportunity baseline run and then one active session without a deadline; baseline failure leaves the monitor inactive with no open session.
2. `Ejecutar ahora` is available only inside that active manual session, reuses its session ID and creates exactly one opportunity for one later unseen passing ID without duplicates.
3. Restarting takes another baseline so entries visible during downtime are ignored; a missing/expired marker fails visibly and requires another stop/start instead of implicit calibration.

Representative integration: with the authenticated live API, PostgreSQL and Redis plus a synthetic provider at the Vinted boundary, start on IDs A/B/C, run the same set with zero opportunities, add D and observe exactly one, repeat D without duplication, stop after terminal, add E and restart with another zero-opportunity baseline. The negative variation makes baseline search fail and proves a visible failed run with no active source/session/deadline. Playwright verifies the manual start, active `Ejecutar ahora`, disabled conflicting controls and persisted API/database state.

### 14.34.2 Recurring session-start baseline

Status: `done` on `feature/recurring-session-start-baseline`.

Recurring start preflights effective scheduler availability/capacity before baseline traffic and revalidates admission after it. Success creates one active continuous/duration/window session and persists the first later deadline from activation without a second immediate business run. With interval 60 and jitter 10 percent, the deadline is 60 to 66 seconds after activation. A post-baseline availability/capacity loss returns the existing `503`/`409`, preserves the successful baseline run and leaves the monitor inactive. Recurring active views do not add a manual override.

After every start path owns calibration, remove `POST /api/monitors/{id}/baseline`, its frontend client/button/status panel and public `baseline_ready`/`baseline_policy_hash` fields. Do not retain tombstones or adapters.

Acceptance criteria:

1. Recurring start produces one zero-opportunity baseline, one active session and one persisted later deadline, with no immediate business run or duplicate initial task.
2. The first scheduled run on the same IDs is a no-op; a later unseen passing ID creates one opportunity and repetition creates none.
3. Preflight failure is traffic- and mutation-free; post-baseline admission loss is visible and leaves no active session/deadline while preserving the completed baseline run.

Representative integration: use the authenticated API, PostgreSQL, Redis, real scheduler queue and consumer with a synthetic provider only at the Vinted boundary. Start on five IDs, verify the 60-to-66-second deadline and no immediate business run, consume the same IDs with zero opportunities, then add one ID and observe exactly one opportunity and terminal ACK. The negative variation uses unavailable scheduler preflight and proves zero run/session/task/provider calls. Playwright verifies that the standalone recalibration contract is absent.

Verification passed with the isolated live PWA/API/PostgreSQL/Redis scenario and real in-process scheduler/queue/consumer, the complete backend gate (`516 passed`, `5 skipped` contractually, plus `3 passed` loopback-only), Ruff, PWA lint/build and Compose rendering. The same-ID run, one-new-ID run and repeat were terminal and ACKed with zero queue residue. Two additional isolated manual-session cycles also passed. No Vinted, proxy or Telegram request was allowed; temporary SQL, Redis, process and log state was removed and operational fingerprints were unchanged.

### 14.34.3 Graceful monitor-session stop

Status: `done` on `fix/session-stop-drain`.

`POST /api/monitors/{id}/stop` makes PostgreSQL inactive first, clears future scheduling and best-effort cancels a ready task. A reserved task rechecks authoritative state under the existing source/admission ownership and cannot create a run while the source remains inactive. Session-owned runs already created reach their normal terminal states; only the last normal terminal closes the monitor session with reason `stopped`. Stronger fail-stop paths keep reasons such as `baseline_required` or `redis_unavailable`. The PWA derives `Deteniendo...` from an inactive source plus a session-owned `running/finalizing` run, or conservatively from the successful local Stop command until a directed runs read is accepted. After a reload, unknown run state does not claim a drain but still blocks edit/archive/start until PostgreSQL is read successfully. A sessionless baseline still makes stop return `409`. The local confirmation latch is transient and never overrides PostgreSQL; there is no new durable stopping state or activation generation.

Acceptance criteria:

1. Stop with no non-terminal run closes the session immediately and admits no scheduled/manual work while the source remains inactive.
2. Stop during existing session work returns promptly, admits nothing else while stopped, preserves every run result and closes the session only after the last normal terminal.
3. A deterministic reserved-task race cannot create a run before a later explicit activation, and the PWA cannot edit, archive or restart during a visible drain.

Representative integration: use real PostgreSQL, Redis queue/consumer and API with a blocking provider only at the Vinted boundary. Stop after the run row exists, release the provider, and verify inactive source first, one terminal run, then `monitor_sessions.stopped_at`/reason with no ready/processing residue. The negative barrier reserves a task before stop but delays run admission until after commit and proves zero run/provider calls while the source remains inactive. Playwright aborts the immediate directed runs read and repeats that failure after a full reload: the local latch first preserves `Deteniendo...`, the reloaded unknown state keeps commands fail-closed, and the real consumer terminal SSE batch finally refreshes PostgreSQL state and unlocks the monitor.

Verification passed the isolated `session-stop-drain` gate (`6` focused plus `1` live Playwright case), the complete backend gate (`519 passed`, `6 skipped`, plus `3 passed` loopback-only), Ruff, PWA lint/build and Compose rendering. The self-review additionally covered a concurrent `finalizing` sibling and preserved the stronger `baseline_required`/`redis_unavailable` reasons. Worker/watchdog remained stopped; all traffic was loopback-only, temporary SQL/Redis/process state was removed and operational fingerprints were unchanged.

## Goal

Automatically execute active opportunity monitors on safe, bounded intervals with enough concurrency and runtime cache support to keep opportunity alerts fast without relying on post-MVP optimizations.

## Scope

- Enable or disable scheduler globally.
- Enable, stop, or archive each monitor.
- Treat inactive monitors as configured but not launched; active monitors are launched for recurring execution.
- Treat running/executing as run state, not as persistent monitor state.
- Start and stop recurring monitor execution using the monitor's persisted filter definition, cadence, and duration/window mode.
- Persist each recurring activation as a monitor session until it is stopped, archived, expired, or blocked by a stopping failure.
- Let manual start calibrate and open an active session without scheduler state or a deadline; later manual runs are explicit commands inside that session.
- Start a monitor for a bounded duration from now, with `monitor_until` stored on the monitor.
- Configure interval seconds per monitor, default `300`, minimum `60`, maximum `3600`.
- Add jitter/randomization between runs, default `20%`, minimum `0%`, maximum `50%`.
- Configure an optional per-monitor stop limit for Vinted session use count, `stop_after_vinted_session_uses`, empty by default, minimum `1`, maximum `1000`.
- Support one daily allowed execution window configured by start/end timepickers and stored as `HH:MM-HH:MM`.
- Record scheduler-triggered errors in the same run/error model.
- Record safe run progress events for anonymous session bootstrap, catalog API request, retries, detail fetches, and failures.
- Record professional run logs with level, phase, sanitized URL, status code, duration, timeout, attempt/retry details, Redis state, filter outcome, and opportunity outcome.
- Run multiple active monitors concurrently with explicit limits.
- Allow at most `2` active monitor runs globally by default.
- Allow at most `1` active run per monitor.
- Keep monitor execution fair so one noisy monitor cannot starve others.
- Require Redis for per-monitor seen state and processing locks.
- Do not run a monitor when Redis is unavailable.
- Require an explicit initial catalog snapshot before any monitor run can process opportunities.
- Isolate anonymous public Vinted session cookies per monitor and prepared proxy sticky identity.
- Use a deterministic fast catalog flow: create one `curl_cffi` session, load or automatically prepare the encrypted monitor-owned Vinted session for the same proxy sticky identity, diagnose egress with it when configured, then call `/api/v2/catalog/items` with API parameters translated from the saved catalog URL and with browser-coherent headers.
- Keep proxy usage globally managed by the scheduler.
- Support UI-managed proxy profiles with encrypted credentials and a declared proxy country; locale, `Accept-Language`, viewport, and Vinted `x-screen` context are resolved internally from country/domain presets.
- Assign proxy/session identity consistently for a run; do not mix cookies across proxies.
- Use active global proxies matching the target country before direct outbound access; direct access is allowed only when both the UI setting and the deployment gate permit it.

## Interfaces

- Worker:
  - scheduler loop;
  - process supervisor that exits when producer progress expires;
  - independent fail-stop watchdog for active recurring monitors;
  - bounded monitor run executor;
  - Redis seen cache client;
  - isolated provider/session factory.
- API/PWA:
  - scheduler settings persisted in `app_settings`;
  - monitor inactive/start/stop/archive controls;
  - monitor configuration save control separated from launch;
  - monitor historical stats endpoint for active monitor performance cards;
  - global proxy pool and scheduler runtime controls.
- Configuration:
  - ownership rule: `.env` owns deployment, secrets, worker and anti-bot defaults; UI `app_settings` owns daily operation only;
  - deployment scheduler enable flag in `.env` as an operational gate;
  - deployment-owned producer heartbeat interval and timeout; the scheduler producer refreshes its own heartbeat while waiting between polls;
  - deployment-owned watchdog poll interval and startup grace, both bounded against the producer heartbeat contract;
  - UI scheduler enable flag in `app_settings`;
  - global concurrency limit, default `2`;
  - per-monitor concurrency limit, default `1`;
  - direct-without-proxy UI enable flag and direct concurrency limit;
  - deployment direct-catalog gate `VINTED_DIRECT_CATALOG_ENABLED`, default false;
  - target Vinted country and internal locale/header/viewport/Vinted-screen presets, with deployment-owned defaults for direct diagnostics;
  - per-proxy run concurrency limit stored on each proxy profile;
  - catalog results per run, detail fetch candidate limit, request timeout, proxy cooldown, and stop-after-failures settings;
  - monitor interval seconds: default `300`, minimum `60`, maximum `3600`;
  - monitor jitter percent: default `20`, minimum `0`, maximum `50`;
  - optional allowed windows as local `HH:MM-HH:MM` ranges;
  - optional `stop_after_vinted_session_uses` per monitor, counting completed runs in the active monitor session that used the same `vinted_session_id`;
  - scheduler timezone, default `Europe/Madrid`;
  - optional UI-managed proxy profiles.
- Run event log:
  - `level`: `debug`, `info`, `warning`, or `error`;
  - stable machine phase such as `run_started`, `redis_check_error`, `anonymous_session_bootstrap_success`, `catalog_api_request_success`, `detail_fetch_error`, `item_discarded`, or `opportunity_created`;
  - `duration_ms`, `status_code`, `timeout_ms`, `attempt`, and `retry_reason` when available;
  - exact event timestamp and one non-interactive operational checklist entry per event in the PWA console;
  - safe cookie, token, HTTP session, and proxy sticky-session markers with name, length, `first4****last4` masked preview for long values, and fingerprint, never the full value;
  - egress diagnostic data collected through the same HTTP session/proxy, including IP and country when the diagnostic endpoint returns them.
  - accumulated history is loaded through REST; an SSE connection without a cursor starts at the current publication tail, while `last_event_id` query input takes precedence over the standard `Last-Event-ID` resume header;
  - the stream cursor is a monotonic publication position independent from `run_events.id`; it represents observed publication order rather than exact database commit timing, so transactions that become visible out of ID order remain deliverable exactly once to the PWA;
  - every persisted monitor event creates indexed outbox work in its own transaction; publication assigns the durable cursor and removes that work atomically without rescanning event history;
  - the SSE stream announces `stream_ready` with its initial cursor in both `id:` and JSON data plus a three-second reconnect delay, drains complete 100-event backlog batches without polling pauses, and emits both a transport comment and named cursor-neutral `stream_heartbeat` every 15 seconds while idle;
  - the SSE response disables intermediary caching/transformation and proxy buffering, and closes promptly after client disconnect while preserving the run-event redaction contract.
- Database:
  - `app_settings`;
  - `search_sources.scheduler_config`;
  - `search_sources.monitor_mode`, `duration_minutes`, `monitor_until`, `next_run_at`, and `filter_definition`;
  - `monitor_sessions`;
  - `proxy_profiles`;
  - `runs.trigger` and optional `runs.monitor_session_id`;
  - `items` for opportunity items only;
  - `errors`.
  - `run_event_outbox`, with one pending row per committed monitor event until publication;
  - `run_event_publications`, with one monotonic stream position per persisted monitor event.

## Acceptance Criteria

- Scheduler can be disabled completely.
- A monitor can be stopped without deleting it.
- A new monitor is inactive until launched.
- Manual start creates one sessionless baseline and opens one active session only after success; later explicit runs reuse that session until stop or fail-stop.
- Scheduler-triggered runs are not dispatched outside configured local-time windows. Session start performs only its internal baseline before scheduling later business runs inside the configured window.
- Time window UI exposes one start time and one end time; empty start/end means no daily window restriction.
- A bounded monitor started for N minutes stores `monitor_until = now + N minutes`.
- Launching a recurring monitor from the PWA uses the monitor's already persisted configuration, performs one sessionless zero-opportunity baseline while inactive and activates only after it succeeds.
- Activation persists `next_run_at` from the post-baseline activation timestamp, interval, jitter and allowed window. The scheduler treats this PostgreSQL value as authoritative over any in-process due-time cache, so start cannot enqueue or execute an immediate business run.
- Initial recurring admission is serialized with a PostgreSQL transaction-scoped advisory lock before capacity and egress selection. With capacity one, two concurrent starts produce exactly one `201` and one `409` without exceeding capacity.
- Recurring start revalidates scheduler availability/capacity after the baseline. Success commits the terminal baseline, active state, monitor session and future deadline together; a post-baseline `503`/`409` commits the successful baseline alone and leaves no active source, session or deadline.
- Starting an already active recurring monitor is rejected without changing its session, activation timestamp, deadline, or run history.
- The scheduler rechecks the persisted deadline after locking a due source and persists window deferrals independently from later capacity failures.
- With a 60-second interval and 10% jitter, the minimum interval floor makes the first post-activation due time 60 to 66 seconds after activation, plus scheduler tick latency.
- Every `Iniciar sesion` creates the Redis snapshot before activation; later manual and scheduler runs require it.
- Starting, preparing a Vinted session or probing detail is blocked when the saved URL contains catalog filters that cannot be translated to the fast API.
- `Guardar` is the only PWA action that persists monitor configuration.
- `Iniciar sesion` is disabled when the selected monitor has unsaved configuration changes and must not send `PATCH /api/monitors/{id}`.
- Monitor active state is controlled only by `POST /api/monitors/{id}/start` and `POST /api/monitors/{id}/stop`; monitor configuration `PATCH` rejects legacy `is_active` payloads.
- Active monitor configuration is read-only; after stop, both PWA and direct `PATCH` remain blocked until every `running/finalizing` run is terminal.
- Launching a recurring monitor is rejected when the effective scheduler is disabled or no scheduler capacity is available.
- Recurring activation requires a fresh heartbeat written by the scheduler producer itself. Missing, malformed, naive, implausibly future, or expired heartbeat state returns `503` and leaves the source, deadline, monitor session and runs unchanged.
- `GET /api/scheduler` exposes `worker_available` and nullable UTC `worker_last_seen_at`; `effective_enabled` is false unless UI/deployment gates, capacity and the live producer are all available.
- The PWA treats scheduler refresh failure as unavailable/unknown, discards any previously usable scheduler state, and blocks recurring launch. It never labels missing producer availability as a degraded operating mode.
- Invalid deployment scheduler configuration terminates worker startup. Once started, the worker supervisor terminates the process when its Redis probe fails or its own producer heartbeat expires; Compose owns restart and reports heartbeat health. Redis loss does not add another public readiness field: process exit stops heartbeat renewal and the existing timeout makes API/PWA availability false.
- The scheduler watchdog starts only after API health confirms API-owned migrations are complete. After its startup grace, an expired producer heartbeat locks active non-manual sources and rechecks liveness before changing them.
- If liveness is still absent after the lock, the watchdog makes PostgreSQL authoritative first: it clears active/deadline/duration state, closes the active monitor session with `scheduler_worker_unavailable`, and persists one sanitized warning event per stopped source. Manual monitors remain unchanged.
- Ready-task cleanup happens only after the PostgreSQL stop commits. Redis cleanup failure is logged visibly and never rolls back the inactive source; a later consumer must treat that inactive database state as authoritative.
- An unexpected watchdog iteration error terminates the watchdog process so Compose restarts it; it is not converted into a silent polling loop.
- A successful manual or recurring activation creates one open monitor session. Manual business runs reuse it; recurring runs associate with it until stopped, expired or failed.
- A recurring monitor with `stop_after_vinted_session_uses=N` stops automatically after the Nth completed run in that active monitor session that used the same `vinted_session_id`, and records `vinted_session_use_limit_reached`.
- The scheduler only considers active recurring monitors.
- Expired active monitors are stopped before scheduler planning.
- Jitter prevents fixed exact polling intervals.
- Scheduler failures are logged without stopping the worker.
- Invalid scheduler config is rejected clearly: interval outside `60..3600`, jitter outside `0..50`, `stop_after_vinted_session_uses` outside `1..1000`, malformed allowed windows, unsupported keys such as `pause_windows`, or an invalid scheduler timezone.
- No more than `2` monitor runs execute at the same time by default.
- The same active monitor never has two active runs at the same time.
- The scheduler uses active healthy proxies from the global pool before direct access, filtered by target country.
- Proxy capacity is the sum of active healthy target-country proxy profile `max_concurrent_runs` values; there is no separate UI-level global per-proxy cap.
- When no proxy is available, direct access is used only if the UI setting allows it and `VINTED_DIRECT_CATALOG_ENABLED=true`.
- If neither proxy nor direct capacity is available, a periodic monitor is not activated or run.
- Manual and scheduler-triggered runs share the same Redis seen state, item identity, monitor dedupe, detail fetch, redaction, and error behavior.
- Manual and scheduler-triggered runs share the same URL-filter compatibility validation and fast API parameter translation.
- Manual and recurring start own their initial snapshot and never create opportunities from it. Scheduler-triggered runs require that session-start marker.
- Redis stores safe task/cache/retry data: IDs, timestamps/due times, policy hash, counters/types, processing/seen markers and normalized public candidates needed for bounded detail retries.
- Redis never stores cookies, tokens, HTML, raw Vinted payloads, proxy credentials, addresses, or payment data. Prepared Vinted cookies/tokens are stored only in the database encrypted with the local app secret.
- Run logs show operational progress with sanitized URLs, request headers after redaction/masking, response headers after redaction/masking, status codes, per-request durations in milliseconds, egress mode, proxy profile id when used, auth mode, IP/country from the egress diagnostic, filter snapshot, Redis/cache decisions, candidate decisions, persistence decisions, opportunity outcomes, and safe counts only.
- Run logs show the translated fast API parameters and URL filter compatibility in safe structured details.
- Run logs never expose raw cookie, token, authorization, proxy credential, HTML, or raw Vinted payload values. Cookie/token/session data is represented only as masked/fingerprinted markers; short values show no characters.
- Persisted event details have one read contract for REST and SSE: strict safe markers survive their JSONB roundtrip under marker containers, sensitive fields and sanitized headers, while caller-forged marker shapes and raw secret canaries are redacted before persistence and cannot reappear on either transport.
- Run logs show Redis availability, seen-cache hits/misses, seen-cache marks, detail fetch start/success/error/skipped, filter pass/discard, item persisted/reused, and opportunity created/skipped events.
- Run logs show catalog session context checks before the API request: impersonate, CSRF, anon id, access token, DataDome cookie, `v_udt`, locale, viewport, Vinted `x-screen`, egress country match, and any missing required key.
- Run logs show Vinted session lifecycle decisions: selected existing session, automatic preparation start/end, proxy sticky marker, probe outcome, use count, max requests, stop-after-use limit, session end reason, and recovery action.
- Run log timestamps are assigned per event and must not reuse a transaction-wide database timestamp.
- A rolled-back monitor event leaves neither an event nor outbox work. A committed event leaves exactly one pending outbox row until a serialized publisher atomically creates its unique publication cursor and removes the pending row.
- Migration 0017 backfills only committed monitor events missing a publication. Runtime publication reads bounded indexed outbox batches; it does not perform a historical anti-join on every SSE poll.
- Tail startup drains only the outbox rows visible in one repeatable PostgreSQL snapshot while holding the global publication lock, so a continuously active producer cannot prevent `stream_ready`. Events committed after that boundary, including a previously reserved lower event ID, receive later cursors and remain resumable.
- Normal SSE polls try the publication lock without waiting. Contention yields an empty poll so heartbeat and disconnect checks continue; a later poll publishes the pending rows after the tail fence is released.
- The Monitors view owns exactly one SSE connection while it is open. Renders and statistics refreshes do not recreate it, leaving the view closes it, and returning or reconnecting resumes from the last received publication cursor.
- Historical REST loading starts only after `stream_ready`; explicit per-monitor history-loaded state is independent from live event presence. REST history and live events are merged by event ID, including live events received while the historical request is still pending; each event appears at most once.
- Only `run_succeeded` and `run_failed` schedule a debounced runtime refresh. A terminal batch refreshes current sources, the affected monitor run histories and statistics once; opportunities refresh only when a terminal reports a positive `opportunities_created` count or omits that count. It does not refresh the unused global run list.
- A terminal batch already received remains pending across navigation away from Monitors and is applied or retried without requiring another terminal event.
- The monitor log follows the newest event while the reader remains at the bottom. Scrolling upward suspends forced scrolling and exposes a new-event control that returns to the tail on desktop and mobile.
- An SSE error or 22.5-second silence is presented as a reconnecting state; the dashboard closes the failed/quiet instance, performs one bounded authentication revalidation and creates one replacement after three seconds with the latest explicit publication cursor. The liveness timer starts during `CONNECTING` and is rearmed by `open`, `stream_ready`, `stream_heartbeat` and valid monitor events. If that replacement also fails during a prolonged outage, it may schedule the next sequential attempt; only one timer, one auth check and one current connection may exist at any instant.
- Every `open`, `error`, `stream_ready`, `stream_heartbeat` and `monitor_event` callback is scoped to the `EventSource` instance that registered it. Once replaced or closed, stale callbacks are inert: they cannot change status/readiness, advance the cursor, append events, close the current stream or schedule another reconnect.
- Run logs show `baseline_snapshot_seeded` when session start seeds the initial catalog snapshot and `baseline_required` when an ordinary run is blocked because that snapshot no longer exists.
- Run configuration logs identify the evaluation contract, policy hash, description-only filter scope, detail mode, early-filter mode and head byte limit. Detail/filter logs expose received bytes, match counts and durations without response content.
- Rejected HTTP responses use a safe body observation containing lengths and type flags; response body snippets are never persisted or returned.
- The PWA Monitors view renders selected monitor accumulated logs as a non-interactive operational checklist: one wrapped multi-line block per event with run id, exact time, state, label, method, URL, status, ms, recovered/missing context, safe cookie flags, API parameters, and failure/skip reason when available, whether the monitor is active or stopped.
- Redacted JSON `run_events.details` remains available through API/database for technical audit, but the main PWA log timeline does not render expandable JSON details.
- The selected monitor log console supports local level filtering and text search without mutating persisted `run_events`.
- The selected monitor log timeline can be cleared locally with `Limpiar vista`; this stores the currently visible event IDs as hidden in the browser session and never deletes persisted `run_events`.

For manual opportunity-pipeline diagnosis, preserve the run id and the events for configuration, catalog response, Redis seen result, each candidate detail/early-filter result, filter decision, persistence/opportunity result, Redis terminal transition and final run status. Export only API/PWA-redacted events; never attach `.env`, raw cookies, proxy URLs with userinfo, response bodies or complete HAR files.
- The PWA Monitors view is organized as three top-level cards: new monitor configuration, the single compact monitor table, and the selected-monitor detail. The table and detail are stacked instead of nested inside a parent card.
- Active monitors appear before inactive monitors in the PWA's single compact monitor table, using status chips and row styling instead of separate active/inactive sections, and show a selected-monitor detail with session summary, read-only configuration, performance card, logs, and a working stop control.
- Active recurring monitor detail does not show `Ejecutar ahora` because periodic execution is already configured; active manual detail shows exactly one explicit `Ejecutar ahora`.
- Every non-archived monitor can be selected from the compact monitor table to show active-session metrics or latest-session metrics above configuration, configuration editable only while stopped and without a non-terminal run, accumulated historical metrics, a default all-history full-width bar chart of `items_found` by time bucket, and accumulated logs so historical manual and recurring runs remain visible after the monitor stops.
- Monitor detail views with no sessions yet show no session/acumulated metric rows until the first launch produces data.
- The performance chart supports fixed operational ranges labeled `Minuto`, `Hora`, `Dia`, `Mes`, and `Todo`.
- Fixed performance chart ranges use deterministic current-period buckets: current minute by 5-second bucket, current hour by 5-minute bucket, current day by 1-hour bucket, and current calendar month by 1-day bucket.
- The month chart runs from day 1 at 00:00 to day 1 of the following month at 00:00, with the final visible X-axis mark at the next month boundary.
- The all-history performance chart range uses automatic buckets: 5-minute buckets up to 1 hour of history, 1-hour buckets up to 24 hours, 1-day buckets up to 90 days, and 1-month buckets after that.
- The performance chart labels the X axis as time and the Y axis as found items, and its tooltip shows the exact bucket interval plus found/run counts.
- The performance chart renders each bar as the exact bucket interval from `bucket_start` to `bucket_end`; bars must not be centered on bucket midpoints or rely on automatic categorical bar width.
- The performance chart draws a vertical marker for the active session start when it falls inside the visible range.
- Idle inactive monitors appear after active or draining monitors in the compact monitor table; selecting an inactive monitor shows editable configuration and launch/archive controls only when no run is non-terminal, plus historical performance and archive confirmation without implying the monitor is running.
- The PWA can receive monitor log updates from the existing SSE stream.
- The PWA monitor detail shows supported, ignored, and unsupported URL filters; unsupported filters block session start and other traffic-producing actions.
- Redis hits avoid DB item lookups and detail fetches for already seen monitor candidates.
- Candidates with an already existing opportunity for the same monitor are marked seen and skipped before filter/detail work if Redis lost that seen state.
- If Redis is unavailable, the affected run fails and the monitor is stopped/blocked until retried.
- Anonymous public cookies/tokens are encrypted at rest only in prepared Vinted sessions and are isolated per monitor plus proxy sticky identity.
- Proxy settings are global; monitor-level proxy selection is not exposed or accepted.
- Proxy profile creation/editing accepts proxy connection data and country only; `locale`, `Accept-Language`, viewport and Vinted `x-screen` are not user-editable API/PWA inputs and are recalculated from internal presets when the country changes.
- Direct requests behave exactly as monitor runs only when global direct fallback is enabled in the UI, `VINTED_DIRECT_CATALOG_ENABLED=true`, and no matching proxy is available.
- Worker retry attempts, browser impersonation, human delay ranges, DataDome challenge penalty, and sticky proxy username template are deployment settings and are not editable from the PWA.
- Proxy passwords stored through the UI are encrypted at rest. The username remains in plaintext and is returned raw by the current API even though the PWA renders its mask; 14.12.8 closes that credential-contract gap.
- Proxy pool entries can be `own`, `datacenter`, or `residential`; target-specific/special proxy classes are not exposed for Vinted.
- If a proxy request fails, only the affected run/source is failed and logged with redacted details.
- Repeated items in the same monitor cannot generate duplicate opportunities.
- A globally known item can still create an opportunity in a different monitor if that monitor sees it.

## Verification

- Unit tests for next-run calculation.
- Unit tests for activation-time persistence of the first recurring deadline, the 60-second jitter floor, and persisted-deadline precedence over stale scheduler runtime state.
- PostgreSQL/API tests for missing, fresh, expired, malformed, naive and future producer heartbeat plus mutation-free recurring `503`; producer tests cover heartbeat during disabled/idle operation and scheduler polls longer than the heartbeat interval.
- Supervisor/watchdog tests cover invalid startup configuration, producer expiry grace, recurring-only locked stop, heartbeat recovery during lock acquisition, session/event persistence, DB-first Redis cleanup failure, and unexpected-error process termination.
- Real-container verification blocks producer progress without external traffic and observes worker exit plus Compose restart; a separate disposable Redis-loss case observes a non-zero worker exit/restart, unchanged expired heartbeat, live API/PWA unavailability and recovery after Redis returns. A QA recurring source/session/task proves watchdog database stop and visible Redis-cleanup failure, followed by complete cleanup and service restoration.
- SSE contract tests for tail startup, query/header cursor precedence, duplicate-free resume, backlog batches larger than 100, reconnect advice, heartbeat, disconnect, buffering/cache headers, and redaction.
- Real PostgreSQL/API verification persists one legitimate marker event and one forged/raw-canary event through the production writer, then confirms identical safe `details` through monitor REST and SSE and complete cleanup of event, outbox, publication and source state.
- PostgreSQL/outbox tests cover event commit and rollback, concurrent/inverted commits, serialized duplicate-free publication, atomic publication rollback, bounded batches, historical migration backfill and tail high-water behavior.
- PostgreSQL tests for inverted event commit order, monotonic duplicate-free publication, JSONB marker roundtrip, atomic activation rollback, concurrent initial admission at capacity one, repeated start rejection, locked-deadline revalidation, and durable window deferral.
- Unit tests for interval, jitter, allowed-window, and disabled-source validation.
- Unit tests for concurrency limit and per-source single-flight behavior.
- Unit tests for Redis hit, miss, processing lock, seen mark, policy-hash reevaluation, and Redis-unavailable failure.
- Unit tests confirming Redis cache contents do not include cookies, tokens, raw payloads, HTML, or proxy credentials.
- Live Playwright check through PWA/API/PostgreSQL/Redis plus the real scheduler queue/consumer with a 60-second interval and 10% jitter: one baseline, no immediate business run, later deduplicated scheduler runs, initial persisted deadline in `60..66` seconds, terminal ACK and bounded loopback-only cleanup.
- Confirm run records identify scheduler-triggered executions.
- Confirm a manual baseline is sessionless, its successful start opens one session, later manual runs reuse it, and stop/fail-stop closes it; confirm recurring session ownership separately.
- Confirm monitor stats aggregate session, historical, and chart bucket data.
- Confirm selecting inactive monitors still shows the all-history chart, accumulated counts, and accumulated log timeline after manual or stopped recurring runs.
- Confirm `Limpiar vista` hides the selected monitor's visible timeline without deleting events from `/api/monitors/{monitor_id}/events`, and new event IDs remain visible after the cleanup.
- Confirm monitor logs include run configuration resolution, selected egress, HTTP session creation/close, egress IP/country when available, Redis decisions, candidate evaluation, detail requirements, filter decisions, item persistence/reuse, opportunity outcomes, exact timestamps, and request durations in milliseconds.
- Confirm safe cookie/token/session/proxy markers use masked/fingerprinted values only and never include raw secret values.
- Confirm the PWA log console renders one non-interactive wrapped checklist block per event, level filtering, text search, no visible JSON details, and no horizontal overflow on mobile.
- Confirm the Monitors view renders three top-level cards for creation, list, and selected detail without nesting the table or detail inside another card.
- Confirm the compact monitor table selects active and inactive monitors, updates the full-width detail panel, and scrolls the detail into view on mobile without horizontal overflow.
- Confirm active monitor details show read-only configuration and stop/log controls; manual mode also shows one `Ejecutar ahora`, while recurring modes do not expose a manual override.
- Confirm inactive monitor launch is blocked while there are unsaved changes, save persists the configuration, and launch then starts using that persisted configuration without another PATCH.
- Confirm manual and recurring start create one zero-opportunity snapshot before activation; recurring activation exposes one later deadline and no immediate business run/task.
- Confirm a recurring monitor configured with `stop_after_vinted_session_uses=1` stops after one completed run, logs the limit, and leaves the encrypted Vinted session history available for diagnosis.
- Confirm inactive monitor start and other traffic-producing actions are blocked when URL filters are unsupported.
- Confirm idle inactive monitor details show editable configuration above the performance chart and use an in-app archive confirmation dialog; drain keeps both unavailable.
- Confirm two different monitors can run concurrently up to the global limit.
- Confirm a third due monitor waits when the global limit is reached.
- Confirm no monitor API or PWA path exposes proxy selection per monitor.
- Confirm proxy API/PWA writes reject manual `locale`, `Accept-Language`, viewport and Vinted `x-screen` fields while read views expose only the resolved context for diagnostics.
- Confirm scheduler capacity reflects active proxy capacity plus allowed direct capacity.
- Confirm periodic activation is blocked when scheduler is disabled or capacity is exhausted.
- Confirm run metadata records `egress_mode=proxy` with proxy details when a proxy is selected and `egress_mode=direct` when direct fallback is used.
- Confirm provider requests use the deterministic order `egress diagnostic` when configured, saved `/catalog?...` document URL, then `/api/v2/catalog/items`.
- Confirm repeated overlapping-monitor items use Redis monitor-scoped dedupe and do not duplicate opportunities within a monitor.
- Confirm Redis miss plus existing monitor opportunity is skipped before filters and logged as `candidate_existing_opportunity_skipped`.
- Confirm direct-disabled/no-proxy path leaves the monitor pending instead of running.
- Confirm proxy credentials are never returned or logged.
- Confirm anonymous session refresh failure marks only the affected run failed and does not stop the scheduler loop.
- Confirm redaction tests cover nested details, URLs, bearer tokens, cookies, token-like assignments, masked values, and fingerprints.
- Confirm PWA build succeeds after adding the log timeline and stream event fields.
- Confirm Playwright observes one pending SSE request while Monitors remains open, no repeated REST traffic while idle, sub-two-second single delivery, one directed terminal refresh, cursor resume after navigation, and tail-follow/new-event behavior on desktop and mobile.
- Confirm Playwright restarts the live API while Monitors is open, keeps at most one current replacement SSE with `last_event_id`, proves late callbacks from a closed request do not change cursor/events or create another stream, then navigates away/back and renders one backend-produced local event exactly once.
