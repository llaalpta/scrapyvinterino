# 008 Bounded Concurrent Scheduler and Runtime Cache

## Goal

Automatically execute active opportunity monitors on safe, bounded intervals with enough concurrency and runtime cache support to keep opportunity alerts fast without relying on post-MVP optimizations.

## Scope

- Enable or disable scheduler globally.
- Enable, pause, stop, or archive each monitor.
- Start and stop monitor execution using the monitor's current filters, cadence, duration/window mode, and optional proxy profile.
- Start a monitor for a bounded duration from now, with `monitor_until` stored on the monitor.
- Configure interval seconds per monitor, default `300`, minimum `60`, maximum `3600`.
- Add jitter/randomization between runs, default `20%`, minimum `0%`, maximum `50%`.
- Support one daily allowed execution window configured by start/end timepickers and stored as `HH:MM-HH:MM`.
- Record scheduler-triggered errors in the same run/error model.
- Run multiple active monitors concurrently with explicit limits.
- Allow at most `2` active monitor runs globally by default.
- Allow at most `1` active run per monitor.
- Keep monitor execution fair so one noisy monitor cannot starve others.
- Maintain a process-local global cache mapping `vinted_item_id -> item_id`.
- Maintain a process-local per-monitor recent-seen cache for traceability acceleration.
- Use caches as acceleration only after successful commits; PostgreSQL remains the source of truth for monitor visibility and item identity.
- Isolate anonymous public Vinted session cookies per provider/run or per egress identity.
- Keep proxy usage optional and disabled by default.
- Support UI-managed proxy profiles with encrypted credentials.
- Assign proxy/session identity consistently for a monitor run; do not mix cookies across proxies.

## Out of Scope

- Distributed scheduling across multiple workers.
- Complex priority queues.
- Authenticated actions.
- Captcha solving or aggressive anti-bot bypassing.
- Mandatory residential proxy usage.
- Persisting Vinted anonymous cookies or tokens.
- Returning or logging raw proxy credentials, cookies, or tokens.

## Interfaces

- Worker:
  - scheduler loop;
  - bounded monitor run executor;
  - process-local item cache;
  - isolated provider/session factory.
- API/PWA:
  - scheduler settings persisted in `app_settings`;
  - monitor pause/start/stop/archive controls;
  - proxy profile controls.
- Configuration:
  - deployment scheduler enable flag in `.env` as an operational gate;
  - UI scheduler enable flag in `app_settings`;
  - global concurrency limit, default `2`;
  - per-monitor concurrency limit, default `1`;
  - monitor interval seconds: default `300`, minimum `60`, maximum `3600`;
  - monitor jitter percent: default `20`, minimum `0`, maximum `50`;
  - optional allowed windows as local `HH:MM-HH:MM` ranges;
  - scheduler timezone, default `Europe/Madrid`;
  - optional proxy enable flag and proxy URL fallback;
  - optional UI-managed proxy profiles.
- Database:
  - `app_settings`;
  - `search_sources.scheduler_config`;
  - `search_sources.monitor_mode`, `duration_minutes`, `monitor_until`, `next_run_at`, `filter_rule_ids`, and `proxy_profile_id`;
  - `monitor_sessions` only as legacy history;
  - `proxy_profiles`;
  - `runs.trigger`;
  - `items`;
  - `source_seen_items`;
  - `errors`.

## Acceptance Criteria

- Scheduler can be disabled completely.
- A monitor can be paused without deleting it.
- Runs are not triggered outside configured local-time windows.
- Time window UI exposes one start time and one end time; empty start/end means no daily window restriction.
- A bounded monitor started for N minutes stores `monitor_until = now + N minutes`.
- Launching a bounded monitor from the PWA stores the config and immediately executes one run.
- Expired active monitors are stopped before scheduler planning.
- Jitter prevents fixed exact polling intervals.
- Scheduler failures are logged without stopping the worker.
- Invalid scheduler config is rejected clearly: interval outside `60..3600`, jitter outside `0..50`, malformed allowed windows, unsupported keys such as `pause_windows`, or an invalid scheduler timezone.
- No more than `2` monitor runs execute at the same time by default.
- The same active monitor never has two active runs at the same time.
- Manual and scheduler-triggered runs share the same item identity, monitor dedupe, detail fetch, source traceability, redaction, and error behavior.
- Runtime cache stores only safe IDs and minimal timestamps: `vinted_item_id`, `item_id`, and recent monitor visibility metadata.
- Runtime cache never stores cookies, tokens, HTML, raw Vinted payloads, proxy credentials, addresses, or payment data.
- Cache hits can avoid unnecessary DB item lookups and detail fetches, but cannot create or suppress `items_new` without committed DB state.
- Cache hits must not suppress required `source_seen_items` first/last run updates.
- Cache entries are written or refreshed only after the owning transaction commits.
- If the process restarts or the cache is empty, scheduler behavior remains correct by falling back to PostgreSQL.
- Anonymous public cookies/tokens are kept in memory only and isolated per provider/session run or per proxy identity.
- Proxy settings are optional; when disabled, direct requests behave exactly as in manual runs.
- Proxy credentials stored through the UI are encrypted at rest and never returned raw by API.
- Proxy enabled without a usable proxy URL/profile is invalid config and fails clearly with redacted details.
- If a proxy request fails, only the affected run/source is failed and logged with redacted details.
- Repeated items in the same monitor cannot generate duplicate opportunities.
- A globally known item can still create an opportunity in a different monitor if that monitor sees it.

## Verification

- Unit tests for next-run calculation.
- Unit tests for interval, jitter, allowed-window, and disabled-source validation.
- Unit tests for concurrency limit and per-source single-flight behavior.
- Unit tests for cache hit, cache miss, post-commit cache update, and rollback/no-cache-update for both global and per-source caches.
- Unit test that a cache hit still records or refreshes `source_seen_items`.
- Unit tests confirming cache contents do not include cookies, tokens, raw payloads, HTML, or proxy credentials.
- Manual check with short interval in local Docker.
- Confirm run records identify scheduler-triggered executions.
- Confirm two different monitors can run concurrently up to the global limit.
- Confirm a third due monitor waits when the global limit is reached.
- Confirm repeated overlapping-monitor items use item identity plus monitor-scoped dedupe and do not duplicate opportunities within a monitor.
- Confirm proxy disabled path uses direct provider behavior.
- Confirm proxy disabled path passes no proxy config to the provider.
- Confirm proxy enabled path passes configured outbound Vinted proxy to the provider without returning or logging credentials.
- Confirm proxy enabled without URL fails with a clear redacted error.
- Confirm anonymous session refresh failure marks only the affected run failed and does not stop the scheduler loop.
