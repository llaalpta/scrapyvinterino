# 003 Manual Run

## Goal

Allow the user to manually execute a configured source and record the execution lifecycle.

## Scope

- Trigger a run for one source.
- Create a `runs` record with started/finished timestamps.
- Create and close a `monitor_sessions` record for punctual/manual launches.
- Track status, item counters, opportunity counters, and errors.
- Expose run history through API and in the PWA monitor view.
- Execute synchronously from the API for this vertical slice.
- Use the public Vinted catalog provider contract from spec 002.

## Out of Scope

- Scheduler.
- Full deduplication logic beyond recording counters needed by the run.
- Persisting catalog items.
- Authenticated actions.
- Notifications.

## Interfaces

- API:
  - `POST /api/sources/{source_id}/runs`;
  - `GET /api/runs?limit=50`.
- PWA:
  - execute a monitor;
  - show recent run history inside the monitor view.
- Database:
  - `monitor_sessions`;
  - `runs`;
  - `errors`.

## Acceptance Criteria

- A manual run can be requested for an inactive source.
- A manual run creates a session, associates the run with it, and closes the session after success or failure.
- Run status moves to success or failed.
- Errors are persisted and visible.
- A failed run does not crash the worker.
- API/PWA can show recent run state from the monitor view.
- The PWA does not expose a separate Activity navigation item for run history.
- `items_found` counts provider candidates.
- `items_new` and `opportunities_created` stay `0` until later specs.
- Item rows are not inserted or updated by this spec.
- Punctual/manual test runs count as closed monitor sessions, not active recurring sessions.

## Verification

- Run one source manually.
- Confirm the manual run has `monitor_session_id` and the session has `stopped_at`.
- Simulate provider failure and confirm persisted error.
- Confirm worker keeps running after failure.
- Confirm items table remains unchanged after a run.
- Confirm PWA can trigger a run from `Monitores` and display its activity there.

## Audit

- Confirm the UI does not imply scheduler, deduplication, filters, or item persistence.
- Confirm failed provider calls create a failed run and an error row.
- Confirm no authenticated Vinted action is introduced.
