# 006 Monitor Exclusion Filters and Opportunities

## Goal

Create monitor-scoped opportunities from public Vinted monitors while applying optional application-owned exclusion filters on item detail data when available.

## Scope

- Treat each configured monitor as the operational context for URL, cadence, optional filters, and runtime metadata.
- Treat Vinted catalog URLs as the primary positive search filter for the monitor.
- Treat local filters as exclusion filters that discard unwanted candidates, usually by blacklist terms in item detail text.
- Fetch item detail for Redis-new monitor candidates when filters require detail data.
- Create opportunities for monitor candidates unless a configured exclusion filter discards them.
- Create opportunities even when no local filters are configured, marked as `passed_without_filters`.
- Create opportunities even when detail cannot be fetched, marked as `passed_without_detail` or `detail_error`.
- Prevent duplicate opportunities for the same item within the same monitor.
- Allow an item known from another monitor to become an opportunity if this monitor sees it.
- Store per-monitor seen state in Redis for speed. Persist only candidates that become opportunities.
- Show opportunity evaluation status in the PWA.

## Out of Scope

- Machine learning scoring.
- Image analysis.
- Authenticated seller or checkout data.
- Persisting discarded candidates for later inspection.

## Interfaces

- API/PWA:
  - create and edit named exclusion filters;
  - start, stop, and retry monitors;
  - assign filters and cadence on the monitor;
  - show evaluation status on opportunities.
- Worker:
  - evaluate filters during monitor runs;
  - create monitor-scoped opportunities.
- Database:
  - `filter_rules`;
  - `opportunities`.

## Acceptance Criteria

- A monitor stores its filters, cadence, and execution mode directly; outbound proxy selection is global scheduler behavior.
- Each monitor run fetches the configured fast catalog page and deduplicates candidates by `vinted_item_id`.
- An item already seen by the same monitor/policy in Redis is skipped without detail, DB writes, or another opportunity.
- An item already known by another monitor can still create an opportunity in this monitor.
- Blacklist matching is case-insensitive and accent-tolerant across title, description, brand, size, status, color, category, and seller when available.
- If blacklist terms match, the item is marked discarded and no opportunity is created.
- Discarded items are not persisted as `items`; the run stores only aggregate discarded counters.
- If no filters are configured, an opportunity is created with evaluation status `passed_without_filters`.
- If filters are configured but detail is unavailable, an opportunity is created with `passed_without_detail` or `detail_error`.
- Opportunity creation is idempotent for monitor + item in the monitor flow.
- `opportunities_created`, `items_filter_passed`, `items_discarded_by_filters`, and `items_filter_pending` reflect the monitor run.

## Verification

- Unit tests for blacklist matching and accent/case normalization.
- Run test with no filters creating `passed_without_filters`.
- Run test with detail blacklist discarding an item.
- Run test with detail failure creating `detail_error` opportunity.
- Run the same item twice in the same monitor/policy and confirm no duplicate opportunity or repeated filter work.
- Run an existing global item in a different monitor and confirm a new monitor opportunity can be created.
- Run a matching blacklist fixture and confirm no `items` row is created for the discarded candidate.
- Frontend Playwright check for monitors, opportunities status badges, and activity counters.
