# 003 Manual Run

## Goal

Allow the user to manually execute a configured monitor and record the execution lifecycle.

## Scope

- Trigger a run for one monitor.
- Create a `runs` record with started/finished timestamps.
- Create and close a `monitor_sessions` record for punctual/manual launches.
- Track status, item counters, opportunity counters, and errors.
- Record the same safe run-event log used by scheduled monitor runs so a manual launch can be inspected from start to final opportunity/no-op decision.
- Require an explicit initial snapshot before a manual run can process candidates.
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
  - `POST /api/monitors/{monitor_id}/runs`;
  - `GET /api/runs?limit=50`.
- PWA:
  - execute a monitor;
  - show recent run history inside the monitor view.
- Database:
  - `monitor_sessions`;
  - `runs`;
  - `errors`.

## Acceptance Criteria

- A manual run can be requested for an inactive monitor.
- A manual run creates a session, associates the run with it, and closes the session after success or failure.
- Run status moves to success or failed.
- Errors are persisted and visible.
- A failed run does not crash the worker.
- API/PWA can show recent run state from the monitor view.
- Manual run events include safe configuration, egress, HTTP/session, request-duration, Redis/cache, candidate, filter, persistence, and opportunity/no-op decisions without raw secrets.
- The PWA log console presents those events as non-interactive operational checklist entries; large JSON diagnostics are not rendered in the main timeline.
- A manual run without a current initial snapshot is rejected with a clear message to recalibrate the listing first.
- The PWA does not expose a separate Activity navigation item for run history.
- `items_found` counts provider candidates.
- `items_new` and `opportunities_created` stay `0` until later specs.
- Item rows are not inserted or updated by this spec.
- Punctual/manual test runs count as closed monitor sessions, not active recurring sessions.

## Verification

- Run one monitor manually.
- Confirm the manual run has `monitor_session_id` and the session has `stopped_at`.
- Simulate provider failure and confirm persisted error.
- Confirm worker keeps running after failure.
- Confirm manual run logs are visible in the monitor log console as one line per event and expose only masked/fingerprinted cookie, token, HTTP session, and proxy session markers.
- Confirm a manual run cannot start until `Recalibrar listado inicial` has seeded the current monitor/policy snapshot.
- Confirm items table remains unchanged after a run.
- Confirm PWA can trigger a run from `Monitores` and display its activity there.

## Audit

- Confirm the UI does not imply scheduler, deduplication, filters, or item persistence.
- Confirm failed provider calls create a failed run and an error row.
- Confirm no authenticated Vinted action is introduced.
