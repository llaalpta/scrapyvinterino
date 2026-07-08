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

Before implementing a non-trivial change:

1. Read the relevant docs in `docs/`.
2. Check `docs/roadmap.md`; work on the first incomplete `Now` item unless the user explicitly changes priority.
3. Update the existing spec, research note, ADR, or product decision record if the change affects behavior or direction.
4. Define acceptance criteria before coding.
5. Implement the smallest vertical slice that satisfies the criteria.
6. Run focused verification.
7. Run an explicit implementer self-review for non-trivial changes.
8. Resolve self-review findings or document why they are deferred.
9. Commit a coherent, small change.

Small mechanical fixes can skip a formal spec update, but they must not contradict existing docs.

## Compatibility Policy

Until the first production release, do not preserve backward compatibility with previous development-only contracts, data, endpoints, or UI flows. When a pre-production model changes, update the owning docs/tests and remove obsolete legacy adapters instead of maintaining parallel behavior.

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

Frontend structure is part of frontend quality. For non-trivial PWA work, keep `frontend/src/App.tsx` as a thin root, put dashboard-level composition in `frontend/src/app/`, cross-feature state hooks in `frontend/src/hooks/`, feature views in `frontend/src/features/`, shared UI in `frontend/src/components/`, generic helpers in `frontend/src/utils/`, and CSS under `frontend/src/styles/`. Split mixed-responsibility files before adding new behavior to them.

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

Prefer focused checks for the area changed:

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
- Do not commit generated caches, secrets, local `.env`, or dependency folders.
- Do not revert user changes unless explicitly requested.
- Check `git status --short --branch` before and after work.
