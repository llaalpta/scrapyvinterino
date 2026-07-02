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
2. Update the existing spec, research note, ADR, or product decision record if the change affects behavior or direction.
3. Define acceptance criteria before coding.
4. Implement the smallest vertical slice that satisfies the criteria.
5. Run focused verification.
6. Commit a coherent, small change.

Small mechanical fixes can skip a formal spec update, but they must not contradict existing docs.

## Documentation Hygiene

Documentation is maintained, not accumulated.

- Update existing documents before creating new ones.
- Do not create overlapping files such as `spec-v2.md`, `architecture-new.md`, `final-notes.md`, or session-specific docs.
- If a topic already belongs in `docs/spec.md`, `docs/architecture.md`, `docs/development.md`, `docs/deployment.md`, `docs/security.md`, `docs/risks.md`, `docs/data-model.md`, or `docs/research/`, update that file.
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

If a check cannot run, document the reason in the final response.

## Git

- Keep commits small and descriptive.
- Do not commit generated caches, secrets, local `.env`, or dependency folders.
- Do not revert user changes unless explicitly requested.
- Check `git status --short --branch` before and after work.
