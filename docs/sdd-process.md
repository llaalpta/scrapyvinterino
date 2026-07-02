# Spec Driven Development Process

This project uses Spec Driven Development to keep product intent, architecture, and implementation aligned.

## Default Flow

1. Understand the requested change.
2. Read the existing documentation that owns the affected area.
3. Update the existing document if the change affects behavior, architecture, risk, security, deployment, or data.
4. Define acceptance criteria.
5. Implement the smallest useful vertical slice.
6. Verify with focused checks.
7. Commit the code and documentation together.

Documentation and implementation should move together. A feature is not done if the relevant docs are stale.

## Documentation Maintenance Rule

Before creating a new document, check whether an existing document already owns the topic.

Update existing docs when possible:

- Product requirements: `docs/spec.md`
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
- Checks were run or skipped with a clear reason.
- Git status is clean after commit.
