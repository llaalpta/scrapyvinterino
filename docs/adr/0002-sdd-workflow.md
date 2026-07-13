# ADR 0002: Spec Driven Development Workflow

## Status

Accepted

## Context

The project has multiple moving parts: scraping research, backend services, PWA workflows, persistence, Docker, and future authenticated actions. Untracked decisions would make the project hard to maintain.

## Decision

Use practical Spec Driven Development for non-trivial changes, with granular roadmap tasks, branch traceability, integration-first acceptance, and an automatic audit loop.

Implementation must be preceded or accompanied by updates to the relevant spec, research note, ADR, or product decision record. Documentation should be maintained in place rather than duplicated.

Broad plans are decomposed into independently demonstrable tasks and persisted on a product-code-free `plan/<scope>` branch before implementation authorization. Each task then lives on a short-lived branch created from an updated `develop`, has bounded acceptance criteria and a real integration scenario for operational behavior, and is proposed back to `develop` separately. Dependent work starts only after its prerequisite is reviewed and integrated through the agreed repository workflow. After its commit-ready implementation, the agent pauses for explicit confirmation before beginning the next task.

Real tests through the involved containers, entrypoints, PostgreSQL, Redis, events and PWA are the primary acceptance evidence for operational behavior. Unit suites and synthetic events support deterministic edge cases but do not prove service coordination. Documentation-only governance uses semantic consistency and workflow dry runs instead of irrelevant services. Bounded Vinted/proxy traffic is declared in the task contract when external behavior is part of the outcome.

The implementer runs a mandatory self-review followed by an automatic independent read-only rubber-duck audit using the lowest-cost suitable reviewer available. Valid findings enter at most three focused implementation-test-audit loops. New outcomes are moved to later roadmap tasks instead of expanding the current branch.

Required dependency failures are fail-stop and visible by default. Degraded modes, fallbacks, compatibility adapters, alternate providers and new silent retry policies require explicit product value, acceptance criteria and user authorization.

## Consequences

- Features have explicit acceptance criteria before implementation.
- Future sessions can recover intent from the repository, not from chat history.
- Documentation maintenance is part of the definition of done.
- Branch and PR scope make it easier to inspect what changed for each spec.
- Smaller tasks reduce debugging cost, context consumption, and the chance that a passing suite hides broken runtime coordination.
- Multi-layer changes finish with real integration evidence, implementer self-review, and an automatic bounded audit covering only the active task.
- Work pauses between tasks, so priority and scope remain user-controlled.
- Required-service failures surface clearly instead of silently changing execution paths.
- Small fixes can remain lightweight, but they must not contradict existing docs.
