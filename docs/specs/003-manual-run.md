# 003 Manual Run

## Goal

Allow one user to open a manual monitor session, execute explicit catalog checks inside it and inspect the complete safe lifecycle.

## Scope

- `POST /api/monitors/{monitor_id}/start` captures the current catalog as an internal `baseline` run before opening the manual session.
- A successful baseline marks the visible IDs as seen, creates no item or opportunity, leaves `next_run_at=null` and opens exactly one `monitor_sessions` row.
- `POST /api/monitors/{monitor_id}/runs` is available only while that manual session is active. Every run is synchronous, single-flight and keeps the same `monitor_session_id` until explicit stop or fail-stop.
- `POST /api/monitors/{monitor_id}/stop` makes the source inactive and blocks new work while it remains stopped. It closes an idle session in the request; if session-owned runs are still `running/finalizing`, the session remains open until all reach terminal and the last closes it with reason `stopped`. Stronger fail-stop paths such as `baseline_required` or `redis_unavailable` retain their diagnostic session reason.
- Run status, counters, errors and the safe event timeline remain visible in the monitor view.
- Manual and scheduled business runs use the same public catalog provider, Redis seen state, filters, persistence, redaction and opportunity contracts.

## Out of Scope

- Automatic cadence for manual mode.
- Hard cancellation of an in-flight provider request or rollback of a run already admitted.
- Durable stopping state, distributed drain coordination or exactly-once crash recovery.
- Authenticated Vinted actions, notifications and purchase behavior.

## Interfaces

- API:
  - `POST /api/monitors/{monitor_id}/start`;
  - `POST /api/monitors/{monitor_id}/runs`;
  - `POST /api/monitors/{monitor_id}/stop`;
  - `GET /api/runs?limit=50`.
- PWA:
  - `Iniciar sesion` while stopped;
  - `Ejecutar ahora` and `Detener sesion` while active;
  - `Deteniendo...` with configuration, archive and restart unavailable while an admitted run finishes;
  - no standalone recalibration action in manual mode.
- Database:
  - one open `monitor_sessions` row for the active manual session;
  - one sessionless `baseline` run at each start;
  - later business runs associated with that session;
  - persisted errors and redacted run events.

## Acceptance Criteria

- Starting a stopped manual monitor creates exactly one successful zero-opportunity baseline and then one active session without a deadline.
- Baseline operational failure returns a visible failed run and leaves the source inactive with no open session or deadline.
- `Ejecutar ahora` is rejected while stopped and reuses the open session while active.
- Repeating the same catalog state creates no duplicate opportunity; one later unseen passing item creates exactly one.
- Losing the baseline marker during an active session creates a visible failed run, closes the session with `baseline_required`, stops the source and tells the user to start a new session. It never recalibrates silently.
- Restarting captures another baseline, so listings that appeared while stopped are not reported as opportunities.
- Stopping during admitted session work returns promptly, admits no later work while the source remains inactive and preserves every run result; the last normal terminal closes the monitor session with reason `stopped`.
- Manual run events retain safe configuration, egress, HTTP/session, Redis, candidate, filter, persistence and terminal decisions without raw secrets.

## Verification

- Run the isolated `manual-session-start-baseline` and `session-stop-drain` scenarios documented in `docs/development.md`.
- Through the live PWA, baseline A/B/C, execute the same set, add D, repeat D, remove the marker, restart with E and force a provider failure.
- Confirm through API, PostgreSQL and Redis that counters, session ownership, dedupe, fail-stop and cleanup match the acceptance criteria.
- Confirm start/run/stop/configuration controls are honest: stop remains available for an admitted run, then `Deteniendo...` blocks edit/archive/restart until terminal.

### Bounded real acceptance (14.37)

The final manual-session gate uses one temporary manual monitor through the already-running PWA/API and exactly one `start`, one later `run` and one `stop`. Worker and scheduler-watchdog remain stopped. The browser blocks every non-loopback request, so only the backend provider may reach the configured egress diagnostic, Vinted and DataDome collector endpoints through the single eligible proxy.

Before traffic, the gate requires no active monitor, non-terminal run or open monitor session, one active proxy, an empty operational Redis database and the expected serial runtime (`catalog_per_page=5`, detail limit `5`, one catalog retry, prepared sessions required and direct catalog disabled). Its allowance is at most `19` logical external operations: up to two six-operation session preparations, two catalog attempts and five item-detail requests. Redirect hops are not logical operations and remain a declared residual because egress/DataDome clients use their library redirect policy.

Acceptance has three criteria:

1. `start` persists one successful sessionless baseline with zero opportunities before it opens one active manual session with `next_run_at=null`; the Redis marker and visible seen IDs agree with the baseline.
2. One immediate PWA `run` reuses that monitor session and performs one real later catalog observation without recreating an opportunity for a baseline-seen ID; its SQL counters, events and visible terminal state agree.
3. PWA `stop` leaves the source inactive and the session closed with `stopped`; a subsequent authenticated local `POST /runs` returns `409` without a new run or Redis mutation, and cleanup removes only the QA user/session, source graph, orphan QA items and source-scoped Redis keys.

This gate passed on 2026-07-15 against the live local stack. Start prepared one anonymous context and persisted a `5/0/0` baseline; the immediate manual run reused both the open monitor session and prepared Vinted session and also finished `5/0/0`. Stop closed the only monitor session with `stopped`, the local post-stop request returned `409`, and recorded request-start phases show six logical external operations. Cleanup removed two runs, 47 events, one prepared session, all authentication/source rows and seven source-scoped Redis keys; Redis returned to zero keys, no monitor/run/session remained active, the pre-existing item and proxy remained, and worker/watchdog were never started.

Verification for the stop slice passed `6` focused cases and one live Playwright/API/PostgreSQL/Redis/scheduler/queue/consumer case, followed by the full backend and PWA gates. The controlled provider existed only at the Vinted boundary; worker/watchdog stayed stopped and no external traffic or QA residue remained.

## Audit

- Confirm the manual flow performs no scheduler enqueue and persists no deadline.
- Confirm baseline runs remain outside performance statistics and business sessions.
- Confirm no Vinted, proxy or Telegram traffic occurs in the controlled integration scenario.
- Confirm no authenticated action or hidden fallback is introduced.
