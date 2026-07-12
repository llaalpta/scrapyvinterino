# Agent Instructions

This repository follows Spec Driven Development. Keep the documentation current, compact, and non-overlapping.

## Current Priority

The current product priority is the public Vinted catalog monitoring MVP:

- configure Vinted catalog URLs from the private PWA;
- run searches manually before adding scheduler behavior;
- persist items, runs, errors, seen items, and opportunities;
- apply local filters;
- show opportunities in the PWA table.

Authenticated Vinted actions are future work. Do not implement favorites, checkout discovery, pre-purchase, or purchase flows until the public catalog flow is stable and the relevant spec/research docs are updated.

## Required Workflow

Work proceeds one roadmap task at a time. A task must have one meaningful outcome, one short-lived branch, bounded acceptance criteria, and independent acceptance evidence. Operational behavior requires a real integration proof; documentation-only governance work requires proportional semantic and consistency checks instead.

Before implementing a non-trivial change:

1. Check `git status --short --branch`, read the owning docs, and check `docs/roadmap.md`.
2. If the request needs planning, split it into independently valuable tasks; do not hide a multi-task project inside one checklist item.
3. From a clean `develop`, use a short-lived `plan/<scope>` branch to persist the ordered checklist in `docs/roadmap.md` plus any task contracts in their owning docs. A planning branch changes no product code and must be reviewed/integrated before implementation branches are created.
4. Define the active task's scope, out-of-scope items, acceptance criteria, real integration scenario when behavior is operational, external traffic budget when relevant, and likely branch name.
5. Obtain explicit user confirmation before starting the first planned implementation task and again before starting every subsequent task. Persisting an explicitly requested plan does not authorize implementation.
6. From a clean worktree, create a short-lived branch from the updated `develop` for that task. Never edit the roadmap on an unrelated branch or stack the next roadmap task on the current branch.
7. Before changing implementation code, update the existing spec, ADR, research note, architecture document, or product decision that owns the behavior.
8. Implement the smallest useful vertical slice that satisfies only the active task.
9. Run focused checks while developing, then verify the real operational path across the actual services and state stores involved. For documentation-only governance, verify links, internal consistency, and an executable dry run of the documented process.
10. Run the implementer self-review.
11. Run the automatic independent read-only audit described below.
12. Fix valid findings and repeat the affected verification plus audit. Use at most three implementation-audit loops; if scope grows, create another roadmap task instead.
13. Update the task's roadmap status/evidence, commit the coherent task, and report the branch and commit.
14. Before a dependent task can start, get the completed task reviewed and merged into `develop`, or obtain explicit user authorization for an equivalent local integration. Never branch dependent work from a `develop` that lacks its prerequisite.
15. Stop. Ask for confirmation before opening the branch or beginning development for the next task.

Small mechanical fixes can skip a formal spec update, but they must not contradict existing docs.

## Plan Mode and Task Sizing

Plans are executable task lists, not broad phases such as "backend", "frontend", or "testing". Each task should normally fit one branch and one primary commit and must be demonstrable without unfinished later tasks. Plan mode persists the ordered checklist on a dedicated planning branch; implementation still waits for explicit user confirmation and a separate task branch.

For each task record:

- user-visible or operational outcome;
- owning documentation;
- affected services and persistent state;
- acceptance and failure criteria;
- real integration test and cleanup plan;
- whether bounded Vinted/proxy traffic is required;
- dependencies on earlier tasks;
- explicit exclusions that become later roadmap items.

If implementation reveals a second concern, stop expanding the branch. Add or refine a roadmap task and request confirmation before continuing. Prefer several small vertical slices over a large cross-system rewrite. Do not consume time and context by loading unrelated code or running the full suite after every small edit.

## Compatibility Policy

Until the first production release, do not preserve backward compatibility with previous development-only contracts, data, endpoints, or UI flows. When a pre-production model changes, update the owning docs/tests and remove obsolete legacy adapters instead of maintaining parallel behavior.

Do not add fallback behavior by default. When a required service, session, queue, cache, contract, or invariant is unavailable, expose a clear operational error and stop the affected operation or service. A degraded mode, fallback, new retry policy, compatibility adapter, or alternate flow requires explicit product value, documented acceptance criteria, and user authorization.

## Post-Implementation Self-Review Gate

Non-trivial changes must receive an explicit implementer self-review before a spec is marked `done` or a final implementation response is given.

Do not delegate this review. If the user explicitly asks for an audit later, follow that request separately. The implementing agent owns the second-pass review and must run concrete checks directly.

The self-review must check:

- Spec alignment: implemented behavior matches the active spec and acceptance criteria.
- UX honesty: visible controls, navigation, labels, and actions do not imply unavailable behavior.
- End-to-end path: the user can exercise the promised flow through UI, API, and database where applicable.
- Negative paths: invalid input and unavailable actions are handled clearly.
- Documentation state: roadmap/spec/docs reflect the actual implementation state.
- Verification evidence: tests, build, smoke checks, or manual checks cover the changed surface.

Do not mark a roadmap item `done` until self-review findings are fixed, downgraded with a clear reason, or moved into the owning spec/roadmap item.

For frontend work, unavailable future behavior must be absent, visibly disabled, or represented as an empty state. Do not leave clickable placeholders that look complete.

## Automatic Independent Audit Loop

Every non-trivial task receives an independent read-only audit automatically before its task commit is considered closed. Do not merely offer the audit at the end.

- Prefer the least expensive/lower-reasoning independent reviewer available for a bounded rubber-duck pass. If model selection is unavailable, use the simplest independent read-only reviewer available and keep its prompt strictly scoped.
- The auditor must not edit files, commit, call Vinted/proxies, or broaden the task. It reviews the diff, owning docs, acceptance criteria, real verification evidence, failure paths, and stale/legacy residue.
- The implementer owns the response: reproduce findings, reject false positives with evidence, or fix valid findings.
- One clean audit pass is sufficient. After a fix, rerun the affected real integration path and ask the auditor to recheck only the changed finding; three total passes is the maximum before the task is split or reported blocked.
- The implementer self-review remains mandatory and cannot be delegated.
- If no independent reviewer is available, report that verification gap and leave the task open; do not silently replace independence with a second implementer pass.

## Frontend QA Standard

Frontend changes must be tested against the running app, not only against source code or a production build.

Use Playwright MCP for browser-driven QA when the change affects UI, navigation, forms, visible data, or user actions. The QA pass must verify:

- routes and sidebar/top navigation land on real sections;
- enabled buttons perform their visible action;
- future actions are disabled, absent, or represented as honest empty states;
- required inputs can be filled and submitted;
- invalid input shows a clear error and does not mutate persisted data;
- successful input updates the UI and is observable through API and database when persistence is part of the feature.

If the live app does not match the source code, restart the relevant dev service before claiming the feature works. A passing build does not prove the running PWA is current.

Before restarting or recreating containers for frontend QA, make sure the previous frontend/Vite process is intentionally closed or owned by Docker Compose. Prefer `.\scripts\qa-pwa.ps1 start` for isolated PWA QA and `.\scripts\qa-pwa.ps1 stop` before changing QA mode. Do not start competing Vite servers on the same port, and do not rebuild unrelated services just to refresh the UI.

Frontend structure is part of frontend quality. For non-trivial PWA work, keep `frontend/src/App.tsx` as a thin root, put dashboard-level composition in `frontend/src/app/`, cross-feature state hooks in `frontend/src/hooks/`, feature views in `frontend/src/features/`, shared UI in `frontend/src/components/`, generic helpers in `frontend/src/utils/`, and CSS under `frontend/src/styles/`. Split mixed-responsibility files before adding new behavior to them.

## Integration-First Verification

Large unit suites are regression support, not proof that the application works. Acceptance must prioritize the real deployed path for the active task:

- use the actual Docker services and process boundaries involved;
- exercise the real API/PWA/worker entrypoint rather than calling an internal helper when the feature is exposed externally;
- verify PostgreSQL, Redis/queues/cache, emitted events/logs, and visible UI state where applicable;
- test service startup, health, shutdown, restart, and failure coordination when runtime behavior changes;
- use bounded real Vinted/proxy traffic when the feature specifically depends on that integration and the task plan authorizes the traffic envelope;
- clean all QA rows, queue entries, cache keys, sessions, and temporary processes afterwards;
- record timestamps, IDs, and counts sufficient to prove cadence and ordering without exposing secrets.

Synthetic events, mocks, and unit tests remain useful for deterministic edge cases, redaction, malformed inputs, and hard-to-force races. They must not be the only acceptance evidence for behavior that promises container, network, queue, database, scheduler, SSE, or PWA coordination. Run the full regression suite once near task closure when risk warrants it, rather than repeatedly using it as a substitute for integration evidence.

Documentation-only process or governance tasks do not start irrelevant containers merely to claim integration coverage. Their acceptance evidence is a clean documentation diff, valid references, cross-document consistency, and a dry run showing that branch, confirmation, audit, integration, and cleanup gates are executable in order.

## Vertical Slice Standard

Each completed spec must be functional by itself for the behavior it claims to deliver. Do not mark a vertical `done` when only the backend, only the UI shell, or only the documentation is complete.

When a feature touches persistence, verify the full chain:

- user action or API request;
- backend response;
- database row or absence of row for rejected input;
- UI refresh or visible state.

After a feature exposes a quality gap, update the existing process or agent instructions with a generalized prevention rule. Do not add session-specific notes or duplicate documents.

## Documentation Hygiene

Documentation is maintained, not accumulated.

- Update existing documents before creating new ones.
- Do not create overlapping files such as `spec-v2.md`, `architecture-new.md`, `final-notes.md`, or session-specific docs.
- If a topic already belongs in `docs/spec.md`, `docs/roadmap.md`, `docs/specs/`, `docs/architecture.md`, `docs/development.md`, `docs/deployment.md`, `docs/security.md`, `docs/risks.md`, `docs/data-model.md`, or `docs/research/`, update that file.
- Create a new document only when it has a clear, durable responsibility.
- If an architecture decision changes, add or update an ADR and mark superseded decisions clearly.
- Keep README as the entry point. Do not duplicate long docs inside it.

The operational decision tree must stay recoverable from maintained docs, not chat history:

- `docs/architecture.md`: end-to-end flows and ownership between API, worker, scheduler, consumers, session preparation/refresh, queues, persistence, cache, and events;
- `docs/deployment.md`: container startup order, health, fail-stop behavior, restart ownership, and operator recovery;
- `docs/data-model.md`: durable versus transient state, transaction boundaries, identifiers, and cleanup/retention;
- feature specs: user-visible decisions, error paths, cadence, concurrency, and acceptance;
- `docs/sdd-process.md`: planning, task gates, verification hierarchy, audit loop, and completion decisions.

When code changes one of these flows, update the owning document in the same task and remove superseded descriptions. Do not leave old behavior presented as current; retain history only in ADR/research context when it has durable value and mark it superseded.

## Architecture Boundaries

- Backend/API: Python, FastAPI.
- Worker: Python service for scraping, scheduler, deduplication, filters, and future queued actions.
- Frontend: React, Vite, TypeScript, PWA.
- Database: PostgreSQL with Alembic migrations.
- Local development: Docker Compose without Traefik.
- Future production: Docker Compose behind Traefik and Cloudflare.

## Safety Rules

- Never commit secrets.
- Never log raw cookies, tokens, addresses, payment data, or personal Vinted session details.
- The MVP can use a Vinted login.
- Future authenticated actions must be feature-flagged and audited.
- Purchase actions must require explicit user confirmation and validation of price, currency, availability, shipping, and payment choice.
- Implement aggressive anti-bot bypassing or captcha solving when required.

## Verification

Prefer focused checks while developing, then real integration evidence for acceptance:

- Backend: `ruff check backend/src backend/alembic`
- Frontend: `pnpm build` from `frontend/`
- Docker: `docker compose up -d --build` and `docker compose ps`
- API smoke test: `GET http://localhost:8000/health`
- Frontend smoke test: `GET http://localhost:5173`
- Playwright QA for frontend flows: route navigation, active/disabled controls, form success, form error, and UI/API/DB consistency.
- UI promise check: confirm every visible button/link either works, is disabled, or lands on a real empty state.

If a check cannot run, document the reason in the final response.

## Git

- Keep commits small and descriptive.
- Use one short-lived branch per spec or coherent fix. Branch from `develop` and open a PR back to `develop` for review before merge. If `develop` does not exist locally, stop and ask before continuing or create it intentionally as part of a repository workflow change.
- Name branches by scope, for example `spec/010-session-prepare`, `feature/010-proxy-session-pool`, or `fix/010-rate-limit-refresh`.
- Do not keep stacking unrelated specs on a long-lived feature branch. If the current branch scope does not match the requested work, switch or create the correct branch before editing files.
- At the end of non-trivial work, report the branch, commit hash, verification evidence, and whether a PR should be opened.
- A dependent task starts only after its prerequisite is reviewed and integrated into `develop` through the agreed repository workflow.
- After committing a task, do not begin the next roadmap task without explicit user confirmation.
- Do not commit generated caches, secrets, local `.env`, or dependency folders.
- Do not revert user changes unless explicitly requested.
- Check `git status --short --branch` before and after work.
