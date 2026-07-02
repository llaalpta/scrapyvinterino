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
8. Run a post-implementation audit.
9. Fix or explicitly defer audit findings.
10. Commit the code and documentation together.

Documentation and implementation should move together. A feature is not done if the relevant docs are stale.

## Post-Implementation Audit

Every non-trivial implementation needs a second pass before it is considered done.

Use a separate agent for the audit when sub-agent tooling is available. The implementing agent should continue to own the final decision: fix valid findings, reject false positives with a reason, or move deferred work into the owning spec or roadmap.

The audit must answer:

- Does the implementation satisfy the active spec and acceptance criteria?
- Can the user exercise the promised flow end to end?
- Are API, UI, database, worker, and docs consistent with each other?
- Are there visible buttons, links, labels, or states that imply behavior that does not exist?
- Are invalid inputs and unavailable actions handled clearly?
- Were the right checks run, and is the evidence recorded?

For frontend work, a screen is not done just because it compiles. Navigation must land somewhere real, enabled buttons must perform their visible action, and future actions must be disabled, absent, or shown as an honest empty state.

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

### Audit

Post-implementation audit focus areas for this spec.
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
- Post-implementation audit completed or explicitly unavailable.
- Audit findings fixed, rejected with reason, or deferred into the owning spec/roadmap.
- Checks were run or skipped with a clear reason.
- Git status is clean after commit.
