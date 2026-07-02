# 005 Fast Detection, Seen Tracking, and Detail Enrichment

## Goal

Detect globally new public Vinted items as fast as possible, track which source saw each item, and enrich only globally new candidates with detail data for later filtering.

## Scope

- Use Vinted's public catalog JSON API as the fast catalog path.
- Bootstrap and refresh anonymous public cookies/tokens when the catalog API needs them.
- Do not use catalog HTML parsing as a normal fallback for fast runs.
- Request `newest_first`, `page=1`, and a small configurable `per_page` window, default `5`.
- Force `newest_first` for the fast API request even if the saved catalog URL has another `order`.
- Maintain a process-local cache of recently known global `vinted_item_id` values as a non-authoritative hint.
- Maintain a process-local per-source cache only as a non-authoritative optimization for source traceability.
- Never let process-local cache state skip committed item persistence or `source_seen_items` trace updates.
- Track item visibility per source.
- Use `items.vinted_item_id` as global item identity and the source of truth for whether an item is new.
- Use `source_seen_items` to record source traceability, not to decide whether an item is globally new.
- Count `items_new` as globally new `items` rows inserted by the run.
- Fetch item detail only for globally new candidates, except future controlled retries for missing or failed details.
- Extract detail fields needed for second-stage filtering and opportunity display: description, semantic color, category, shipping price, buyer protection fee, total price, full photo set, seller rating, seller badges, and item availability flags when visible.
- Leave opportunity creation to local filter evaluation in spec 006.
- Keep `opportunities_created` at `0` until local filters create notification-worthy opportunities.

## Out of Scope

- Notification delivery.
- Scheduler.
- Authenticated actions.
- HTML catalog fallback in the fast path.
- Checkout, pickup point selection, payment methods, or authenticated purchase actions.
- Full filter-management UI.
- Opportunity creation.

## Interfaces

- Provider:
  - fast catalog request via `/api/v2/catalog/items`;
  - anonymous session bootstrap/refresh;
  - item detail fetch by item URL.
- In-memory runtime:
  - global known-ID cache;
  - per-source seen-ID cache.
- Database:
  - `source_seen_items`;
  - `items`;
  - detail fields on `items`.

## Acceptance Criteria

- Catalog fetch uses the JSON API in the fast path.
- If the JSON API fails with auth/session errors, the provider refreshes anonymous public session state and retries once.
- If the retry fails, the run is marked failed and the app/worker keeps running.
- HTML catalog parsing is not used as a fallback for a failed fast run.
- Global item identity is checked idempotently against `items.vinted_item_id`.
- First time an item appears anywhere in the system, it is considered new.
- Re-running the same source with the same top items does not fetch detail.
- The same item appearing under another source is traced in `source_seen_items` but does not count as new.
- Seen records keep first and last seen run references.
- No opportunities are created before local filters exist.
- Details are fetched only for globally new candidates and are bounded by the configured per-run limit.
- Detail failures are recorded without crashing the service.
- Caches are updated only after the run transaction commits.
- Cache misses or stale cache state cannot change `items_new`, source traceability, or detail-fetch decisions.

## Verification

- Run the fast provider and confirm it calls the catalog JSON API.
- Simulate expired anonymous session and confirm one bootstrap-and-retry.
- Simulate retry failure and confirm a failed run plus error row.
- Run with repeated cached IDs and confirm no detail fetch.
- Run the same fixture twice and confirm no duplicate source-seen records.
- Run the same item under two sources and confirm the second run records source traceability without counting a new item or fetching detail.
- Confirm `items_found`, `items_new`, and `opportunities_created` reflect catalog results, globally new items, and `0` opportunities before filters.
- Confirm persistence failures do not update process-local caches.

## Audit

- Confirm the fast path has no hidden HTML catalog fallback.
- Confirm a failed source run does not stop API, PWA, worker, or other sources.
- Confirm no cookies, tokens, checkout payloads, addresses, payment data, or pickup point data are persisted.
- Confirm detail fetches are bounded by configurable limits and concurrency.
- Confirm overlapping sources cannot create duplicate detail fetches, candidates, or future notifications for the same global item.
