# 005 Fast Detection, Deduplication, and Opportunities

## Goal

Detect newly uploaded items as fast as possible, enrich only promising new candidates with detail data, and create opportunities without duplicates.

## Scope

- Use Vinted's public catalog JSON API as the fast catalog path.
- Bootstrap and refresh anonymous public cookies/tokens when the catalog API needs them.
- Do not use catalog HTML parsing as a normal fallback for fast runs.
- Request `newest_first`, `page=1`, and a small configurable `per_page` window, default `5`.
- Maintain a process-local cache of recently seen `vinted_item_id` values per source for immediate skips.
- Track item visibility per source.
- Use `vinted_item_id` as global item identity.
- Use `source_seen_items` to know if a source has seen an item before.
- Fetch item detail only for candidates that are not known as seen for that source.
- Extract detail fields needed for second-stage filtering and opportunity display: description, semantic color, category, shipping price, buyer protection fee, total price, full photo set, seller rating, seller badges, and item availability flags when visible.
- Create opportunities only for newly seen items that pass the currently available filters.
- Prevent duplicate opportunities for the same source, item, and rule.

## Out of Scope

- Notification delivery.
- Scheduler.
- Authenticated actions.
- HTML catalog fallback in the fast path.
- Checkout, pickup point selection, payment methods, or authenticated purchase actions.
- Full filter-management UI.

## Interfaces

- Provider:
  - fast catalog request via `/api/v2/catalog/items`;
  - anonymous session bootstrap/refresh;
  - item detail fetch by item URL.
- In-memory runtime:
  - per-source seen-ID cache.
- Database:
  - `source_seen_items`;
  - `opportunities`;
  - `items`;
  - `filter_rules`.

## Acceptance Criteria

- Catalog fetch uses the JSON API in the fast path.
- If the JSON API fails with auth/session errors, the provider refreshes anonymous public session state and retries once.
- If the retry fails, the run is marked failed and the app/worker keeps running.
- HTML catalog parsing is not used as a fallback for a failed fast run.
- Cached IDs are skipped before database lookup.
- IDs not in cache are checked in one batch against source-specific seen state.
- First time an item appears for a source, it is considered new.
- Re-running the same source with the same top items does not fetch detail or create duplicate opportunities.
- The same item can be new for a different source.
- Seen records keep first and last seen run references.
- Opportunity creation is idempotent.
- Details are fetched only for new source candidates that survive cheap catalog filters.
- Detail failures are recorded without crashing the service.

## Verification

- Run the fast provider and confirm it calls the catalog JSON API.
- Simulate expired anonymous session and confirm one bootstrap-and-retry.
- Simulate retry failure and confirm a failed run plus error row.
- Run with repeated cached IDs and confirm no detail fetch.
- Run the same fixture twice and confirm no duplicate opportunity.
- Run the same item under two sources and confirm source-specific seen tracking.
- Confirm database uniqueness protects against duplicate opportunities.
- Confirm `items_found`, `items_new`, and `opportunities_created` reflect catalog results, source-new items, and inserted opportunities.

## Audit

- Confirm the fast path has no hidden HTML catalog fallback.
- Confirm a failed source run does not stop API, PWA, worker, or other sources.
- Confirm no cookies, tokens, checkout payloads, addresses, payment data, or pickup point data are persisted.
- Confirm detail fetches are bounded by configurable limits and concurrency.
