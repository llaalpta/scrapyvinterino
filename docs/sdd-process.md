# Spec Driven Development Process

This project uses practical SDD to preserve product intent without turning every possible failure into immediate implementation work. `AGENTS.md` contains the non-negotiable summary; this document owns the detailed workflow.

## Operating principle

The current product is a personal local scraper, not an unattended distributed platform. Prefer a small useful behavior, honest failure and manual recovery over speculative exactly-once automation. Real integration evidence remains more valuable than a large mocked suite, but verification must be proportional to the promise being made.

## Work classification

| Class | Use when | Planning and review |
| --- | --- | --- |
| Micro | Mechanical change with no behavior, schema or process coordination; normally <=2 files/about 50 lines. | Implementation branch, focused check, self-review and a minimal diff-only audit. No planning branch or dedicated integration environment. |
| Standard | One observable outcome, <=3 runtime boundaries, <=3 acceptance criteria, <=1 migration and one QA setup. | One implementation branch, representative integration, self-review and one scoped independent audit. |
| Program | More than one outcome, multiple QA setups, or a schema + worker + PWA redesign. | `plan/<scope>` branch first; split into standard tasks before product code. |

Approximate standard-task warning levels are eight product files, 400 product lines and 500 new test lines. Pause at roughly ten product files/500 product lines, a second migration/outcome or a second QA environment. These numbers expose accidental scope growth; they are not incentives to fill a quota.

Give a concise progress checkpoint after about 60-90 minutes of active work. If the remaining work no longer fits the original outcome, stop and split it rather than continuing silently.

## Default flow

1. Inspect `git status --short --branch`, the first incomplete `Now` item and its owning docs.
2. Confirm the task class, desired outcome, exclusions and actual dependencies.
3. For a program only, persist the decomposition on a product-code-free planning branch and integrate it first.
4. Define no more than three acceptance criteria, one real representative scenario, one relevant negative variation, cleanup and external-traffic allowance.
5. Obtain one confirmation for the task. It covers the complete branch lifecycle through automatic PR publication after a positive independent audit. Merging that PR and starting the next task each remain separately user-gated; no intermediate confirmation is needed inside the agreed contract.
6. Branch from updated `develop`. Update the owning behavior/decision docs before or with code.
7. Implement the smallest complete vertical slice and run focused checks during development.
8. Exercise the real boundary promised by the task, inspect durable/runtime evidence and clean all QA state.
9. Run the implementer self-review, then the proportional independent audit automatically.
10. Fix only in-scope findings, rerun only affected evidence, update owning docs and roadmap status/evidence as needed, and request finding-specific re-audit until the verdict is positive or the loop ceiling blocks and splits the task.
11. After a positive verdict, commit the coherent task, push normally and open its PR to `develop` automatically. A dependent task waits for review/merge.
12. Do not merge that PR or start the next task without separate explicit user authorization.

## Task contract

Record only information that changes implementation or acceptance:

- one user-visible or operational outcome;
- owning spec/document and real prerequisites;
- affected API/process/state boundaries;
- up to three acceptance/failure criteria;
- one representative integration path and one negative variation;
- exact cleanup and initial/final service state;
- external request allowance, normally zero;
- explicit exclusions and accepted risks.

The roadmap stores priority, status and short outcomes. Detailed contracts live in the owning spec when the task starts. Do not duplicate the same contract in a second roadmap table, architecture prose and implementation diary.

## Verification matrix

Choose the smallest row that proves the behavior:

| Changed promise | Primary evidence | Supporting checks |
| --- | --- | --- |
| Pure calculation, validation or redaction | Focused unit test | Ruff/type/build as applicable. |
| API + persistence | One request through the live API and the resulting/absent PostgreSQL row | Focused API tests for field matrices. |
| Worker/queue | One real Redis payload through the consumer and its PostgreSQL/event/ACK outcome | Unit cases for malformed payloads and deterministic races. |
| Scheduler/lifecycle | Real process/container start, outage or restart only for the service under contract | Focused timing/config tests. |
| PWA | One Playwright flow against the running app, API response and persisted state | Frontend lint/build. |
| External provider semantics | Bounded authorized call only when local control cannot prove the external contract | Local fake at the network boundary for failures and redaction. |
| Documentation/process | Clean diff, valid references, consistency search and a dry run of branch/gate decisions | No unrelated containers. |

Use a single real representative case; do not reproduce every parameter combination across containers. Unit tests own matrices, malformed inputs, safe canaries and precise concurrency barriers.

Run the full backend suite once only when the task affects a migration, authentication/security, shared concurrency primitive or central runtime path. Other tasks use focused tests locally and the normal PR/CI regression gate when available. Record duration when a suite is large enough to affect workflow decisions.

## Current recovery boundary

Docker Compose restarts worker/watchdog after unexpected process exit. Redis reservations may be recovered by the existing implementation, but this is best-effort rather than an exactly-once promise.

For the current personal operating model, an ambiguous crash may require the user to inspect logs/PWA and relaunch a session. Do not add durable preparation/use ledgers, distributed visibility timeouts, coordinated drain or AOF recovery projects unless a reproducible normal-use failure or a new production target justifies them.

Required dependencies fail visibly. A fallback, degraded mode, new compatibility adapter, automatic retry or alternate provider needs explicit product value and user authorization. Explicitly inventoried legacy tombstones may remain until the next route-focused microtask, but no new ones are added.

## Self-review

The implementer reviews the final diff directly and answers:

- Does it deliver only the active outcome and match the owning contract?
- Can the user exercise the promised path end to end?
- Are error states and visible UI honest?
- Are API, database, Redis/process state, events and docs consistent where touched?
- Did verification use the correct real boundary and leave no QA residue?
- Did any test complexity exceed the value of the risk it covers?

Do not delegate this decision.

## Independent audit

Every completed task receives one read-only rubber-duck audit by the least expensive suitable independent reviewer. A micro audit stays limited to the final diff, instructions and focused-check evidence; it does not justify a dedicated integration environment. For standard work, limit the prompt to:

- the task diff and owning contract;
- the representative real evidence;
- the two highest-risk failure modes declared before implementation;
- stale docs, secrets/legacy residue and cleanup within the changed surface.

Classify findings:

- **A — blocking:** reproducibly violates acceptance, security or data integrity. Fix before closure.
- **B — contained:** improves the same outcome and adds no more than modest scope. Fix only if it remains inside the task.
- **C — adjacent:** a new outcome, speculative race or production hardening. Record as accepted/conditional risk; do not expand the branch automatically.

One clean pass produces the positive verdict. After a fix, request only a finding-specific recheck. Two loops are normal; three total is the ceiling. If no suitable reviewer is available or no positive verdict is reached, the task is blocked or split and no closure commit, push or PR is published. A second self-review cannot replace the independent audit.

## Branches, confirmations and PRs

- Use one implementation branch per coherent task, based on current `develop`.
- A user instruction such as "continua con 14.x" authorizes the complete lifecycle of that one task through automatic PR publication after a positive independent audit.
- Do not request additional confirmation for in-scope docs, tests, audit fixes, the closure commit, normal push or opening the PR.
- Merging the PR and starting the next task each require separate explicit user authorization. Merge a true prerequisite before dependent work; independent roadmap items are ordered by value, not a synthetic `After previous` chain.
- Never delete local/remote branches or rewrite published history. Full Git safety rules live in `AGENTS.md`.

## Documentation maintenance

Before adding a file, use the existing owner:

- product summary/decisions: `docs/spec.md`, `docs/product-decisions.md`;
- priority: `docs/roadmap.md`;
- behavior: `docs/specs/`;
- current system/state: `docs/architecture.md`, `docs/data-model.md`;
- local QA/deployment/security: `docs/development.md`, `docs/deployment.md`, `docs/security.md`;
- dated provider facts: `docs/research/`;
- durable technical decisions: `docs/adr/`.

Before code, document decisions and acceptance that implementation needs. After verification, update current-state prose and concise evidence. Do not pre-write speculative implementation detail or keep superseded current behavior.

Add a process rule only when a problem is safety-critical or the pattern has occurred at least twice. One audit observation belongs in its owner/risk decision, not automatically in `AGENTS.md` or `Now`.

## Frontend QA

For changed UI behavior, select either the existing Docker frontend or the isolated Vite workflow in `docs/development.md`; never run competing servers. Use Playwright for one successful flow and one relevant failure, then confirm API/database state when persistence is involved. Starting worker/external traffic requires the task's explicit allowance.

## Completion checklist

- Branch and task class match the requested outcome.
- Acceptance contains at most three meaningful criteria.
- Owning docs describe current behavior without duplication.
- Focused checks and the representative real path passed.
- Negative behavior is visible and mutation-free where required.
- QA rows, Redis keys, sessions and temporary processes were cleaned; initial services were restored.
- Implementer self-review completed.
- Proportional independent audit completed; A findings closed and C findings did not expand scope.
- Roadmap status/evidence updated; after a positive audit, the coherent commit, normal push and PR to `develop` were completed automatically.
- Work stopped before the next task pending confirmation.
