# ADR 0004: Local and Production Deployment

## Status

Accepted

## Context

Development happens on a local machine. Production will later run on a different server behind Traefik and Cloudflare.

## Decision

Keep local and production deployment concerns separate.

- `docker-compose.yml` is for local development and exposes direct localhost ports.
- Production will use a separate compose file with Traefik labels and no public Postgres exposure.
- Local development must not depend on Traefik, Cloudflare, or the production server.

## Consequences

- Local development remains simple: `docker compose up -d --build`.
- Production routing can evolve independently.
- The frontend, API, worker, and database stay as separate services.
