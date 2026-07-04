# 007 Opportunities Browser

## Goal

Show opportunity results in the private PWA with paginated backend queries, monitor traceability, date/price/monitor filters, and mobile-friendly rendering.

## Scope

- Display opportunity rows in a desktop table.
- Display opportunity rows as mobile cards/list items instead of a table.
- Paginate opportunity queries server-side; do not rely on infinite scroll.
- Filter opportunities by scrape date/time range, price range, monitor, and evaluation status.
- Keep opportunity filters collapsed by default; open them inline on desktop and as a drawer on mobile.
- Reuse the same filter button to open and close the filter panel.
- Allow active filter chips to remove individual filters.
- Keep page-size selection in the pagination controls, not in the product filters.
- Show the monitor that generated the opportunity and the scrape date/time.
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
- Browsing all seen or discarded catalog candidates.

## Interfaces

- API:
  - paginated opportunities endpoint.
- PWA:
  - tabbed Opportunities, Monitors, Filters, and Settings views;
  - monitor activity is shown inside the Monitors view.
  - collapsible dashboard navigation on desktop;
  - sticky horizontal dashboard navigation on mobile.
- Database:
  - `opportunities`;
  - `items`.
  - `search_sources`.

## Acceptance Criteria

- Opportunities appear in a paginated table on desktop.
- Opportunities appear as cards/list items on mobile widths.
- No infinite-scroll behavior is needed to inspect results.
- Opportunity rows include monitor name and scrape date/time.
- Dashboard navigation exposes Opportunities, Monitors, Filters, and Settings; Activity is not a separate top-level view.
- Date/time, price, monitor, and status filters update the backend query.
- Active filter chips can clear individual filters without opening the panel.
- Pagination controls request new pages from the backend.
- Page-size controls request page 1 with the selected number of results per page.
- There is no Results tab for all seen items.
- The Opportunities tab is the primary result surface and is truthful when empty.
- The Vinted item can be opened from the `view` action.
- Future actions are visible but disabled or feature-flagged.
- Empty state is clear.
- Table remains usable on desktop widths and is replaced by cards on mobile widths.
- The page itself does not scroll horizontally; horizontal overflow is limited to the desktop table container.
- Desktop navigation can collapse to a compact icon rail without losing access to any section.
- Mobile navigation remains visible at the top while scrolling and keeps a stable height across sections.
- Monitor activity is reachable from the Monitors view without a separate Activity tab.

## Verification

- Backend tests for pagination defaults and limits.
- Backend tests for monitor, status, scrape date range, and price filters.
- Backend tests for invalid filter ranges.
- Backend tests for paginated opportunities empty and seeded data.
- Frontend build.
- Playwright check against the running app for tabs, collapsible desktop navigation, sticky mobile navigation, collapsed filters, mobile filter drawer, pagination page size, desktop table, mobile cards, no page-level horizontal scroll, and disabled future actions.
