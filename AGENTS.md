# Agent Instructions

This repository uses practical Spec Driven Development. Keep documentation current, compact and non-overlapping.

## Product and operating model

The current target is a personal, private, single-user Vinted catalog monitor:

- configure public catalog URLs from the PWA;
- prepare public anonymous sessions without a Vinted account login;
- run manual and recurring monitors through the real API/worker/PostgreSQL/Redis/PWA path;
- start each observation session from an automatic catalog baseline, then persist and filter only later opportunities;
- run locally with Docker Compose, with manual maintenance and relaunch accepted.

This is not yet an unattended 24/7 production service. One worker instance, best-effort queue recovery and a rare duplicate around abrupt process death are acceptable when visible. Do not build exactly-once ledgers, distributed recovery or production hardening unless the roadmap/user explicitly promotes them.

Notifications and authenticated Vinted actions remain future work. Do not implement alerts, favorites, checkout, pre-purchase or purchase flows until the public monitoring MVP is manually accepted and the user explicitly reprioritizes them.

## Required workflow

Work on one roadmap outcome at a time:

1. Run `git status --short --branch`, inspect `docs/roadmap.md` and read the owning docs.
2. Classify the work as micro, standard or program using `docs/sdd-process.md`.
3. Use `plan/<scope>` only for a program or a priority/dependency redesign. A standard task already defined by the roadmap starts on its implementation branch; an authorized bounded external request does not change its class by itself.
4. Define at most three acceptance criteria, one representative real scenario, one relevant negative path, cleanup and any external-traffic allowance.
5. Obtain one user confirmation for the task. That confirmation authorizes its branch, docs, implementation, verification, self-review, bounded audit, audit fixes and, after a positive audit verdict, the coherent commit, normal push, PR and non-destructive merge to `develop`. Starting the next task still requires separate explicit user authorization.
6. Branch from an updated `develop`, update the owning behavior/decision documentation and implement only that outcome.
7. Run focused checks, then the smallest real integration path that proves the affected boundaries. Bring owning docs and roadmap status/evidence to their intended final state before review. Do not manufacture unrelated container work.
8. Run the implementer self-review directly, followed by the proportional independent read-only audit below.
9. Fix in-scope findings, recheck only the affected evidence, update owning docs and roadmap as needed, and request finding-specific re-audit until the verdict is positive or the loop ceiling blocks and splits the task. After a positive verdict, commit, push normally and open the PR to `develop` automatically; when it is mergeable and all configured required checks pass, merge it with a merge commit without deleting the branch.
10. Do not open the next task branch without separate explicit user authorization. If the PR cannot be merged normally, stop and report the exact blocker; never rewrite history or delete refs to force closure. A true prerequisite must be merged before dependent work begins.

Small mechanical fixes may skip a spec update when they do not change behavior or contradict current docs.

## Multi-agent policy

The primary agent owns objective interpretation, planning, architecture, prioritization, orchestration, integration, mandatory instruction reading, implementer self-review and final judgment. Preserve its context and reasoning budget for decisions that require the strongest reasoning.

Delegate only concrete, bounded subtasks when they can run independently and the expected benefit exceeds coordination cost. When capability selection is available:

- use the least expensive capable agent for narrow repository searches, inventories, routine commands, linting, builds and mechanical verification;
- use a general implementation agent for scoped coding, debugging, refactoring, testing and focused review;
- reserve the highest-reasoning agent for architecture, security, data integrity, cross-service races, ambiguous failures and final tradeoffs.

Each subagent validates its own scope and reports changed files, commands and evidence, findings and unresolved risks. The primary agent reviews the resulting diff and key evidence, but does not repeat successful routine checks unless results conflict, are incomplete or affect security, data integrity or architecture.

Do not delegate mandatory instruction reading, product decisions, roadmap priority, final integration or implementer self-review. Do not assign overlapping files or mutable runtime state to concurrent agents. Avoid delegation for micro work when coordination would cost more than direct execution. Independent audits remain read-only and follow the policy below.

## Task sizing and stop rules

- **Micro:** no behavior/schema/process coordination; normally at most two files and about 50 changed lines. Use a focused check, self-review and a minimal diff-only independent audit; do not create an integration environment solely for that audit.
- **Standard:** one observable invariant or user outcome, no more than three runtime boundaries, one migration at most and one QA setup with a negative variation.
- **Program:** multiple outcomes, multiple QA setups, or a schema + worker + PWA redesign. Plan and split it before product code.

For a standard task, roughly eight product files, 400 product lines and 500 new test lines are warning thresholds, not targets. Pause and split when the work crosses about ten product files/500 product lines, introduces a second outcome or migration, or requires a second QA environment. Give a status checkpoint after roughly 60-90 minutes of active work; do not silently turn one task into a multi-hour program.

Use one representative real integration case. Put field matrices, malformed inputs, redaction and deterministic races in focused unit tests. Run the full backend suite at most once near closure and only when schema, security, shared concurrency or core runtime risk warrants it.

## Failure and compatibility policy

Before the first production release, remove obsolete development-only contracts rather than adding compatibility adapters. Explicitly inventoried tombstones may remain temporarily as accepted legacy until the next route-focused microtask; do not create new ones.

Required failures are visible and fail-stop. Do not add degraded modes, fallback providers, hidden refreshes, silent retry loops or automatic recovery merely to obtain a green test. Manual restart and PWA relaunch are valid for the current operating model.

Existing Redis reservation recovery is best-effort implementation, not a product guarantee. Do not expand it into exactly-once crash recovery without a new explicit product decision.

## Self-review and independent audit

The implementing agent must personally review every non-trivial change for spec alignment, honest UX, end-to-end behavior, negative paths, documentation and verification evidence.

Then run one tightly scoped independent read-only audit using the least expensive suitable reviewer available. It reviews only the diff, owning contract, real evidence and the two main declared risks. It must not edit, commit, call external services or reopen unrelated architecture.

Classify findings:

- **A:** violates acceptance, security or data integrity; reproduce and fix before closure.
- **B:** hardening of the same outcome that adds little scope; fix only when it stays contained.
- **C:** adjacent/new outcome or theoretical platform hardening; record as accepted/conditional risk and do not expand the branch.

One clean pass closes the audit. After a fix, recheck only that finding. Two loops are the default; a third is the absolute ceiling before splitting. Keep a micro audit to its final diff, instructions and focused-check evidence. A missing reviewer or a verdict that is not positive at the loop ceiling blocks closure and commit/push/PR/merge publication.

## Frontend and integration QA

Frontend behavior must be checked against the running app with Playwright when UI, navigation, forms or visible state change. Verify one success, one relevant rejection and API/database consistency when persistence is involved.

Before starting/restarting frontend QA, inspect existing Docker/Vite ownership and ports. Use `scripts/qa-pwa.ps1` only when its worker/external-traffic behavior is authorized; otherwise follow the worker-stopped procedure in `docs/development.md`. Do not start competing Vite instances or rebuild unrelated services.

Keep `frontend/src/App.tsx` thin; composition belongs in `frontend/src/app/`, cross-feature hooks in `frontend/src/hooks/`, views in `frontend/src/features/`, shared UI in `frontend/src/components/`, helpers in `frontend/src/utils/` and CSS in `frontend/src/styles/`.

Operational acceptance should use the real boundary that matters: live API for API contracts, PostgreSQL for persistence, Redis/consumer for queue behavior, Playwright for PWA behavior and real process/container restart only when lifecycle is the task. Synthetic providers are appropriate at the external boundary when Vinted/proxy behavior is not under test.

Never call Vinted, a proxy or Telegram without an explicit bounded allowance. Clean QA rows, Redis state and temporary processes, and restore the initial service state.

## Documentation ownership

- `docs/roadmap.md`: short priority/status queue and real dependencies only.
- Feature specs: behavior, interfaces and acceptance.
- `docs/architecture.md`: current cross-service ownership.
- `docs/deployment.md`: service lifecycle and operator recovery.
- `docs/data-model.md`: durable/transient state and transactions.
- `docs/development.md`: local workflows and QA commands.
- `docs/security.md`: secrets, redaction and trust boundaries.
- `docs/sdd-process.md`: detailed task classification, gates and verification.
- ADRs/research: durable decisions and dated observations.

Update existing owners instead of creating overlapping documents. Remove superseded current-state prose. Add a generalized process rule only for a safety-critical problem or a failure pattern observed more than once; an isolated audit finding does not automatically grow `AGENTS.md` or the roadmap.

## Safety

- Never commit or print secrets, raw cookies, tokens, addresses, payment data or personal Vinted session details.
- MVP scraping uses local PWA login plus public anonymous Vinted context, not an authenticated Vinted account.
- Future authenticated actions must be feature-flagged and audited; purchases require explicit confirmation and validation.
- Do not broaden anti-bot/captcha behavior beyond the active spec and authorized task.

## Focused verification

- Backend lint: `ruff check backend/src backend/alembic`.
- Frontend build: `pnpm build` from `frontend/`; run lint when frontend source changes.
- Compose/API: inspect `docker compose ps`; start only required services; `GET http://localhost:8000/health` is process liveness only.
- Worker/watchdog: start only when accepted by the task after inspecting active monitors and queue state.
- PWA: Playwright against the live selected frontend, plus API/database checks.

Document any check that cannot run and why.

## Git safety and branch discipline

- Use one short-lived branch per coherent task, based on `develop`; after its positive gate, push it normally, open its PR and merge it to `develop` automatically when the remote gate is green.
- Keep commits small and descriptive. Merge prerequisites before dependent work.
- Treat every published/local/remote branch, tag and commit as durable history.
- Commits, normal pushes, PRs, reviews and non-destructive merges after the positive gate need no additional confirmation. Use a merge commit and preserve the source branch.
- Never delete branches/tags/refs, use automatic post-merge deletion, force-push, rebase/squash published history, reset shared branches or run pruning that can make work unreachable.
- If deletion or rewriting appears necessary, stop and request explicit authorization for exact refs and recovery.
- Do not commit caches, `.env`, dependencies or generated local artifacts. Do not revert user changes without explicit instruction.
- Check `git status --short --branch` before and after work, and report branch, commit and verification evidence.
