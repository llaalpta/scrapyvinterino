# 005 Fast Detection, Seen Tracking, and Detail Enrichment

## Goal

Detect public Vinted items as fast as possible, track which monitor saw each item, and prevent duplicate work and alerts within the same monitor.

## Scope

- Use Vinted's public catalog JSON API as the fast catalog path.
- Bootstrap and refresh anonymous public cookies/tokens when the catalog API needs them.
- Do not use catalog HTML parsing as a normal fallback for fast runs.
- Request `newest_first`, `page=1`, and a small configurable `per_page` window, default `5`.
- Force `newest_first` for the fast API request even if the saved catalog URL has another `order`.
- Maintain a process-local global item cache as catalog acceleration, not as alert dedupe.
- Maintain the first process-local per-source cache as the runtime base for scheduler traceability cache in spec 008.
- Treat process-local cache as an accelerator over committed state, never as a replacement for PostgreSQL monitor visibility.
- Track item visibility per monitor.
- Use `items.vinted_item_id` as normalized catalog identity only.
- Use `source_seen_items` as the source of truth for whether an item is new to that monitor.
- Count `items_new` as items first seen by the monitor in that run.
- Fetch item detail for monitor-new candidates that need it for filtering, except controlled retries for missing or failed details.
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
  - global known-ID cache, upgraded by spec 008 to `vinted_item_id -> item_id`;
  - per-source seen-ID cache, upgraded by spec 008 for scheduled traceability acceleration.
- Database:
  - `source_seen_items`;
  - `items`;
  - detail fields on `items`.

## Acceptance Criteria

- Catalog fetch uses the JSON API in the fast path.
- If the JSON API fails with auth/session errors, the provider refreshes anonymous public session state and retries once.
- If the retry fails, the run is marked failed and the app/worker keeps running.
- HTML catalog parsing is not used as a fallback for a failed fast run.
- Item catalog identity is checked idempotently against `items.vinted_item_id`.
- First time an item appears in a monitor, it is considered new for that monitor.
- Re-running the same monitor with the same top items does not create another opportunity.
- The same item appearing under another monitor can be considered new for that other monitor.
- Seen records keep first and last seen run references.
- No opportunities are created before local filters exist.
- Details are fetched only for monitor-new candidates that need detail and are bounded by the configured per-run limit.
- Detail failures are recorded without crashing the service.
- Caches are updated only after the run transaction commits.
- Cache misses or stale cache state cannot change `items_new`, source traceability, or detail-fetch correctness.
- Spec 008 must extend these caches before scheduler-triggered alerting work continues.

## Verification

- Run the fast provider and confirm it calls the catalog JSON API.
- Simulate expired anonymous session and confirm one bootstrap-and-retry.
- Simulate retry failure and confirm a failed run plus error row.
- Run with repeated cached IDs and confirm no detail fetch.
- Run the same fixture twice and confirm no duplicate source-seen records.
- Run the same item under two monitors and confirm each monitor can count the item once without duplicating alerts inside either monitor.
- Confirm `items_found`, `items_new`, and `opportunities_created` reflect catalog results, monitor-new items, and created opportunities.
- Confirm persistence failures do not update process-local caches.

## Audit

- Confirm the fast path has no hidden HTML catalog fallback.
- Confirm a failed source run does not stop API, PWA, worker, or other sources.
- Confirm no cookies, tokens, checkout payloads, addresses, payment data, or pickup point data are persisted.
- Confirm detail fetches are bounded by configurable limits and concurrency.
- Confirm overlapping monitors cannot duplicate alerts within one monitor but can independently alert on the same catalog item.
