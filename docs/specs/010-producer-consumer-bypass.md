# 010 Producer-Consumer Architecture with DataDome Bypass

## Goal

Migrate the worker from a synchronous-coupled model (scheduler → `execute_monitor_run` → `httpx`) to a **Producer-Consumer** pattern with Redis queue, using `curl_cffi` for TLS fingerprint bypass and residential proxies with dynamic sticky sessions (UUID per task), with multi-layer anti-bot evasion.

## Scope

- Scheduler acts as Producer: evaluates timing, jitter, time windows, and enqueues `MonitorTask` to Redis.
- Workers act as Consumers: listen on Redis queue via `BLPOP`, process tasks with full evasion lifecycle.
- All HTTP traffic migrated from `httpx` to `curl_cffi` with `impersonate` for TLS/JA3 and HTTP/2 fingerprint spoofing.
- Browser profile pool with coherent `impersonate` + `User-Agent` + `Sec-Ch-Ua*` per session.
- Residential proxy sticky sessions with dynamic UUID injection per task.
- DataDome challenge detection and response with escalation retry.
- Human-like micro-timing between bootstrap and catalog requests.
- Realistic navigation flow selection (Google referral, home navigation, internal referral).
- Proxy quality scoring with exponential cooldown on failures.
- Proactive degradation metrics emitted as run events.

## Interfaces

- Worker:
  - `SchedulerRunner` (producer): enqueues `MonitorTask` to Redis.
  - `TaskConsumer` (consumer): dequeues tasks, manages evasion lifecycle, calls `execute_monitor_run`.
  - `CurlCffiVintedCatalogProvider`: replaces `HttpVintedCatalogProvider`.
  - `BrowserProfile` pool: coherent browser identity per session.
  - `datadome` module: challenge detection and response.
- Redis:
  - Task queue: `vinted:task_queue` (LPUSH/BLPOP).
  - Seen cache: unchanged.
- Configuration:
  - `worker_consumer_count`: number of concurrent consumers.
  - `worker_blpop_timeout_seconds`: BLPOP timeout.
  - `worker_max_retry_attempts`: escalation retry limit.
  - `curl_impersonate_browser`: default impersonate value.
  - `human_delay_min_seconds`, `human_delay_max_seconds`: timing range.
  - Proxy sticky session format configurable via proxy profile username template.

## Acceptance Criteria

- No `httpx` import exists anywhere in the codebase.
- `curl_cffi` is the only HTTP client library used.
- Every HTTP request to Vinted uses `impersonate` with a browser profile.
- Bootstrap and catalog requests share the same `curl_cffi.Session`, same proxy IP, same cookies.
- Each task generates a unique UUID for the proxy sticky session.
- The proxy session UUID is discarded after the task completes.
- DataDome challenges are detected before processing catalog results.
- Challenge detection triggers IP discard, new UUID, and retry with escalation.
- Human-like delay (1.2-3.8s, non-uniform distribution) is applied between bootstrap and catalog.
- Navigation flow is randomly selected per task (Google/home/internal referer).
- `Sec-Ch-Ua*` headers match the `impersonate` version exactly.
- Header order matches real Chrome browser order.
- Proxy failures use exponential cooldown instead of linear.
- Scheduler enqueues tasks to Redis instead of executing them directly.
- Workers consume tasks via BLPOP.
- Manual runs bypass the queue but use the same evasion stack.
- Run events include `browser_profile`, `session_id`, `datadome_cookie`, and `bootstrap_duration_ms`.
- Degradation metrics are emitted as run events.
- All existing business logic (deduplication, filters, opportunities) is preserved.

## Verification

- `ruff check backend/src backend/alembic` passes.
- `scripts/check_ja3.py` confirms correct JA3 fingerprint.
- `scripts/check_datadome.py` confirms bootstrap + catalog flow works.
- `scripts/inspect_vinted_session.py` captures real Chrome reference.
- `scripts/compare_fingerprints.py` shows no critical differences.
- Docker build succeeds with `curl-cffi` installed.
- Manual run from PWA uses `curl_cffi` (visible in run event logs).
- Scheduler enqueues and consumers process tasks (Redis queue drains to 0).
- Exponential cooldown on proxy failures verified.
