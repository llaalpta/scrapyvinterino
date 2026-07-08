# ADR 0002: Spec Driven Development Workflow

## Status

Accepted

## Context

The project has multiple moving parts: scraping research, backend services, PWA workflows, persistence, Docker, and future authenticated actions. Untracked decisions would make the project hard to maintain.

## Decision

Use Spec Driven Development for non-trivial changes, with branch and review traceability.

Implementation must be preceded or accompanied by updates to the relevant spec, research note, ADR, or product decision record. Documentation should be maintained in place rather than duplicated.

Each non-trivial spec or coherent fix should live on a short-lived branch created from `develop`, then be proposed back to `develop` as a PR. The implementer must run a self-review before completion and propose a separate implementation audit when the work touches multiple layers.

## Consequences

- Features have explicit acceptance criteria before implementation.
- Future sessions can recover intent from the repository, not from chat history.
- Documentation maintenance is part of the definition of done.
- Branch and PR scope make it easier to inspect what changed for each spec.
- Multi-layer changes should finish with a self-review and an optional audit proposal covering API, database, worker, cache, frontend, Docker, logs, and verification.
- Small fixes can remain lightweight, but they must not contradict existing docs.
