# ADR 0001: Application Stack

## Status

Accepted

## Context

The project needs a backend for scraping, persistence, scheduler work, authenticated future actions, and a private web app for operation.

## Decision

Use:

- Python 3.12 and FastAPI for the API.
- A Python worker service for scraping, scheduling, filtering, and future actions.
- PostgreSQL with SQLAlchemy and Alembic for persistence.
- React, Vite, TypeScript, and PWA support for the frontend.
- Docker Compose for local development.

## Consequences

- Python keeps scraping, HTTP, parsing, jobs, and backend logic in one ecosystem.
- React/Vite keeps the private PWA lightweight without Next.js SSR complexity.
- PostgreSQL avoids an early SQLite-to-Postgres migration.
- API, worker, frontend, and database can be deployed independently later.
