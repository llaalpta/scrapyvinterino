# Roadmap

This roadmap is a short priority queue, not an exhaustive risk register. Work on the first incomplete `Now` item unless the user explicitly changes priority. Acceptance details belong in the owning spec when the task starts.

## Product and operating target

- Personal, private, single-user Vinted catalog monitor running locally with Docker Compose.
- Manual maintenance, service restart and session relaunch from the PWA are acceptable.
- Failures must be visible and must not create hidden fallbacks or retry loops.
- Existing queue recovery remains best-effort; exactly-once crash recovery is not a product requirement.
- The current block ends when manual and recurring sessions prove that only catalog entries observed after session-start calibration become opportunities. Notifications and production 24/7 hardening remain separate.

## Now

Keep no more than five independently valuable tasks here.

| Priority | Status | Item | Owner | Suggested branch | Outcome |
| --- | --- | --- | --- | --- | --- |
| 1 | planned | 14.38 Real recurring-session acceptance | `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/008-scheduler.md` | `qa/live-recurring-session-acceptance` | A bounded real 60-second session proves no immediate business run, the initial `60..66` deadline, three scheduler/queue/consumer executions, post-baseline opportunity semantics and complete restoration. |

## Next

Do not promote another feature until 14.37-14.38 close the manual monitoring MVP acceptance. Telegram remains the first future candidate, subject to a new explicit product decision after that gate.

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

Detailed historical verification remains in the owning specs, `docs/010-producer-consumer-implementation.md`, ADRs and Git history.

## Roadmap rules

- `Now` contains at most five outcomes and only real dependencies determine their order.
- A finding enters `Now` only when it affects normal use, protects secrets/data, or is required by the selected product target.
- Adjacent theoretical hardening becomes an accepted risk or conditional item, not automatic implementation scope.
- One task, branch, review and merge completes before a genuinely dependent task starts.
- After each completed task, stop for user confirmation before beginning the next one.
- Never delete branches or rewrite published history while reorganizing this roadmap.
