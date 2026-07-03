# 006 Session Exclusion Filters and Opportunities

## Goal

Create session-scoped opportunities from public Vinted search sources while applying application-owned exclusion filters on item detail data when available.

## Scope

- Introduce monitor sessions as the operational context for a search source, filter snapshot, cadence snapshot, optional proxy, and runtime metadata.
- Treat Vinted catalog URLs as the primary positive search filter.
- Treat local filters as exclusion filters that discard unwanted candidates, usually by blacklist terms in item detail text.
- Fetch or reuse item detail for candidates that have not already been evaluated in the same session with the same filter hash.
- Create opportunities for session candidates unless a configured exclusion filter discards them.
- Create opportunities even when no local filters are configured, marked as `passed_without_filters`.
- Create opportunities even when detail cannot be fetched, marked as `passed_without_detail` or `detail_error`.
- Prevent duplicate opportunities for the same item within the same monitor session.
- Allow an item known globally from another source/session to become an opportunity in a later session if that session sees it.
- Store minimal per-session item state for speed and auditability.
- Show opportunity evaluation status in the PWA.

## Out of Scope

- Machine learning scoring.
- Image analysis.
- Authenticated seller or checkout data.
- Historical reprocessing when filters change. Changing filters requires stopping the current session and starting a new one.

## Interfaces

- API/PWA:
  - create and edit named exclusion filters;
  - start and stop monitor sessions;
  - assign filters, cadence, and proxy profile when launching a session;
  - show evaluation status on opportunities.
- Worker:
  - evaluate filters during session runs;
  - create session-scoped opportunities.
- Database:
  - `filter_rules`;
  - `monitor_sessions`;
  - `session_item_state`;
  - `opportunities`.

## Acceptance Criteria

- A monitor session snapshots its source, filters, proxy, cadence, and filter hash at launch time.
- Each session run fetches the configured fast catalog page, deduplicates candidates by `vinted_item_id`, and preserves global item dedupe.
- An item already evaluated in the same session with the same filter hash is skipped without detail fetch or filter evaluation.
- An item already known globally can still create an opportunity in a different session.
- Blacklist matching is case-insensitive and accent-tolerant across title, description, brand, size, status, color, category, and seller when available.
- If blacklist terms match, the item is marked discarded and no opportunity is created.
- If no filters are configured, an opportunity is created with evaluation status `passed_without_filters`.
- If filters are configured but detail is unavailable, an opportunity is created with `passed_without_detail` or `detail_error`.
- Opportunity creation is idempotent for `session_id + item_id`.
- `opportunities_created`, `items_filter_passed`, `items_discarded_by_filters`, and `items_filter_pending` reflect the session run.

## Verification

- Unit tests for blacklist matching and accent/case normalization.
- Run test with no filters creating `passed_without_filters`.
- Run test with detail blacklist discarding an item.
- Run test with detail failure creating `detail_error` opportunity.
- Run the same item twice in the same session and confirm no duplicate opportunity or repeated filter work.
- Run an existing global item in a new session and confirm a new session opportunity can be created.
- Frontend Playwright check for sessions, opportunities status badges, and monitor counters.
