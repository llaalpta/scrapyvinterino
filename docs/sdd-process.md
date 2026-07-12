# Spec Driven Development Process

This project uses Spec Driven Development to keep product intent, architecture, and implementation aligned.

## Default Flow

Work advances through one independently demonstrable roadmap task at a time:

1. Inspect status/branch, roadmap priority, and the documents that own the requested behavior.
2. Decompose broad work into contained tasks with meaningful outcomes.
3. From a clean `develop`, persist the checklist and task contracts on a product-code-free `plan/<scope>` branch, then review/integrate that branch.
4. Define the active task's scope, exclusions, acceptance/failure criteria, affected state, real integration proof for operational behavior, cleanup, and external traffic envelope.
5. Obtain explicit confirmation before starting the planned implementation task; the planning commit is not implementation authorization.
6. From a clean worktree, create its branch from the updated `develop`; never modify the roadmap on an unrelated feature branch.
7. Update the owning documentation before changing implementation code.
8. Implement only the active vertical slice.
9. Run focused checks during development.
10. Exercise the real operational path through the actual containers, entrypoints, database, queues/cache, events, and UI involved. Use semantic consistency checks and a process dry run for documentation-only governance.
11. Run implementer self-review, then an automatic independent read-only rubber-duck audit.
12. Fix valid findings and repeat the affected verification plus bounded audit. Stop after at most three loops and split newly discovered scope into another task.
13. Run the appropriate regression gate once near closure, update roadmap evidence/status, and commit code/docs together.
14. Before a dependent task, review and merge its prerequisite into `develop`, or obtain explicit user authorization for an equivalent local integration; never branch from a `develop` that lacks required work.
15. Stop and request confirmation before starting the next roadmap task or branch.

Documentation and implementation should move together. A feature is not done if the relevant docs are stale.

## Planning and Task Contract

A plan is a queue of small deliverables, not one large implementation divided by technical layer. Avoid tasks called only "backend", "frontend", "tests", or "cleanup"; those usually create incomplete cross-task behavior.

Each planned task records:

- operational or user outcome;
- owning docs/spec;
- dependencies and explicit out-of-scope follow-ups;
- affected services and PostgreSQL/Redis/browser state;
- acceptance criteria and honest stop/error behavior;
- real integration scenario, evidence to capture, and cleanup;
- bounded Vinted/proxy request allowance when external behavior is essential;
- suggested branch name.

Plan mode persists its checklist on a dedicated `plan/<scope>` branch from clean `develop`, without product code or contamination of another feature branch. That planning branch is reviewed/integrated first. The first implementation starts only after a separate confirmation and on its own task branch. After its audit and commit, the agent pauses even when the next checklist item is obvious. Scope discovered during implementation is added to the roadmap instead of silently expanding the branch.

## Branch and PR Discipline

Use one short-lived branch per spec or coherent fix. The default integration target is `develop`, and completed work should be proposed as a PR back to `develop`.

Branch from `develop` before editing a new spec or feature. If `develop` does not exist locally, stop and ask before continuing, or create it intentionally as an explicit repository workflow change. Do not silently invent a new integration branch during unrelated product work.

Name branches by scope, for example:

- `spec/010-session-prepare`
- `feature/010-proxy-session-pool`
- `fix/010-rate-limit-refresh`

Do not keep stacking unrelated specs on a long-lived feature branch. If the current branch scope does not match the requested work, switch or create the correct branch before editing files.

## Post-Implementation Self-Review

Every non-trivial implementation needs an implementer-owned second pass before it is considered done.

Do not delegate this review. If the user explicitly asks for an audit later, follow that request separately. The implementing agent owns the final decision: fix valid findings, reject false positives with a reason, or move deferred work into the owning spec or roadmap.

The self-review must answer:

- Does the implementation satisfy the active spec and acceptance criteria?
- Can the user exercise the promised flow end to end?
- Are API, UI, database, worker, and docs consistent with each other?
- Are there visible buttons, links, labels, or states that imply behavior that does not exist?
- Are invalid inputs and unavailable actions handled clearly?
- Were the right checks run, and is the evidence recorded?

For frontend work, a screen is not done just because it compiles. Navigation must land somewhere real, enabled buttons must perform their visible action, and future actions must be disabled, absent, or shown as an honest empty state.

Frontend structure is part of the acceptance bar. Before adding a non-trivial PWA flow, keep the React root thin, place feature screens under `frontend/src/features/`, shared UI under `frontend/src/components/`, composition under `frontend/src/app/`, cross-feature state hooks under `frontend/src/hooks/`, and styles under `frontend/src/styles/`. Do not continue growing a multi-view `App.tsx` monolith when the change adds new state, controls, or views.

## Automatic Independent Audit

The implementer self-review is mandatory and is followed automatically by a separate read-only audit before task closure.

Use the lowest-cost/lower-reasoning independent reviewer exposed by the platform for a tightly scoped rubber-duck pass. If the platform cannot select a model, use the simplest independent reviewer available and constrain it to the active task. The reviewer does not edit, commit, call external services, or reopen unrelated architecture.

The audit checks only the relevant parts of:

- spec/roadmap alignment and stale documentation;
- API/UI contracts and negative/fail-stop paths;
- database transactions/migrations and Redis/queue/cache state;
- worker, scheduler, service startup/health and concurrency;
- events, logs, redaction and observability;
- real integration evidence, cleanup, and remaining gaps;
- obsolete adapters, fallback paths, test-only residue, and legacy descriptions.

The implementer reproduces each actionable finding. Valid findings enter another focused implementation-real-test-audit loop; false positives are rejected with evidence. One clean pass is sufficient and rechecks cover only changed findings, with three total passes as the maximum. A finding that requires a new outcome becomes a new roadmap task and waits for user confirmation.

If the platform exposes no independent reviewer, the task remains open and the limitation is reported. A second implementer pass is still self-review, not an independent audit.

## Verification Hierarchy

Verification follows this order:

1. Real integration acceptance through the actual service entrypoint and containers.
2. Persistent/runtime evidence in PostgreSQL, Redis queues/cache, events/logs, and browser state.
3. Focused automated tests for the changed contracts and deterministic failure paths.
4. One broader regression suite near task closure when risk warrants it.

A large passing suite cannot replace the first two levels. Mocks and synthetic events are supporting tools for rare races, malformed data, redaction, or precise edge cases; they are not sufficient proof of coordination between API, worker, scheduler, consumers, Docker, Redis, PostgreSQL, SSE, and PWA.

Real Vinted/proxy tests are bounded in the task contract. Record the expected request/run count, keep the test monitor controlled, avoid secret output, and stop the monitor plus clean QA state after verification. If external traffic is not necessary to prove the task, use the real local stack without external calls.

Required dependencies fail visibly and stop the affected operation or service by default. Do not invent a degraded mode, fallback, compatibility mode, new silent retry policy, or alternate provider merely to make a test pass. Such behavior needs explicit product value, acceptance criteria, and user authorization.

Documentation-only process or governance tasks use proportional acceptance evidence: clean diffs, valid references, cross-document consistency, and an executable dry run of the documented gates. They do not start unrelated application containers merely to manufacture integration evidence.

## Browser QA Rule

When a spec changes the PWA, run a browser-driven QA pass against the live development app.

Use `.\scripts\qa-pwa.ps1 start` as the default PWA QA entrypoint. It keeps backend services in Docker and runs an isolated local Vite server on `127.0.0.1:5176` with a localhost API proxy, avoiding conflicts with the Docker frontend service and stale browser sessions.

Before restarting or recreating containers for frontend QA:

- run `.\scripts\qa-pwa.ps1 stop` if isolated QA may be active;
- inspect `docker compose ps` to see which frontend/API services are already running;
- check the target port before starting another Vite server when a conflict is suspected;
- choose either the Docker frontend on `5173` or isolated QA on `5176`, not both for the same pass;
- rebuild only the affected service unless the change explicitly crosses service boundaries.

The QA pass should use Playwright MCP when available and cover the behavior claimed by the spec:

- navigate through the routes or anchors that should work;
- inspect which controls are enabled or disabled;
- submit working forms through the UI;
- submit at least one relevant invalid input;
- verify that successful UI actions are visible after the action;
- verify persistence through API and database when the feature saves data.

If the live app differs from the source code or build output, restart the affected dev service and repeat the QA. Do not rely on `pnpm build` alone for UI acceptance.

## Learning Rule

After closing a feature, capture generalized lessons in the existing process docs or agent instructions when a preventable issue was found.

Write these lessons as durable rules, not as session notes. Prefer guidance such as "after implementing a frontend flow, verify the running app with Playwright and persisted data with the database" over a detailed account of a specific bug.

Do not create new documents for these lessons unless no existing document owns the topic.

## Documentation Maintenance Rule

Before creating a new document, check whether an existing document already owns the topic.

Update existing docs when possible:

- Product requirements: `docs/spec.md`
- Roadmap and order of work: `docs/roadmap.md`
- Feature specs: `docs/specs/`
- System design: `docs/architecture.md`
- Data model: `docs/data-model.md`
- Local development: `docs/development.md`
- Deployment: `docs/deployment.md`
- Security: `docs/security.md`
- Risks: `docs/risks.md`
- Vinted research: `docs/research/`
- Durable technical decisions: `docs/adr/`
- Product direction decisions: `docs/product-decisions.md`

Create a new document only when the responsibility is clear and durable. Do not create parallel documents that restate or override existing ones.

## Spec Template

Use this shape when adding a feature spec or expanding an existing one:

```md
## Feature Name

### Goal

What user or system outcome this change enables.

### Scope

What is included.

### Out of Scope

What is intentionally excluded.

### Interfaces

API endpoints, worker jobs, UI flows, settings, or database entities affected.

### Acceptance Criteria

- Observable behavior that must pass.
- Error cases that must be handled.
- Data that must be persisted or left untouched.

### Verification

Commands, tests, or manual checks required.

### Self-Review

Post-implementation self-review focus areas for this spec.
```

## ADR Rules

Use ADRs for architecture decisions that should not be rediscovered every session.

ADRs should be short and use:

- Status
- Context
- Decision
- Consequences

If a decision changes, create a new ADR or update the current one with a clear supersession note. Do not silently rewrite history.

## Research Rules

Research docs are for facts learned from investigation.

- Store sanitized findings.
- Do not include cookies, tokens, personal data, addresses, payment information, or raw authenticated payloads.
- Record dates for Vinted observations because implementation details may change.
- Separate observed facts from assumptions.

## Completion Checklist

Before considering work done:

- Branch scope was checked and matches the spec or coherent fix.
- The roadmap task is small enough to ship and demonstrate independently.
- Relevant docs are current.
- No duplicate or contradictory docs were introduced.
- Code matches the documented architecture.
- UI/API/database behavior matches what the user can actually do.
- Real integration evidence covers the actual services and state stores promised by the task.
- Non-trivial frontend changes follow the documented module boundaries instead of adding more mixed responsibility to the app root.
- For PWA changes, Playwright or equivalent browser QA covered the live app.
- Post-implementation self-review completed.
- Self-review findings fixed, rejected with reason, or deferred into the owning spec/roadmap.
- The independent read-only audit completed automatically and its findings were resolved or moved to a later task.
- Frontend/container ownership was checked before restarting or recreating Vite/Docker services.
- Process docs were updated with generalized prevention rules if the work revealed a repeatable quality gap.
- Checks were run or skipped with a clear reason.
- QA rows, Redis keys, queue entries, sessions, and temporary processes were cleaned.
- Git status is clean after commit.
- Work stopped pending explicit confirmation before the next task.
