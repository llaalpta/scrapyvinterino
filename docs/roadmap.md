# Roadmap

This roadmap decides what to do next. Work on the first incomplete item in `Now` unless there is an explicit product decision to change priority.

Status values:

- `not-started`
- `in-progress`
- `blocked`
- `done`

## Now

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 1 | done | Search sources | `docs/specs/001-search-sources.md` | Configure and list Vinted catalog URLs from API/PWA. |
| 2 | done | Vinted catalog research | `docs/specs/002-vinted-catalog-research.md` | Discover how catalog data is delivered and define provider contract. |
| 3 | done | Manual runs | `docs/specs/003-manual-run.md` | Trigger source execution manually and record run state. |
| 4 | done | Item persistence | `docs/specs/004-item-persistence.md` | Store normalized public catalog items. |
| 5 | done | Fast detection and seen tracking | `docs/specs/005-deduplication-and-opportunities.md` | Use fast catalog JSON, catalog identity, monitor traceability, and bounded detail fetch. |
| 6 | done | Bounded concurrent scheduler and runtime cache | `docs/specs/008-scheduler.md` | Run sources concurrently with limits, jitter, isolated anonymous sessions, and global item cache before alerting. |
| 7 | done | Results and opportunities browser | `docs/specs/007-opportunities-table.md` | Paginated results, source scrape traceability, filters, mobile cards, and separate tabs before creating opportunities. |
| 8 | done | Frontend structure baseline | `docs/development.md` | Split the PWA into layout, feature, shared component, helper, hook, and style modules before adding local filters. |
| 9 | done | Session exclusion filters, monitor, and proxy pool | `docs/specs/006-local-filters.md` | Launch monitor sessions with monitor-owned exclusion terms, opportunities, run monitor, and encrypted proxy profiles. |
| 10 | done | Source archive, time windows, and timed sessions | `docs/specs/001-search-sources.md`, `docs/specs/008-scheduler.md` | Archive sources safely, configure one daily time window with timepickers, and launch bounded sessions from now. |
| 11 | done | Opportunity monitors model correction | `docs/specs/001-search-sources.md`, `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/006-local-filters.md`, `docs/specs/008-scheduler.md` | Treat configured Vinted searches as reusable monitors with per-monitor dedupe, optional filters, and accumulated monitor metrics. |
| 12 | done | Professional monitor logs | `docs/specs/008-scheduler.md`, `docs/security.md` | Structured run events with levels, safe session diagnostics, durations, Redis/cache events, and PWA log timeline. |
| 13 | done | Producer-Consumer + DataDome bypass | `docs/specs/010-producer-consumer-bypass.md` | Prepared residential session, catalog baseline, reliable queue recovery and five-item public-detail run verified live on 2026-07-11. |
| 13.1 | done | Prepared session hardening | `docs/specs/010-producer-consumer-bypass.md` | Require strict prepared context (`datadome`, `__cf_bm`, CSRF, anon, access, `v_udt`, geo/locale/screen) before a monitor-owned session becomes reusable. |
| 13.2 | done | Public item document enrichment | `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/010-producer-consumer-bypass.md` | Structural JSON-LD/Next Flight parsing, resilient Redis retries, public availability/pricing, complete direct-CDN photos, production-path detail probe, and accessible opportunity gallery verified against the supplied HAR plus backend/PWA tests. |
| 13.3 | done | Fast item detail pipeline | `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/010-producer-consumer-bypass.md` | Separate timings, recent sticky egress reuse, selective Flight parser and safe early-rejection shadow shipped; C2 remains canary-only because the live persistent C1 control was faster. |
| 14 | done | Fast opportunity pipeline with Redis seen cache | `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/006-local-filters.md`, `docs/specs/007-opportunities-table.md`, `docs/specs/008-scheduler.md` | Make Redis mandatory for monitor seen state, persist only opportunities as product results, and remove seen-results/session legacy. |
| 14.1 | done | Notification-ready opportunity contract | `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/006-local-filters.md` | Description-only filtering, versioned Redis policy, optional catalog views, safe single-request early rejection and redacted diagnostics independently audited before notification delivery. |
| 14.2 | done | Practical SDD workflow governance | `AGENTS.md`, `docs/sdd-process.md`, `docs/adr/0002-sdd-workflow.md` | Granular planning branches, one-task implementation branches, confirmation/integration gates, proportional integration-first acceptance, bounded automatic audits and fail-stop defaults documented; semantic checks and three-pass independent audit completed. |
| 14.3 | done | Atomic initial scheduler admission | `docs/specs/008-scheduler.md` | Transaction-scoped advisory lock and one-commit activation shipped; forced-overlap PostgreSQL race produced one `201`/one `409` with exact winner/loser state, rollback left no residue, focused gates and independent audit passed without external traffic. |
| 14.4 | not-started | Scheduler producer availability | `docs/specs/008-scheduler.md`, `docs/deployment.md` | After 14.3; branch `fix/scheduler-producer-availability`; heartbeat-backed API/PWA availability and mutation-free `503` when the producer is absent. No external traffic. |
| 14.5 | not-started | Scheduler watchdog fail-stop | `docs/specs/008-scheduler.md`, `docs/deployment.md` | After 14.4; branch `fix/scheduler-watchdog-fail-stop`; worker self-exit/restart, watchdog stop and API-owned migration startup gate. No external traffic. |
| 14.6 | not-started | Real recurring cadence closure | `docs/specs/008-scheduler.md`, `docs/010-producer-consumer-implementation.md` | After 14.5; branch `verify/scheduler-recurring-cadence`; PWA launch plus one immediate and two recurring business runs. This is the only mandatory live Vinted task. |
| 14.7 | not-started | Transactional SSE outbox | `docs/specs/008-scheduler.md`, `docs/data-model.md` | After 14.6; branch `fix/sse-transactional-outbox`; migration 0017, atomic pending publication and duplicate-free durable cursors. No external traffic. |
| 14.8 | not-started | Persisted run-event redaction parity | `docs/specs/008-scheduler.md`, `docs/security.md` | After 14.7; branch `fix/run-event-persisted-redaction`; identical safe REST/SSE JSONB roundtrip and rejection of forged markers. No external traffic. |
| 14.9 | not-started | PWA EventSource reconnect race | `docs/specs/008-scheduler.md`, `docs/development.md` | After 14.8; branch `fix/pwa-eventsource-reconnect-race`; stale callbacks cannot affect the replacement stream and cursor resume delivers once. No external traffic. |
| 14.10 | not-started | Service lifecycle map | `docs/architecture.md`, `docs/deployment.md` | After 14.9; branch `docs/service-lifecycle-map`; verified startup, migrations, health, shutdown, restart, fail-stop and operator recovery. |
| 14.11 | not-started | Monitor command map | `docs/architecture.md`, `docs/specs/001-search-sources.md` | After 14.10; branch `docs/monitor-command-map`; create, edit and archive across PWA/API/DB without launching a run. Activation and stop belong to 14.13. |
| 14.12 | not-started | Public anonymous session map | `docs/architecture.md`, `docs/security.md`, `docs/specs/010-producer-consumer-bypass.md` | After 14.11; branch `docs/public-session-map`; preparation, reuse, refresh, expiry, challenge and invalidation without authenticated Vinted login. |
| 14.13 | not-started | Run lifecycle map | `docs/architecture.md`, `docs/specs/008-scheduler.md` | After 14.12; branch `docs/run-lifecycle-map`; activate/stop plus manual, recurring, duration, window, deadlines, terminal states and compensation. |
| 14.14 | not-started | Queue recovery map | `docs/architecture.md`, `docs/data-model.md`, `docs/specs/010-producer-consumer-bypass.md` | After 14.13; branch `docs/queue-recovery-map`; ready/reserved/processing/ACK/requeue/dead-letter and AOF recovery. |
| 14.15 | not-started | Cache and concurrency map | `docs/architecture.md`, `docs/data-model.md`, `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/008-scheduler.md` | After 14.14; branch `docs/cache-concurrency-map`; seen/processing/retry/finalizing ownership, locks and capacity. |
| 14.16 | not-started | Persistence, events and logs map | `docs/architecture.md`, `docs/data-model.md`, `docs/deployment.md`, `docs/security.md` | After 14.15; branch `docs/persistence-events-logs-map`; SQL transactions, outbox/SSE, stdout, redaction, PWA and retention. |
| 14.17 | not-started | Current-state documentation pruning | `docs/architecture.md`, `docs/deployment.md`, `docs/data-model.md`, `docs/security.md`, `docs/010-producer-consumer-implementation.md`, `docs/specs/001-search-sources.md`, `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/008-scheduler.md`, `docs/specs/010-producer-consumer-bypass.md` | After 14.16; branch `docs/current-state-pruning`; remove confirmed stale blockers and superseded descriptions without expanding to unrelated docs. |
| 14.18 | not-started | Deterministic integration-test settings and cleanup | `backend/tests/test_manual_runs.py`, `docs/development.md` | After 14.17; branch `fix/integration-test-settings`; remove host `.env`/encryption-key dependence and leaked scheduler settings from integration tests without weakening production contracts. |

### Operational closure contracts

`fix/scheduler-worker-liveness-sse-hardening` at `ba4b9cc` is a read-only patch source, not a merge candidate. Each hunk must be reapplied deliberately in 14.3-14.9 or rejected with a recorded reason. Keep that branch until the 14.9 equivalence check is complete.

| Item | Affected state and acceptance | Traffic, cleanup and exclusions |
| --- | --- | --- |
| 14.3 | FastAPI and PostgreSQL `search_sources`, `monitor_sessions` and `runs`. A PostgreSQL advisory lock serializes initial admission. Two concurrent starts at capacity one yield one winner and one `409`; activation, session, `next_run_at` and initial run are atomic. A pre-run failure leaves no active/session/deadline/run residue. Manual, duration and window behavior remains valid. | Real PostgreSQL with the Vinted provider controlled only at the external boundary. Delete QA sources, sessions, runs, events and Redis markers. Excludes heartbeat, watchdog and SSE. |
| 14.4 | Worker producer heartbeat in `app_settings`; public `GET /api/scheduler` adds `worker_available: boolean` and `worker_last_seen_at: UTC or null`, and `effective_enabled` requires a fresh producer. Missing, stale, malformed, future or unreadable state is unavailable; recurring start returns mutation-free `503`. PWA status/preflight blocks launch, and an API refresh error cannot retain a usable stale state. | Start/stop the real worker only after confirming no recurring monitor is active; verify API and PWA with Playwright, then restore initial services/config. No Vinted/proxy. Excludes worker restart and watchdog. |
| 14.5 | Invalid scheduler configuration or producer loss of progress makes the worker self-exit so Compose can restart it. The watchdog stops only recurring monitors, leaves manual monitors intact, rechecks heartbeat after source locks, closes sessions and records the failure. API remains the migration owner; worker/watchdog wait for API health. Unexpected watchdog errors terminate instead of looping silently. | Use a local QA source/session/task fixture and real containers. PostgreSQL inactive state is authoritative if Redis cleanup fails visibly. Restore service state and remove all QA state. No Vinted/proxy. |
| 14.6 | Playwright launches one recurring monitor through the live PWA/API/worker/PostgreSQL/Redis/events path. With interval 60 and jitter 10%, initial `next_run_at` is activation +60..66 seconds; exactly one immediate and two later terminal runs occur without duplicate cadence. | One copied public monitor URL, one healthy configured proxy, no direct fallback, `catalog_per_page=1`, detail candidates `0`, external retries disabled, one explicit baseline recalibration that may prepare one anonymous session internally, and three business runs total across all attempts. Stop after run three and remove the full QA graph/keys/queues; extra live budget requires new authorization. |
| 14.7 | Alembic 0016-to-0017 and zero-to-head, PostgreSQL event/outbox/publication transactions and the real API stream. Commit, rollback, inverted commits, historical backfill, restart, tail, backlog and resume give every committed monitor event exactly one durable publication cursor and duplicate-free logical resume; SSE transport may reconnect or replay. | Prefer an isolated QA database and drop it afterwards; otherwise delete QA events/outbox/publications/source by captured IDs. No Vinted/proxy. Excludes redaction changes and frontend races. |
| 14.8 | Persisted JSONB event details loaded through real REST and SSE. Legitimate safe markers match; caller-forged shapes and raw canaries never appear in response, DB-visible output or logs. | Synthetic secret canaries only; delete QA event/publication/outbox/run/source. No Vinted/proxy. Excludes EventSource ownership. |
| 14.9 | Live PWA, API and SSE connection state. API restart produces one replacement stream; closed-instance callbacks are ignored; navigation closes and return resumes from the last cursor with one rendered event. | Use a backend-produced local operational event, not browser injection. Restore API/PWA and remove QA event state. No Vinted/proxy. Finish with a hunk-by-hunk `ba4b9cc` equivalence decision. |
| 14.10 | Compose services and their startup/migration/health/restart ownership, verified with `config`, `up`, `ps`, controlled kill/restart and logs. The output is one current decision tree in architecture/deployment. | No Vinted/proxy. Capture and restore initial service state. A runtime discrepancy becomes a later fix; this documentation branch does not change services. |
| 14.11 | Monitor commands through the live PWA/API with PostgreSQL confirmation: valid create/edit/archive and invalid requests with no mutation. | No Vinted/proxy and no monitor launch. Remove the QA monitor graph. Session preparation belongs to 14.12; activation, stop and run execution belong to 14.13. |
| 14.12 | Public anonymous session selection, preparation, reuse, refresh, expiry, challenge and invalidation, including secret ownership and fail-stop decisions. | Reuse captured 14.6 evidence and deterministic challenge tests; no new traffic by default. If evidence is insufficient, leave the task open and request another budget. |
| 14.13 | Activation, stop, manual, continuous, duration and window run transitions, deadline authority, terminal states, compensation and failure ownership reconciled with code/spec. | Reuse sanitized IDs/timestamps from 14.6 and exercise negative paths locally. No additional Vinted/proxy traffic and no product-code changes. |
| 14.14 | Redis ready/reserved/processing/ACK/requeue/dead-letter ownership and AOF recovery verified with a real worker/Redis restart and bounded QA payloads. | No Vinted/proxy. Remove QA queue, processing, reverse-marker and dead-letter entries and restore worker/Redis state. |
| 14.15 | Redis/PostgreSQL seen, processing, retry, `finalizing`, policy hash, admission locks and capacity mapped from real state transitions. | Reuse 14.6 hit/skip evidence and local controlled failures; no new external traffic. Delete QA cache/retry/lock residue. |
| 14.16 | PostgreSQL transaction boundaries, event outbox/publication, SSE/PWA delivery, process logs, redaction and retention risk traced end to end. | No Vinted/proxy. Use local sanitized events, remove QA state, and classify absent rotation/retention as accepted risk or a new contained task. |
| 14.17 | Only documents changed by 14.3-14.16 are checked against captured evidence; stale blockers and superseded current-state prose are removed or marked historical where durable. | Documentation-only semantic/link audit. No services or external traffic; unrelated drift becomes separate roadmap work. |
| 14.18 | Integration tests use explicit settings regardless of host `.env`, restore `app_settings` after mutation, and the isolated sticky prepared-session test passes without changing production encryption/session validation. | No Vinted/proxy. Use only test-owned settings/rows and restore or remove them through fixtures; exclude runtime session behavior changes. |

## Next

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 15 | not-started | Notifications | `docs/spec.md` | PWA push, Telegram, webhook, Discord, or email after web monitoring works. |

## Later

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 16 | not-started | Production deployment hardening | `docs/deployment.md` | Traefik and Cloudflare deployment details. |

## Future Authenticated Actions

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 17 | not-started | Authenticated actions | `docs/specs/009-authenticated-actions.md` | Favorites, checkout discovery, pre-purchase, and explicit purchase. |

## Roadmap Rules

- Do not skip ahead unless the user explicitly changes priority.
- If an item changes scope, update its existing spec instead of creating a parallel document.
- If a new item is needed, add it here and create a spec only if no existing document owns it.
- Mark an item `done` only when its acceptance criteria and verification steps are satisfied.
- Keep `docs/spec.md` as the product-level summary and `docs/specs/` as feature-level specs.
- Integrate and close one 14.x task before requesting confirmation to start the next; do not batch their implementation or verification.
