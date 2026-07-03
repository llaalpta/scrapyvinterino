# 008 Bounded Concurrent Scheduler and Runtime Cache

## Goal

Automatically execute configured sources on safe, bounded intervals with enough concurrency and runtime cache support to keep future opportunity alerts fast without relying on post-MVP optimizations.

## Scope

- Enable or disable scheduler globally.
- Enable or disable each source.
- Configure interval seconds per source, default `300`, minimum `60`, maximum `3600`.
- Add jitter/randomization between runs, default `20%`, minimum `0%`, maximum `50%`.
- Support allowed execution windows.
- Record scheduler-triggered errors in the same run/error model.
- Run multiple sources concurrently with explicit limits.
- Allow at most `2` active source runs globally by default.
- Allow at most `1` active run per source.
- Keep source execution fair so one noisy source cannot starve others.
- Maintain a process-local global cache mapping `vinted_item_id -> item_id`.
- Maintain a process-local per-source recent-seen cache for traceability acceleration.
- Use caches as acceleration only after successful commits; PostgreSQL remains the source of truth for global dedupe.
- Isolate anonymous public Vinted session cookies per provider/run or per egress identity.
- Keep proxy usage optional and disabled by default.
- Support one configured proxy URL initially, with a future-compatible shape for a proxy pool.
- Assign proxy/session identity consistently for a run; do not mix cookies across proxies.

## Out of Scope

- Distributed scheduling across multiple workers.
- Complex priority queues.
- Authenticated actions.
- Captcha solving or aggressive anti-bot bypassing.
- Mandatory residential proxy usage.
- Persisting Vinted anonymous cookies or tokens.
- Persisting proxy credentials outside ignored local configuration.

## Interfaces

- Worker:
  - scheduler loop;
  - bounded run executor;
  - process-local item cache;
  - isolated provider/session factory.
- API/PWA:
  - scheduler settings persisted in `app_settings`;
  - source pause/enable controls.
- Configuration:
  - deployment scheduler enable flag in `.env` as an operational gate;
  - UI scheduler enable flag in `app_settings`;
  - global concurrency limit, default `2`;
  - per-source concurrency limit, default `1`;
- source interval seconds: default `300`, minimum `60`, maximum `3600`;
  - source jitter percent: default `20`, minimum `0`, maximum `50`;
  - optional allowed windows as local `HH:MM-HH:MM` ranges;
  - scheduler timezone, default `Europe/Madrid`;
  - optional proxy enable flag and proxy URL.
- Database:
  - `app_settings`;
  - `search_sources.scheduler_config`;
  - `runs.trigger`;
  - `items`;
  - `source_seen_items`;
  - `errors`.

## Acceptance Criteria

- Scheduler can be disabled completely.
- A source can be paused without deleting it.
- Runs are not triggered outside configured local-time windows.
- Jitter prevents fixed exact polling intervals.
- Scheduler failures are logged without stopping the worker.
- Invalid scheduler config is rejected clearly: interval outside `60..3600`, jitter outside `0..50`, malformed allowed windows, unsupported keys such as `pause_windows`, or an invalid scheduler timezone.
- No more than `2` source runs execute at the same time by default.
- The same source never has two active runs at the same time.
- Manual and scheduler-triggered runs share the same global dedupe, detail fetch, source traceability, redaction, and error behavior.
- Runtime cache stores only safe IDs and minimal timestamps: `vinted_item_id`, `item_id`, and recent source visibility metadata.
- Runtime cache never stores cookies, tokens, HTML, raw Vinted payloads, proxy credentials, addresses, or payment data.
- Cache hits can avoid unnecessary DB item lookups and detail fetches, but cannot create or suppress `items_new` without committed DB state.
- Cache hits must not suppress required `source_seen_items` first/last run updates.
- Cache entries are written or refreshed only after the owning transaction commits.
- If the process restarts or the cache is empty, scheduler behavior remains correct by falling back to PostgreSQL.
- Anonymous public cookies/tokens are kept in memory only and isolated per provider/run or per proxy identity.
- Proxy settings are optional; when disabled, direct requests behave exactly as in manual runs.
- Proxy enabled without a usable proxy URL is invalid config and fails clearly with redacted details.
- If a proxy request fails, only the affected run/source is failed and logged with redacted details.
- Overlapping sources cannot generate duplicate detail fetches or future alert candidates for the same globally known item.

## Verification

- Unit tests for next-run calculation.
- Unit tests for interval, jitter, allowed-window, and disabled-source validation.
- Unit tests for concurrency limit and per-source single-flight behavior.
- Unit tests for cache hit, cache miss, post-commit cache update, and rollback/no-cache-update for both global and per-source caches.
- Unit test that a cache hit still records or refreshes `source_seen_items`.
- Unit tests confirming cache contents do not include cookies, tokens, raw payloads, HTML, or proxy credentials.
- Manual check with short interval in local Docker.
- Confirm run records identify scheduler-triggered executions.
- Confirm two different sources can run concurrently up to the global limit.
- Confirm a third due source waits when the global limit is reached.
- Confirm repeated overlapping-source items use global dedupe and do not fetch detail twice.
- Confirm proxy disabled path uses direct provider behavior.
- Confirm proxy disabled path passes no proxy config to the provider.
- Confirm proxy enabled path passes configured outbound Vinted proxy to the provider without persisting credentials.
- Confirm proxy enabled without URL fails with a clear redacted error.
- Confirm session refresh failure marks only the affected run failed and does not stop the scheduler loop.
