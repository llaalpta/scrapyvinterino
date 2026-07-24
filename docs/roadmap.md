# Roadmap

This roadmap is a short priority queue, not an exhaustive risk register. Work on the first incomplete `Now` item unless the user explicitly changes priority. Acceptance details belong in the owning spec when the task starts.

## Product and operating target

- Personal, private, single-user Vinted catalog monitor running locally with Docker Compose.
- Manual maintenance, service restart and session relaunch from the PWA are acceptable.
- Failures must be visible and must not create hidden fallbacks or retry loops.
- Existing queue recovery remains best-effort; exactly-once crash recovery is not a product requirement.
- The current block ends when normal catalog traffic is proxy-only, sticky lifetime matches the configured provider, bounded challenge recovery is honest and proxy consumption is visible at monitor/session scope. Notifications and production 24/7 hardening remain separate.

## Now

Program `14.54` is implemented one standard task at a time. `14.54.3` is complete; `14.54.4` is the next planned task and requires separate user confirmation.

| Item | Status | Outcome | Dependency |
| --- | --- | --- | --- |
| 14.54.1 | done | Move sticky username format and maximum lifetime to each proxy profile; DataImpulse uses `sessid` with a 25-minute local limit, while the monitor session remains independent. | None |
| 14.54.2 | done | Before candidates are accepted, give the selected profile at most its current/initial attempt plus one fresh sticky, verify forced egress rotation and cool it once if both attempts fail. | 14.54.1 merged |
| 14.54.3 | done | Reassign an exhausted run to the next eligible proxy profile through one capacity-checked PostgreSQL handoff that makes the durable run binding authoritative over its stale queue payload. | 14.54.2 merged |
| 14.54.4 | planned | Let an explicit PWA retry target one cooling profile for one fresh-sticky attempt without clearing cooldown in advance. | 14.54.3 merged |

## Next

Telegram opportunity alerts (`15.1`) follow `14.54` and manual acceptance of the proxy/session behavior, subject to a new explicit product decision and bounded plan. Production hardening remains deferred for the current personal operating model.

## Conditional hardening

Do not schedule these merely because the risk exists. Promote one only when its trigger is observed or the operating target changes.

| Item | Trigger |
| --- | --- |
| 14.12.4 Prepared-session rotation durability | Repeated runs prove that lost cookie/context rotation causes avoidable session failures. |
| 14.12.6 APP secret-key sentinel | Key rotation, long-lived encrypted data or server deployment is planned. |
| 14.12.7 Encrypted-row corruption handling | A corrupt row is reproduced outside a deliberately malformed test. |
| 14.12.8 Proxy credential read model | Before exposing the app beyond loopback/private access. |
| 14.12.11 Anonymous-session retention | Encrypted session rows show meaningful unbounded growth. |
| 14.24 Manual diagnostic secret safety | Before either affected diagnostic script is used again; until then it remains non-operational. |
| 14.30 Archive/runtime race | A reserved/running archive race is reproduced in normal use; the interim operator rule is stop, wait for terminal state, then archive. |

## Production and future work

| Status | Item | Trigger |
| --- | --- | --- |
| deferred | 14.20 API graceful shutdown | Production or unattended operation. |
| deferred | 14.22 Watchdog observability | Production or a real invisible watchdog hang. |
| deferred | 14.23 Core service restart ownership | Production deployment design. |
| deferred | 14.32 Local login abuse controls | Network exposure beyond the trusted local boundary. |
| deferred | 14.33 PWA browser security headers | Same-origin HTTPS deployment. |
| deferred | 16 Production deployment hardening | Decision to deploy behind Traefik/Cloudflare. |
| future | 15.1 Telegram opportunity alert | Manual acceptance of session start, recurring cadence, filters and opportunity creation. |
| future | 17 Authenticated Vinted actions | Public monitoring is stable and the user explicitly reprioritizes account actions. |

## Retired or absorbed work

- 14.12.9 and 14.12.10: no durable exactly-once ledgers for session reuse or preparation. A rare duplicate around abrupt process death is accepted and must remain visible.
- 14.12.12: remove obsolete tombstones when their routes are next touched; it does not block product work.
- 14.13-14.16: do not create four additional mapping branches. Current lifecycle, queue, cache, persistence, event and log ownership already lives in maintained architecture/spec/data-model docs.
- 14.17: current-state pruning is continuous documentation hygiene, not a terminal project phase.
- 14.14, 14.21 and 14.31: do not build or verify sophisticated queue/AOF/drain/split-brain recovery for the personal local operating model. Existing recovery is best-effort only.
- 14.25 and 14.29: their user-relevant validation is absorbed by 14.26; adversarial cross-process serialization is not a standalone priority.

## Completed milestones

| Scope | Status | Evidence summary |
| --- | --- | --- |
| 1-13.3 | done | Sources, public provider research, runs, persistence, filters, opportunities, prepared public sessions, producer/consumer execution and detail enrichment work end to end. |
| 14-14.1 | done | Redis seen cache and notification-ready opportunity contract. |
| 14.2 | done | Integration-first SDD governance; simplified by the current lightweight revision. |
| 14.3-14.6 | done | Atomic scheduler admission, producer heartbeat/watchdog and real recurring cadence. |
| 14.7-14.9 | done | Transactional SSE outbox, persisted redaction parity and single-current-EventSource reconnect behavior. |
| 14.10-14.12 | done | Current service, monitor-command and public-session maps. |
| 14.12.1 | done | Private local PWA/API access control. |
| 14.12.2 | done | Proxy/session identity generation and pre-provider fencing, merged through PR #8 at `e9eed13`; Alembic `0019` deliberately removes incompatible prepared sessions. |
| 14.12.3 | done | Catalog response fail-stop merged through PR #13: the first classified challenge/rejection/`429` terminates and ACKs without failure-triggered refresh, retry or requeue. |
| 14.12.5 | done | Runtime/API/PWA canonical prepared-session eligibility, monitor-scoped safe reasons and one-shot expiry refresh passed the isolated live API/Playwright gate (10 tests) plus the isolated backend suite (510 passed, 2 opt-in skipped), with no external traffic or operational-state drift. |
| 14.18 | done | The fixed scheduler/consumer identity canary passed twice in fresh PostgreSQL databases and Redis 15; occupied-Redis rejection and failed-test cleanup preserved operational PostgreSQL/Redis fingerprints, with no worker, provider or proxy traffic. |
| 14.19 | done | Redis loss now makes the worker exit non-zero for Docker restart and lets the existing heartbeat/API/PWA contract converge unavailable. A disposable internal-network Redis/worker/API/Vite/Playwright gate passed 17 focused tests plus one live outage/recovery flow with unchanged operational fingerprints and no external traffic. |
| 14.26-14.28 | done | Monitor identity editing, serialized command state and independently loaded PWA bootstrap surfaces passed their live PWA/API/PostgreSQL gates and were merged through PRs #21-#23. |
| 14.34.1 | done | Manual session start calibrates without opportunities, opens one active session and leaves later business runs to `Ejecutar ahora` until explicit stop; its real PWA/API/PostgreSQL/Redis gate and full checks passed without external traffic or residue. |
| 14.34.2-14.34.3 | done | Recurring start now persists only its later deadline after baseline, while stop drains admitted work and fences reserved tasks; both live scheduler/queue/consumer/PWA gates passed without external traffic or residue. |
| 14.35 | done | The operational database moved from `0018` to head `0019` by recreating only the API: exactly nine incompatible prepared sessions were removed, every non-session SQL/Redis fingerprint stayed identical, all six authenticated DB-backed surfaces returned `200`, and worker/watchdog remained stopped with no Vinted/proxy traffic or QA residue. |
| 14.36 | done | Visible PWA collections now distinguish loading, confirmed empty and unavailable state, retain confirmed snapshots after refresh failures and lock only dependent mutations. The isolated API/Vite/auth/PostgreSQL Playwright gate, Ruff, frontend lint and production build passed with unchanged operational PostgreSQL/Redis fingerprints, worker/watchdog stopped and no external traffic or QA residue. |
| 14.37 | done | A bounded live PWA/API/proxy/Vinted run proved baseline-before-activation and prepared-session reuse with `5/0/0 -> 5/0/0`, honest stop and local post-stop `409`. Six logical external operations stayed below the allowance of 19; exact SQL/Redis cleanup left no QA state or active work and kept worker/watchdog stopped. |
| 14.38 | done | One bounded live PWA start produced a sessionless `5/0/0` baseline, three real scheduler/Redis/consumer successes with prepared-session use count `1 -> 4`, eight post-baseline opportunities, exact three-way enqueue/receive/ACK evidence and no fourth run after PWA stop. The 22 logical external operations stayed below 45; exact cleanup restored every stable SQL/Redis fingerprint and initial service owner while preserving ordinary proxy telemetry. |
| 14.39 | done | The live PWA now distinguishes URL-applied filters, effective application-controlled order/page, no-effect parameters and blockers. Four focused parser tests, frontend lint/build and desktop/mobile Playwright against the real API/PostgreSQL path passed with zero external requests and zero QA residue. |
| 14.40 | done | Monitor detail now presents accumulated results first, explicitly including active work, followed by directly comparable active/latest-session results; the chart remains independently scoped to accumulated history. Focused stats tests, frontend lint/build and desktop/mobile Playwright against the real API/PostgreSQL path passed with zero external requests and zero QA residue. |
| 14.41 | done | Accumulated monitor logs now start collapsed, retain the complete timeline and controls on expansion, and reset closed when selecting another monitor. Frontend lint/build and live desktop/mobile Playwright passed with keyboard operation, persisted-event consistency, zero browser external requests and zero QA residue. |
| 14.42 | done | The development API now guarantees one configured local user after migrations without weakening production auth. Fresh-database migration/provisioning passed twice with stable ID/count, the real Compose restart reported the existing user without exposing its password, live PWA login survived reload with zero external requests, and the isolated backend gate passed `528` normal plus `3` loopback-only tests with operational PostgreSQL/Redis unchanged and no QA session residue. |
| 14.43 | done | `Iniciar sesion` is now the only normal PWA entrypoint; standalone preparation and detail-probe UI/state/API clients/CSS were removed while authenticated backend diagnostics remain direct-only. Frontend lint/build and live PWA login/monitor selection proved both controls absent plus the unsaved-draft start guard, with one login POST, zero external requests and zero QA residue. |
| 14.44 | done | Detail candidates now receive at most one two-second retry inside their observing run and close as terminal seen or discarded work; the durable Redis retry payload/index and its settings were removed. The real recurring PWA/API/PostgreSQL/Redis/scheduler/consumer gate proved fail-once/succeed-once, current log narration and later dedupe with no retry/processing residue, and the isolated backend gate passed 521 normal plus 3 loopback-only cases with unchanged operational fingerprints. |
| 14.45 | done | `Encontrados` now counts only monitor-new candidates after Redis/durable-opportunity deduplication, including claimed batches that later fail-stop; baseline and repeats count zero, while `Oportunidades` counts only newly committed passing results. Migration 0020 remapped historical counters and removed `items_new` from rows and event details; the live manual PWA/API/PostgreSQL/Redis path, historical migration probe, Ruff, frontend lint/build and isolated backend gate (`523` normal plus `3` loopback-only) passed with no external traffic or QA residue. |
| 14.46 | done | The redundant scheduler UI gate and persisted `app_settings.scheduler.enabled` were removed. `.env`, live worker heartbeat and capacity now determine availability, while per-monitor start/stop owns recurrence. Migration 0021, the nine-case live PWA/scheduler/queue gate and the complete isolated backend suite (`525` normal plus `3` loopback-only) passed without external traffic or QA residue. |
| 14.47 | done | The selected-monitor detail now separates a compact action-first read mode from guarded stopped/idle editing, preserves accumulated/session performance and collapses proxy-bound HTTP contexts plus logs as diagnosis. The isolated authenticated API/PostgreSQL/Vite/Playwright gate passed `8` focused plus `1` live case on desktop/mobile with no external traffic, QA residue or operational PostgreSQL/Redis drift; Ruff and frontend lint/build passed. |
| 14.48 | done | Run cards now expose total and phase timings plus curl-observed proxy bytes, category totals and explicit partial/direct/historical states. Eighteen initial focused checks plus eleven finding-specific rechecks, Ruff, frontend lint/build and local PostgreSQL/API/PWA Playwright passed; one bounded real PWA start crossed the proxy/Vinted path and DataImpulse independently reported `3` requests / `830343` bytes for its billing window, without runtime vendor integration. Exact cleanup left no QA rows or queued work. |
| 14.49 | done | Normal API/PWA/scheduler/queue catalog work is proxy-only; migration 0022 removed seeded legacy keys while preserving current state, and hostile proxy-less payloads are quarantined once without logging raw identity/content. The live API/scheduler/Redis/consumer/PWA gate passed `14/14`, the affected manual-run regression `71/71` and hostile queue/consumer checks `16/16`; Ruff/frontend checks passed with unchanged operational fingerprints and zero external traffic. The single full-suite pass exposed only the then-invalid test proxy FK (`504 passed, 9 skipped, 37 failed`); all 37 belonged to the subsequently green 71-case file. |
| 14.50 | done | Proxy settings now describe local configuration without a misleading IP test, while monitor start owns the real egress check and visible cooldown/retry contract. Migration 0023, 46 focused cases and one live authenticated PWA/API/PostgreSQL flow passed after a positive audit with no external traffic or operational-state drift. |
| 14.51 | done | Monitor stats now aggregate typed proxy-traffic estimates across all runs and the active/latest session, with exact calibration linkage and honest missing/partial states; the compact PWA replaces the five-run panel. Five aggregation cases, five activation/linkage cases and one live API/PostgreSQL/PWA flow passed, followed by a positive independent audit, with no external traffic or operational-state drift. |
| 14.52 | done | Monitor detail now orders compact filters and HTTP readiness before a single accumulated/session performance table, uses a 170 px chart only for real business activity and keeps logs collapsed. Five aggregation plus five linkage cases and one live desktop/mobile API/PostgreSQL/PWA flow passed; the audit fix proved partial traffic and URL blockers remain visible at 390 px, with no external traffic or operational-state drift. |
| 14.53 | done | Prepared-context uses and expiry are now distinct from observed proxy requests; performance separates bytes and request counts and uses smaller chart labels. Five traffic cases, eight activation/rotation cases and one live PWA flow passed after a positive independent audit, with zero external traffic and no operational-state drift. |
| 14.54.1 | done | Migration 0024 moved the strict sticky username template and `1..120` minute TTL to each proxy profile, included both in the identity fence and made prepared-context expiry use the earlier global/profile deadline. The isolated authenticated PWA/API/PostgreSQL/Redis scenario passed `25/25`, including runtime username construction, context invalidation/replacement in the same monitor session and rejection without mutation; the full isolated backend gate passed `564` normal plus `3` loopback-only tests, with 11 opt-in skips, zero external traffic and unchanged operational fingerprints. The independent read-only audit returned positive with no A/B/C findings. |
| 14.54.2 | done | The selected profile now receives at most one replacement sticky for recoverable pre-candidate failure. Its forced diagnostic traverses the proxy and blocks a repeated known IP before Vinted; exhaustion applies one cooldown and one terminal without profile fallback, while `429` and post-candidate work remain fail-stop. The isolated API/Vite/Playwright/proxy-loopback gate passed `14/14`; the complete backend gate passed `566` with 12 opt-in skips plus `5` loopback catalog cases. Operational PostgreSQL/Redis fingerprints were unchanged, no external traffic occurred and the independent audit returned positive with no A/B/C findings. |

Detailed historical verification remains in the owning specs, `docs/010-producer-consumer-implementation.md`, ADRs and Git history.

## Roadmap rules

- `Now` contains at most five outcomes and only real dependencies determine their order.
- A finding enters `Now` only when it affects normal use, protects secrets/data, or is required by the selected product target.
- Adjacent theoretical hardening becomes an accepted risk or conditional item, not automatic implementation scope.
- One task, branch, review and merge completes before a genuinely dependent task starts.
- After each completed task, stop for user confirmation before beginning the next one.
- Never delete branches or rewrite published history while reorganizing this roadmap.
