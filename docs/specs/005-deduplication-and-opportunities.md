# 005 Fast Detection, Redis Seen Tracking, and Detail Enrichment

## Goal

Detect public Vinted items as fast as possible, use Redis to decide whether each monitor already processed an item, and prevent duplicate work and alerts within the same monitor.

## Scope

- Use Vinted's public catalog JSON API as the fast catalog path.
- Bootstrap and refresh anonymous public cookies/tokens when the catalog API needs them.
- Do not use catalog HTML parsing as a normal fallback for fast runs.
- Request `newest_first`, `page=1`, and a small configurable `per_page` window, default `5`.
- Force `newest_first` for the fast API request even if the saved catalog URL has another `order`.
- Require Redis before a monitor processes candidates. If Redis is unavailable, fail the run and stop/block that monitor execution.
- Use Redis seen keys scoped by monitor and evaluation policy hash as the source of truth for whether an item should be processed.
- Use short-lived Redis processing locks to avoid concurrent duplicate work for the same monitor/item.
- Use `items.vinted_item_id` as normalized catalog identity only for items that become opportunities.
- Count `items_new` as candidates newly claimed by Redis for that monitor/policy in that run.
- Fetch item detail only for Redis-new candidates that need it for filtering, except controlled retries for missing or failed details.
- Extract detail fields needed for second-stage filtering and opportunity display: description, semantic color, category, shipping price, buyer protection fee, total price, full photo set, seller rating, seller badges, and item availability flags when visible.
- Leave opportunity creation behavior to local filter evaluation in spec 006.

## Out of Scope

- Notification delivery.
- Scheduler.
- Authenticated actions.
- HTML catalog fallback in the fast path.
- Checkout, pickup point selection, payment methods, or authenticated purchase actions.
- Reusable/global filter-management UI.
- Persisting non-opportunity catalog candidates.

## Interfaces

- Provider:
  - fast catalog request via `/api/v2/catalog/items`;
  - anonymous session bootstrap/refresh;
  - item detail fetch by item URL.
- Redis runtime:
  - required seen cache by monitor, policy hash, and `vinted_item_id`;
  - required processing lock by monitor, policy hash, and `vinted_item_id`;
  - configurable TTL and per-monitor cap.
- Database:
  - `items` for opportunity items only;
  - detail fields on `items`.

## Acceptance Criteria

- Catalog fetch uses the JSON API in the fast path.
- If the JSON API fails with auth/session errors, the provider refreshes anonymous public session state and retries once.
- If the retry fails, the run is marked failed and the app/worker keeps running.
- HTML catalog parsing is not used as a fallback for a failed fast run.
- Redis availability is checked before candidate processing; unavailable Redis marks the run failed and no detail/opportunity work happens.
- Item catalog identity is checked idempotently against Redis seen state before detail/filter work.
- First time an item appears in a monitor/policy, it is considered new for that monitor.
- Re-running the same monitor with the same top items does not create another opportunity.
- The same item appearing under another monitor can be considered new for that other monitor.
- Changing the monitor URL or monitor-owned filter definition changes the policy hash and can reevaluate visible items.
- Non-opportunity candidates are not persisted as `items`.
- Details are fetched only for monitor-new candidates that need detail and are bounded by the configured per-run limit.
- Detail failures are recorded without crashing the service.
- Redis seen state is marked after each candidate reaches a terminal outcome.
- A processing lock expiring allows retry instead of permanently losing a candidate.

## Verification

- Run the fast provider and confirm it calls the catalog JSON API.
- Simulate expired anonymous session and confirm one bootstrap-and-retry.
- Simulate retry failure and confirm a failed run plus error row.
- Run with repeated seen Redis IDs and confirm no detail fetch.
- Run the same fixture twice and confirm no duplicate opportunity or repeated filter work.
- Run the same item under two monitors and confirm each monitor can count the item once without duplicating alerts inside either monitor.
- Confirm `items_found`, `items_new`, and `opportunities_created` reflect catalog results, monitor-new items, and created opportunities.
- Confirm Redis unavailable fails the run and does not create opportunities.
- Confirm discarded candidates are not inserted into `items`.

## Audit

- Confirm the fast path has no hidden HTML catalog fallback.
- Confirm a failed source run does not stop API, PWA, worker, or other sources.
- Confirm no cookies, tokens, checkout payloads, addresses, payment data, or pickup point data are persisted.
- Confirm detail fetches are bounded by configurable limits and concurrency.
- Confirm overlapping monitors cannot duplicate alerts within one monitor but can independently alert on the same catalog item.
