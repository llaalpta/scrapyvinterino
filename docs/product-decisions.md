# Product Decisions

This document records product direction decisions. It is updated when product intent changes. It does not replace ADRs for technical architecture.

| Date | Decision | Context | Consequence |
| --- | --- | --- | --- |
| 2026-07-02 | Build a private PWA as the primary interface. | Telegram/webhook notifications are useful later, but item actions need a controlled UI. | The MVP focuses on a web dashboard with tables, filters, sources, runs, and actions. |
| 2026-07-02 | Use Telegram and push notifications later. | Monitoring in the web app is more important for the first usable workflow. | Notification channels remain pluggable but are not MVP-critical. |
| 2026-07-02 | MVP does not use Vinted login. | Public catalog monitoring should be stable before authenticated actions. | Authenticated account login remains deferred; preparing and reusing public anonymous request context is allowed for catalog monitoring. |
| 2026-07-02 | Future authenticated actions may use a personal Vinted account. | The app is personal and may later mark favorites, inspect checkout, preselect options, and buy after explicit confirmation. | Authenticated actions require feature flags, secret handling, redacted logs, and audit records. |
| 2026-07-02 | No automatic purchase immediately after scraping. | Buying should remain a deliberate user action. | Purchase must require explicit UI confirmation and safety validation. |
| 2026-07-02 | Develop locally without Traefik. | The production server is a different machine. | `docker-compose.yml` exposes local ports directly; Traefik is reserved for production configuration. |
| 2026-07-02 | Production target is Docker behind Traefik and Cloudflare. | The app should later deploy cleanly to the user's server. | Keep a separate production compose example and do not let Traefik complicate local development. |
| 2026-07-03 | Complete scheduler concurrency and runtime cache before filters and opportunities. | The project values a robust first release over a rushed MVP that later needs optimization work. | Bounded scheduler concurrency, global item cache, isolated anonymous sessions, and optional proxy configuration are MVP requirements before alert-producing features. |
| 2026-07-06 | Do not preserve backward compatibility before the first production release. | The product is still pre-production and local data is disposable. Carrying legacy endpoints, schemas, or UI flows slows down model correction. | Breaking migrations and API/UI removals are allowed when they simplify the MVP. Update docs/tests with the current contract and remove legacy compatibility instead of maintaining adapters. |
| 2026-07-13 | Optimize for a personal local service, not unattended exactly-once operation. | The app has one user; manual Compose maintenance and relaunch from the PWA are acceptable when failures are visible. | Keep best-effort recovery already present, but do not prioritize durable crash ledgers, coordinated drain or distributed queue recovery. |
| 2026-07-13 | Deliver Telegram as the first notification channel. | The web monitoring path has produced real recurring runs and opportunities; a mobile alert now adds more value than speculative production hardening. | Implement one optional Telegram message per new opportunity before PWA push or additional channels, without a retry/outbox platform. |
