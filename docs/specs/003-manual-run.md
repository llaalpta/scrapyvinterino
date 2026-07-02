# 003 Manual Run

## Goal

Allow the user to manually execute a configured source and record the execution lifecycle.

## Scope

- Trigger a run for one source.
- Create a `runs` record with started/finished timestamps.
- Track status, item counters, opportunity counters, and errors.
- Expose run history through API and PWA.
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
  - execute an active source;
  - show recent run history.
- Database:
  - `runs`;
  - `errors`.

## Acceptance Criteria

- A manual run can be requested for an active source.
- Run status moves to success or failed.
- Errors are persisted and visible.
- A failed run does not crash the worker.
- API/PWA can show recent run state.
- `items_found` counts provider candidates.
- `items_new` and `opportunities_created` stay `0` until later specs.
- Item rows are not inserted or updated by this spec.

## Verification

- Run one source manually.
- Simulate provider failure and confirm persisted error.
- Confirm worker keeps running after failure.
- Confirm items table remains unchanged after a run.
- Confirm PWA can trigger a run and display it.

## Audit

- Confirm the UI does not imply scheduler, deduplication, filters, or item persistence.
- Confirm failed provider calls create a failed run and an error row.
- Confirm no authenticated Vinted action is introduced.
