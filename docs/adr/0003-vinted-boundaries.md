# ADR 0003: Vinted Integration Boundaries

## Status

Accepted

## Context

The project starts by monitoring public catalog data but may later use a personal authenticated Vinted session for favorites, checkout discovery, pre-purchase, and explicit purchase.

## Decision

Separate Vinted integration into public and authenticated boundaries.

- MVP uses public catalog access without Vinted login.
- Public scraping must use backend HTTP requests, with Playwright allowed only for research or anonymous cookie discovery if needed.
- Authenticated actions are future work and must be feature-flagged.
- Purchase actions must require explicit user confirmation and safety checks.

## Consequences

- The public catalog flow can be built and tested before handling secrets.
- Authenticated flows can have stricter logging, redaction, and auditing.
- The app will not implement automatic purchase immediately after scraping.
- Research docs must never store raw tokens, cookies, addresses, payment data, or personal payloads.
