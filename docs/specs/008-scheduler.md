# 008 Bounded Concurrent Scheduler and Runtime Cache

## Goal

Automatically execute active opportunity monitors on safe, bounded intervals with enough concurrency and runtime cache support to keep opportunity alerts fast without relying on post-MVP optimizations.

## Scope

- Enable or disable scheduler globally.
- Enable, stop, or archive each monitor.
- Treat inactive monitors as configured but not launched; active monitors are launched for recurring execution.
- Treat running/executing as run state, not as persistent monitor state.
- Start and stop recurring monitor execution using the monitor's current filters, cadence, and duration/window mode.
- Persist each recurring activation as a monitor session until it is stopped, archived, expired, or blocked by a stopping failure.
- Allow punctual/manual monitor execution from an inactive monitor for testing without activating scheduler state.
- Start a monitor for a bounded duration from now, with `monitor_until` stored on the monitor.
- Configure interval seconds per monitor, default `300`, minimum `60`, maximum `3600`.
- Add jitter/randomization between runs, default `20%`, minimum `0%`, maximum `50%`.
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
- Isolate anonymous public Vinted session cookies per provider/run or per egress identity.
- Keep proxy usage optional and globally managed by the scheduler.
- Support UI-managed proxy profiles with encrypted credentials.
- Assign proxy/session identity consistently for a run; do not mix cookies across proxies.
- Use active global proxies before direct outbound access; direct access is allowed only when the global scheduler setting permits it.

## Interfaces

- Worker:
  - scheduler loop;
  - bounded monitor run executor;
  - Redis seen cache client;
  - isolated provider/session factory.
- API/PWA:
  - scheduler settings persisted in `app_settings`;
  - monitor inactive/start/stop/archive controls;
  - monitor historical stats endpoint for active monitor performance cards;
  - global proxy pool and scheduler runtime controls.
- Configuration:
  - ownership rule: `.env` owns deployment, secrets, worker and anti-bot defaults; UI `app_settings` owns daily operation only;
  - deployment scheduler enable flag in `.env` as an operational gate;
  - UI scheduler enable flag in `app_settings`;
  - global concurrency limit, default `2`;
  - per-monitor concurrency limit, default `1`;
  - direct-without-proxy enable flag and direct concurrency limit;
  - per-proxy run concurrency limit stored on each proxy profile;
  - catalog results per run, detail fetch candidate limit, request timeout, proxy cooldown, and stop-after-failures settings;
  - monitor interval seconds: default `300`, minimum `60`, maximum `3600`;
  - monitor jitter percent: default `20`, minimum `0`, maximum `50`;
  - optional allowed windows as local `HH:MM-HH:MM` ranges;
  - scheduler timezone, default `Europe/Madrid`;
  - optional UI-managed proxy profiles.
- Run event log:
  - `level`: `debug`, `info`, `warning`, or `error`;
  - stable machine phase such as `run_started`, `redis_check_error`, `anonymous_session_bootstrap_success`, `catalog_api_request_success`, `detail_fetch_error`, `item_discarded`, or `opportunity_created`;
  - `duration_ms`, `status_code`, `timeout_ms`, `attempt`, and `retry_reason` when available;
  - safe session markers with name, length, masked preview, and fingerprint, never the full value.
- Database:
  - `app_settings`;
  - `search_sources.scheduler_config`;
  - `search_sources.monitor_mode`, `duration_minutes`, `monitor_until`, `next_run_at`, and `filter_rule_ids`;
  - `monitor_sessions`;
  - `proxy_profiles`;
  - `runs.trigger` and optional `runs.monitor_session_id`;
  - `items` for opportunity items only;
  - `errors`.

## Acceptance Criteria

- Scheduler can be disabled completely.
- A monitor can be stopped without deleting it.
- A new monitor is inactive until launched.
- Punctual/manual execution can run from inactive state, creates a monitor session, closes that session after the run, and returns to inactive state.
- Runs are not triggered outside configured local-time windows.
- Time window UI exposes one start time and one end time; empty start/end means no daily window restriction.
- A bounded monitor started for N minutes stores `monitor_until = now + N minutes`.
- Launching a recurring monitor from the PWA stores the config, marks it active, and immediately executes one run.
- Launching a recurring monitor is rejected when the effective scheduler is disabled or no scheduler capacity is available.
- Launching any monitor creates a monitor session; recurring sessions remain active until stopped/expired/failed, while punctual sessions close after the run.
- The scheduler only considers active recurring monitors.
- Expired active monitors are stopped before scheduler planning.
- Jitter prevents fixed exact polling intervals.
- Scheduler failures are logged without stopping the worker.
- Invalid scheduler config is rejected clearly: interval outside `60..3600`, jitter outside `0..50`, malformed allowed windows, unsupported keys such as `pause_windows`, or an invalid scheduler timezone.
- No more than `2` monitor runs execute at the same time by default.
- The same active monitor never has two active runs at the same time.
- The scheduler uses active healthy proxies from the global pool before direct access.
- Proxy capacity is the sum of active healthy proxy profile `max_concurrent_runs` values; there is no separate UI-level global per-proxy cap.
- When no proxy is available, direct access is used only if the global setting allows it.
- If neither proxy nor direct capacity is available, a periodic monitor is not activated or run.
- Manual and scheduler-triggered runs share the same Redis seen state, item identity, monitor dedupe, detail fetch, redaction, and error behavior.
- Redis stores only safe IDs and timestamps: monitor id, policy hash, `vinted_item_id`, processing markers, and seen markers.
- Redis never stores cookies, tokens, HTML, raw Vinted payloads, proxy credentials, addresses, or payment data.
- Run logs show operational progress with sanitized URLs, status codes, durations, egress mode, proxy profile id when used, auth mode, and safe counts only; they never expose cookie or token values.
- Run logs expose anonymous session diagnostics using masked/fingerprinted markers only; short values show no characters.
- Run logs show Redis availability, seen-cache hits/misses, detail fetch start/success/error/skipped, filter pass/discard, and opportunity created/skipped events.
- The PWA Monitors view renders selected monitor accumulated logs as a readable timeline/console with run id, level, label, timestamp, ms, status, URL, message, and collapsible details, whether the monitor is active or stopped.
- The selected monitor log timeline can be cleared locally with `Limpiar vista`; this stores the currently visible event IDs as hidden in the browser session and never deletes persisted `run_events`.
- The PWA Monitors view is organized as three top-level cards: new monitor configuration, the single compact monitor table, and the selected-monitor detail. The table and detail are stacked instead of nested inside a parent card.
- Active monitors appear before inactive monitors in the PWA's single compact monitor table, using status chips and row styling instead of separate active/inactive sections, and show a selected-monitor detail with session summary, read-only configuration, performance card, logs, and a working stop control.
- Active monitor detail does not show an `Ejecutar ahora` button because periodic execution is already configured.
- Every non-archived monitor can be selected from the compact monitor table to show active-session metrics or latest-session metrics above configuration, stopped-only editable configuration, accumulated historical metrics, a default all-history full-width bar chart of `items_found` by time bucket, and accumulated logs so historical and punctual runs remain visible after the monitor stops.
- Monitor detail views with no sessions yet show no session/acumulated metric rows until the first launch produces data.
- The performance chart supports fixed operational ranges labeled `Minuto`, `Hora`, `Dia`, `Mes`, and `Todo`.
- Fixed performance chart ranges use deterministic current-period buckets: current minute by 5-second bucket, current hour by 5-minute bucket, current day by 1-hour bucket, and current calendar month by 1-day bucket.
- The month chart runs from day 1 at 00:00 to day 1 of the following month at 00:00, with the final visible X-axis mark at the next month boundary.
- The all-history performance chart range uses automatic buckets: 5-minute buckets up to 1 hour of history, 1-hour buckets up to 24 hours, 1-day buckets up to 90 days, and 1-month buckets after that.
- The performance chart labels the X axis as time and the Y axis as found items, and its tooltip shows the exact bucket interval plus found/run counts.
- The performance chart renders each bar as the exact bucket interval from `bucket_start` to `bucket_end`; bars must not be centered on bucket midpoints or rely on automatic categorical bar width.
- The performance chart draws a vertical marker for the active session start when it falls inside the visible range.
- Inactive monitors appear after active monitors in the compact monitor table; selecting an inactive monitor shows editable configuration, launch/archive controls, historical performance, and archive confirmation without implying the monitor is running.
- The PWA can receive monitor log updates from the existing SSE stream.
- Redis hits avoid DB item lookups and detail fetches for already seen monitor candidates.
- If Redis is unavailable, the affected run fails and the monitor is stopped/blocked until retried.
- Anonymous public cookies/tokens are kept in memory only and isolated per provider/session run or per proxy identity.
- Proxy settings are global; monitor-level proxy selection is not exposed or accepted.
- Direct requests behave exactly as monitor runs only when global direct fallback is enabled and no proxy is available.
- Worker retry attempts, browser impersonation, human delay ranges, DataDome challenge penalty, and sticky proxy username template are deployment settings and are not editable from the PWA.
- Proxy credentials stored through the UI are encrypted at rest and never returned raw by API.
- Proxy pool entries can be `own`, `datacenter`, or `residential`; target-specific/special proxy classes are not exposed for Vinted.
- If a proxy request fails, only the affected run/source is failed and logged with redacted details.
- Repeated items in the same monitor cannot generate duplicate opportunities.
- A globally known item can still create an opportunity in a different monitor if that monitor sees it.

## Verification

- Unit tests for next-run calculation.
- Unit tests for interval, jitter, allowed-window, and disabled-source validation.
- Unit tests for concurrency limit and per-source single-flight behavior.
- Unit tests for Redis hit, miss, processing lock, seen mark, policy-hash reevaluation, and Redis-unavailable failure.
- Unit tests confirming Redis cache contents do not include cookies, tokens, raw payloads, HTML, or proxy credentials.
- Manual check with short interval in local Docker.
- Confirm run records identify scheduler-triggered executions.
- Confirm monitor sessions are created, closed, and associated to punctual runs, and created/associated/stopped for recurring runs.
- Confirm monitor stats aggregate session, historical, and chart bucket data.
- Confirm selecting inactive monitors still shows the all-history chart, accumulated counts, and accumulated log timeline after manual or stopped recurring runs.
- Confirm `Limpiar vista` hides the selected monitor's visible timeline without deleting events from `/api/monitors/{monitor_id}/events`, and new event IDs remain visible after the cleanup.
- Confirm the Monitors view renders three top-level cards for creation, list, and selected detail without nesting the table or detail inside another card.
- Confirm the compact monitor table selects active and inactive monitors, updates the full-width detail panel, and scrolls the detail into view on mobile without horizontal overflow.
- Confirm active monitor details show read-only configuration, stop/log controls, and do not show save, archive, or punctual launch controls.
- Confirm inactive monitor details show editable configuration above the performance chart and use an in-app archive confirmation dialog.
- Confirm two different monitors can run concurrently up to the global limit.
- Confirm a third due monitor waits when the global limit is reached.
- Confirm no monitor API or PWA path exposes proxy selection per monitor.
- Confirm scheduler capacity reflects active proxy capacity plus allowed direct capacity.
- Confirm periodic activation is blocked when scheduler is disabled or capacity is exhausted.
- Confirm run metadata records `egress_mode=proxy` with proxy details when a proxy is selected and `egress_mode=direct` when direct fallback is used.
- Confirm repeated overlapping-monitor items use Redis monitor-scoped dedupe and do not duplicate opportunities within a monitor.
- Confirm direct-disabled/no-proxy path leaves the monitor pending instead of running.
- Confirm proxy credentials are never returned or logged.
- Confirm anonymous session refresh failure marks only the affected run failed and does not stop the scheduler loop.
- Confirm redaction tests cover nested details, URLs, bearer tokens, cookies, token-like assignments, masked values, and fingerprints.
- Confirm PWA build succeeds after adding the log timeline and stream event fields.
