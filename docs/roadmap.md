# Roadmap

This roadmap decides what to do next. Work on the first incomplete item in `Now` unless there is an explicit product decision to change priority.

Status values:

- `not-started`
- `in-progress`
- `blocked`
- `done`

## Now

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 1 | done | Search sources | `docs/specs/001-search-sources.md` | Configure and list Vinted catalog URLs from API/PWA. |
| 2 | done | Vinted catalog research | `docs/specs/002-vinted-catalog-research.md` | Discover how catalog data is delivered and define provider contract. |
| 3 | done | Manual runs | `docs/specs/003-manual-run.md` | Trigger source execution manually and record run state. |
| 4 | done | Item persistence | `docs/specs/004-item-persistence.md` | Store normalized public catalog items. |
| 5 | done | Fast detection and seen tracking | `docs/specs/005-deduplication-and-opportunities.md` | Use fast catalog JSON, catalog identity, monitor traceability, and bounded detail fetch. |
| 6 | done | Bounded concurrent scheduler and runtime cache | `docs/specs/008-scheduler.md` | Run sources concurrently with limits, jitter, isolated anonymous sessions, and global item cache before alerting. |
| 7 | done | Results and opportunities browser | `docs/specs/007-opportunities-table.md` | Paginated results, source scrape traceability, filters, mobile cards, and separate tabs before creating opportunities. |
| 8 | done | Frontend structure baseline | `docs/development.md` | Split the PWA into layout, feature, shared component, helper, hook, and style modules before adding local filters. |
| 9 | done | Session exclusion filters, monitor, and proxy pool | `docs/specs/006-local-filters.md` | Launch monitor sessions with source snapshots, exclusion filters, opportunities, run monitor, and encrypted proxy profiles. |
| 10 | done | Source archive, time windows, and timed sessions | `docs/specs/001-search-sources.md`, `docs/specs/008-scheduler.md` | Archive sources safely, configure one daily time window with timepickers, and launch bounded sessions from now. |
| 11 | done | Opportunity monitors model correction | `docs/specs/001-search-sources.md`, `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/006-local-filters.md`, `docs/specs/008-scheduler.md` | Treat configured Vinted searches as reusable monitors with per-monitor dedupe, optional filters, and accumulated monitor metrics. |

## Next

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 12 | done | Fast opportunity pipeline with Redis seen cache | `docs/specs/005-deduplication-and-opportunities.md`, `docs/specs/006-local-filters.md`, `docs/specs/007-opportunities-table.md`, `docs/specs/008-scheduler.md` | Make Redis mandatory for monitor seen state, persist only opportunities as product results, and remove seen-results/session legacy. |
| 13 | not-started | Notifications | `docs/spec.md` | PWA push, Telegram, webhook, Discord, or email after web monitoring works. |

## Later

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 14 | not-started | Production deployment hardening | `docs/deployment.md` | Traefik and Cloudflare deployment details. |

## Future Authenticated Actions

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 15 | not-started | Authenticated actions | `docs/specs/009-authenticated-actions.md` | Favorites, checkout discovery, pre-purchase, and explicit purchase. |

## Roadmap Rules

- Do not skip ahead unless the user explicitly changes priority.
- If an item changes scope, update its existing spec instead of creating a parallel document.
- If a new item is needed, add it here and create a spec only if no existing document owns it.
- Mark an item `done` only when its acceptance criteria and verification steps are satisfied.
- Keep `docs/spec.md` as the product-level summary and `docs/specs/` as feature-level specs.
