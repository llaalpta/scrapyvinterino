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
7. Run a post-implementation audit for non-trivial changes.
8. Resolve audit findings or document why they are deferred.
9. Commit a coherent, small change.

Small mechanical fixes can skip a formal spec update, but they must not contradict existing docs.

## Post-Implementation Audit Gate

Non-trivial changes must be audited before a spec is marked `done` or a final implementation response is given.

The audit should be performed by a separate agent when sub-agent tooling is available. If it is not available, perform an explicit second-pass review and say that no separate agent was available.

The audit must check:

- Spec alignment: implemented behavior matches the active spec and acceptance criteria.
- UX honesty: visible controls, navigation, labels, and actions do not imply unavailable behavior.
- End-to-end path: the user can exercise the promised flow through UI, API, and database where applicable.
- Negative paths: invalid input and unavailable actions are handled clearly.
- Documentation state: roadmap/spec/docs reflect the actual implementation state.
- Verification evidence: tests, build, smoke checks, or manual checks cover the changed surface.

Do not mark a roadmap item `done` until audit findings are fixed, downgraded with a clear reason, or moved into the owning spec/roadmap item.

For frontend work, unavailable future behavior must be absent, visibly disabled, or represented as an empty state. Do not leave clickable placeholders that look complete.

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
- The MVP must not use a Vinted login.
- Future authenticated actions must be feature-flagged and audited.
- Purchase actions must require explicit user confirmation and validation of price, currency, availability, shipping, and payment choice.
- Do not implement aggressive anti-bot bypassing or captcha solving.

## Verification

Prefer focused checks for the area changed:

- Backend: `ruff check backend/src backend/alembic`
- Frontend: `pnpm build` from `frontend/`
- Docker: `docker compose up -d --build` and `docker compose ps`
- API smoke test: `GET http://localhost:8000/health`
- Frontend smoke test: `GET http://localhost:5173`
- UI promise audit: confirm every visible button/link either works, is disabled, or lands on a real empty state.

If a check cannot run, document the reason in the final response.

## Git

- Keep commits small and descriptive.
- Do not commit generated caches, secrets, local `.env`, or dependency folders.
- Do not revert user changes unless explicitly requested.
- Check `git status --short --branch` before and after work.
