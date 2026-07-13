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

- The safe local default starts only infrastructure/API: `docker compose up -d --build postgres redis api`; frontend is added explicitly after checking its port.
- A full Compose start also starts worker and watchdog. It is operational execution, not a harmless smoke check: inspect active monitors and Redis ready/processing state and confirm any external-traffic budget first.
- Production routing can evolve independently.
- Frontend, API, worker, watchdog, PostgreSQL and Redis stay as separate services.
