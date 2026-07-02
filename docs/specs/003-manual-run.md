# 003 Manual Run

## Goal

Allow the user to manually execute a configured source and record the execution lifecycle.

## Scope

- Trigger a run for one source.
- Create a `runs` record with started/finished timestamps.
- Track status, item counters, opportunity counters, and errors.
- Expose run history through API and PWA.
- Support a fake or fixture provider until Vinted research is complete.

## Out of Scope

- Scheduler.
- Full deduplication logic beyond recording counters needed by the run.
- Authenticated actions.
- Notifications.

## Interfaces

- API:
  - trigger source run;
  - list recent runs.
- Worker:
  - execute one source on demand.
- Database:
  - `runs`;
  - `errors`.

## Acceptance Criteria

- A manual run can be requested for an active source.
- Run status moves to success or failed.
- Errors are persisted and visible.
- A failed run does not crash the worker.
- API/PWA can show recent run state.

## Verification

- Run one source manually.
- Simulate provider failure and confirm persisted error.
- Confirm worker keeps running after failure.
