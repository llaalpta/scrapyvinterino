# 007 Results and Opportunities Browser

## Goal

Show scraped results and future opportunities in the private PWA with paginated backend queries, source traceability, date/price/source filters, and mobile-friendly rendering.

## Scope

- Display scraped item rows in a desktop table.
- Display scraped item rows as mobile cards/list items instead of a table.
- Paginate result queries server-side; do not rely on infinite scroll.
- Filter results by scrape date/time range, price range, and scrape source.
- Show the source that last scraped the item and the scrape date/time.
- If a source filter is active, show the scrape date/time for that source.
- Keep an Opportunities tab as a separate, honest empty/paginated view until local filters create opportunities.
- Show image, title, brand, size, status, price, favorites, seller, country, source, and detection time when available.
- Provide action affordances:
  - view item;
  - favorite;
  - prepare purchase;
  - purchase.
- Keep future authenticated actions disabled or clearly unavailable until implemented.

## Out of Scope

- Real favorite or purchase execution.
- Push notifications.
- Advanced table customization.
- Creating opportunities from filter rules; owned by Spec 006.

## Interfaces

- API:
  - paginated and filterable item results;
  - paginated opportunities endpoint.
- PWA:
  - tabbed Results, Opportunities, Sources, Filters, Runs, and Settings views.
- Database:
  - `opportunities`;
  - `items`.
  - `source_seen_items`;
  - `search_sources`.

## Acceptance Criteria

- Scraped items appear in a paginated Results table on desktop.
- Scraped items appear as cards/list items on mobile widths.
- No infinite-scroll behavior is needed to inspect results.
- Result rows include source name and scrape date/time.
- Date/time, price, and source filters update the backend query.
- Pagination controls request new pages from the backend.
- Results can be filtered to a specific source without duplicating globally deduped items.
- If an item was seen by multiple sources, the unfiltered row shows the most recent source that saw it.
- The Opportunities tab is present and truthful when there are no opportunities.
- The Vinted item can be opened from the `view` action.
- Future actions are visible but disabled or feature-flagged.
- Empty state is clear.
- Table remains usable on desktop widths and is replaced by cards on mobile widths.

## Verification

- Backend tests for pagination defaults and limits.
- Backend tests for source, scrape date range, and price filters.
- Backend tests for multi-source items: latest source globally and filtered source semantics.
- Backend tests for invalid filter ranges.
- Backend tests for paginated opportunities empty and seeded data.
- Frontend build.
- Playwright check against the running app for tabs, filters, pagination, desktop table, mobile cards, and disabled future actions.
