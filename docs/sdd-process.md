# Spec Driven Development Process

This project uses Spec Driven Development to keep product intent, architecture, and implementation aligned.

## Default Flow

1. Understand the requested change.
2. Check `docs/roadmap.md` to identify the current priority.
3. Read the existing documentation that owns the affected area.
4. Update the existing document if the change affects behavior, architecture, risk, security, deployment, or data.
5. Define acceptance criteria.
6. Implement the smallest useful vertical slice.
7. Verify with focused checks.
8. Run an explicit implementer self-review.
9. Fix or explicitly defer self-review findings.
10. Commit the code and documentation together.

Documentation and implementation should move together. A feature is not done if the relevant docs are stale.

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

## Browser QA Rule

When a spec changes the PWA, run a browser-driven QA pass against the live development app.

Use `.\scripts\qa-pwa.ps1 start` as the default PWA QA entrypoint. It keeps backend services in Docker and runs an isolated local Vite server on `127.0.0.1:5176` with a localhost API proxy, avoiding conflicts with the Docker frontend service and stale browser sessions.

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

Write these lessons as durable rules, not as session notes. Prefer guidance such as “after implementing a frontend flow, verify the running app with Playwright and persisted data with the database” over a detailed account of a specific bug.

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

- Relevant docs are current.
- No duplicate or contradictory docs were introduced.
- Code matches the documented architecture.
- UI/API/database behavior matches what the user can actually do.
- Non-trivial frontend changes follow the documented module boundaries instead of adding more mixed responsibility to the app root.
- For PWA changes, Playwright or equivalent browser QA covered the live app.
- Post-implementation self-review completed.
- Self-review findings fixed, rejected with reason, or deferred into the owning spec/roadmap.
- Process docs were updated with generalized prevention rules if the work revealed a repeatable quality gap.
- Checks were run or skipped with a clear reason.
- Git status is clean after commit.
