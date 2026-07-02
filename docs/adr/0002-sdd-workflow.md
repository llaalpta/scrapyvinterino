# ADR 0002: Spec Driven Development Workflow

## Status

Accepted

## Context

The project has multiple moving parts: scraping research, backend services, PWA workflows, persistence, Docker, and future authenticated actions. Untracked decisions would make the project hard to maintain.

## Decision

Use Spec Driven Development for non-trivial changes.

Implementation must be preceded or accompanied by updates to the relevant spec, research note, ADR, or product decision record. Documentation should be maintained in place rather than duplicated.

## Consequences

- Features have explicit acceptance criteria before implementation.
- Future sessions can recover intent from the repository, not from chat history.
- Documentation maintenance is part of the definition of done.
- Small fixes can remain lightweight, but they must not contradict existing docs.
