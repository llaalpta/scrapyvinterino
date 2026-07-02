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
| 5 | not-started | Fast detection and opportunities | `docs/specs/005-deduplication-and-opportunities.md` | Use fast catalog JSON, source dedupe, bounded detail fetch, and create opportunities once. |
| 6 | not-started | Local filters | `docs/specs/006-local-filters.md` | Apply application-owned filters after fetching results. |
| 7 | not-started | Opportunities table | `docs/specs/007-opportunities-table.md` | Show new opportunities and action affordances in the PWA. |

## Next

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 8 | not-started | Scheduler | `docs/specs/008-scheduler.md` | Add configurable automatic execution after manual flow is stable. |

## Later

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 9 | not-started | Notifications | `docs/spec.md` | PWA push, Telegram, webhook, Discord, or email after web monitoring works. |
| 10 | not-started | Production deployment hardening | `docs/deployment.md` | Traefik and Cloudflare deployment details. |

## Future Authenticated Actions

| Order | Status | Item | Spec | Notes |
| --- | --- | --- | --- | --- |
| 11 | not-started | Authenticated actions | `docs/specs/009-authenticated-actions.md` | Favorites, checkout discovery, pre-purchase, and explicit purchase. |

## Roadmap Rules

- Do not skip ahead unless the user explicitly changes priority.
- If an item changes scope, update its existing spec instead of creating a parallel document.
- If a new item is needed, add it here and create a spec only if no existing document owns it.
- Mark an item `done` only when its acceptance criteria and verification steps are satisfied.
- Keep `docs/spec.md` as the product-level summary and `docs/specs/` as feature-level specs.
