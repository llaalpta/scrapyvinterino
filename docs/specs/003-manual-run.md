# 003 Manual Run

## Goal

Allow one user to open a manual monitor session, execute explicit catalog checks inside it and inspect the complete safe lifecycle.

## Scope

- `POST /api/monitors/{monitor_id}/start` captures the current catalog as an internal `baseline` run before opening the manual session.
- A successful baseline marks the visible IDs as seen, creates no item or opportunity, leaves `next_run_at=null` and opens exactly one `monitor_sessions` row.
- `POST /api/monitors/{monitor_id}/runs` is available only while that manual session is active. Every run is synchronous, single-flight and keeps the same `monitor_session_id` until explicit stop or fail-stop.
- `POST /api/monitors/{monitor_id}/stop` closes an idle manual session. Until roadmap item 14.34.3, a non-terminal run makes stop return `409`; the PWA disables the conflicting controls.
- Run status, counters, errors and the safe event timeline remain visible in the monitor view.
- Manual and scheduled business runs use the same public catalog provider, Redis seen state, filters, persistence, redaction and opportunity contracts.

## Out of Scope

- Automatic cadence for manual mode.
- Graceful stop while a run is already executing; roadmap item 14.34.3 owns that change.
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
- Manual run events retain safe configuration, egress, HTTP/session, Redis, candidate, filter, persistence and terminal decisions without raw secrets.

## Verification

- Run the isolated `manual-session-start-baseline` scenario documented in `docs/development.md`.
- Through the live PWA, baseline A/B/C, execute the same set, add D, repeat D, remove the marker, restart with E and force a provider failure.
- Confirm through API, PostgreSQL and Redis that counters, session ownership, dedupe, fail-stop and cleanup match the acceptance criteria.
- Confirm start/run/stop/configuration controls are honest and disabled during conflicting non-terminal work.

## Audit

- Confirm the manual flow performs no scheduler enqueue and persists no deadline.
- Confirm baseline runs remain outside performance statistics and business sessions.
- Confirm no Vinted, proxy or Telegram traffic occurs in the controlled integration scenario.
- Confirm no authenticated action or hidden fallback is introduced.
