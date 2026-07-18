# 006 Monitor Exclusion Filters and Opportunities

## Goal

Create monitor-scoped opportunities from public Vinted monitors while applying optional monitor-owned exclusion terms on item detail data when available.

## Scope

- Treat each configured monitor as the operational context for URL, cadence, optional filter terms, and runtime metadata.
- Treat Vinted catalog URLs as the primary positive search filter for the monitor.
- Treat monitor-local terms as exclusion filters that discard unwanted candidates only when they occur in the public item description.
- Fetch item detail for every Redis-new monitor candidate before filter evaluation and opportunity creation.
- Create opportunities for monitor candidates unless a configured exclusion filter discards them.
- Create opportunities even when no local filters are configured, marked as `passed_without_filters`.
- Do not create opportunities when detail cannot be fetched or parsed. Retry an ordinary failure once after two seconds in the same run; if it still fails, record pending diagnostics and mark the candidate seen without deferred work.
- Create an opportunity when the required public detail is complete even if public availability is reserved, hidden, processing, closed, shipping-unavailable, or otherwise not currently buyable; persist the state and reason codes honestly.
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
  - edit exclusion terms inline in each monitor configuration;
  - start, stop, and retry monitors;
  - save filter terms and cadence on the monitor before launch;
  - show evaluation status on opportunities.
- Worker:
  - evaluate filters during monitor runs;
  - create monitor-scoped opportunities.
- Database:
  - `search_sources.filter_definition`;
  - `opportunities`.

## Acceptance Criteria

- A monitor stores its filter terms, cadence, and execution mode directly; outbound proxy selection is global scheduler behavior.
- Filter terms are not reusable across monitors and there is no separate filters view.
- Each monitor run fetches the configured fast catalog page and deduplicates candidates by `vinted_item_id`.
- An item already seen by the same monitor/policy in Redis is skipped without detail, DB writes, or another opportunity.
- An item already known by another monitor can still create an opportunity in this monitor.
- Blacklist matching is case-insensitive and accent-tolerant over the public description only.
- If blacklist terms match, the item is marked discarded and no opportunity is created.
- Discarded items are not persisted as `items`; the run stores only aggregate discarded counters.
- If no filters are configured, an opportunity is created with evaluation status `passed_without_filters`.
- If detail is unavailable or not parseable after the immediate retry, no item or opportunity is persisted, the run counts the candidate as pending, and Redis stores only terminal seen state.
- Public availability is informational and never authorizes an authenticated action. A future purchase must revalidate price, currency, availability, shipping, payment, and user confirmation against the authenticated session.
- Public availability never acts as an exclusion rule; non-buyable and unknown states may create opportunities and must be represented honestly.
- Opportunity creation is idempotent for monitor + item in the monitor flow.
- `items_found` is fixed before filter evaluation from the monitor-new, post-deduplication candidate set. A discarded or detail-pending candidate remains found but never becomes an opportunity.
- `opportunities_created`, `items_filter_passed`, `items_discarded_by_filters`, and `items_filter_pending` reflect the monitor run.
- The API does not accept legacy `filter_rule_ids` or monitor `is_active` patch payloads.

## Verification

- Unit tests for blacklist matching and accent/case normalization.
- Unit tests proving terms in title, brand, size, status, seller, color, category and badges do not discard when absent from the description.
- Run test with no filters creating `passed_without_filters`.
- Run test with detail blacklist discarding an item.
- Run test with two detail failures creating no item or opportunity, recording a pending outcome and marking the item seen without Redis retry state.
- Run the same item twice in the same monitor/policy and confirm no duplicate opportunity or repeated filter work.
- Run an existing global item in a different monitor and confirm a new monitor opportunity can be created.
- Run a matching blacklist fixture and confirm no `items` row is created for the discarded candidate.
- Frontend Playwright check for monitors, opportunities status badges, and activity counters.
- Frontend Playwright check that filter terms are edited inside monitor configuration and that no top-level Filters navigation exists.
