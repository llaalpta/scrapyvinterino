# 008 Scheduler

## Goal

Automatically execute configured sources on safe, configurable intervals after the manual run flow is stable.

## Scope

- Enable or disable scheduler globally.
- Enable or disable each source.
- Configure interval minimum and maximum.
- Add jitter/randomization between runs.
- Support pause windows or allowed execution windows.
- Record scheduler-triggered errors in the same run/error model.

## Out of Scope

- Distributed scheduling across multiple workers.
- Complex priority queues.
- Authenticated actions.

## Interfaces

- Worker:
  - scheduler loop.
- API/PWA:
  - scheduler settings.
- Database:
  - `search_sources.scheduler_config`;
  - `runs`;
  - `errors`.

## Acceptance Criteria

- Scheduler can be disabled completely.
- A source can be paused without deleting it.
- Runs are not triggered outside configured windows.
- Jitter prevents fixed exact polling intervals.
- Scheduler failures are logged without stopping the worker.

## Verification

- Unit tests for next-run calculation.
- Manual check with short interval in local Docker.
- Confirm run records identify scheduler-triggered executions.
