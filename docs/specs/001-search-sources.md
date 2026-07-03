# 001 Search Sources

## Goal

Allow the user to configure Vinted catalog search URLs from the private app and persist them as reusable monitoring sources.

## Scope

- Create a search source with a display name and original Vinted catalog URL.
- List configured sources through API and PWA.
- Store a normalized representation of query parameters.
- Keep sources active by default.
- Validate that the URL is an anonymous public Vinted catalog URL before saving it.
- Archive sources from the PWA as the safe delete behavior while preserving historical runs, seen items, sessions, and opportunities.

## Out of Scope

- Executing searches.
- Validating that Vinted returns results.
- Scheduler settings beyond storing existing placeholder config.
- Authenticated Vinted sessions.

## Interfaces

- API:
  - `GET /api/sources`
  - `POST /api/sources`
  - `DELETE /api/sources/{source_id}`
- PWA:
  - source creation form;
  - source count and visible source list;
  - archive/delete action with confirmation.
- Database:
  - `search_sources`.

## URL Rules

- Accepted schemes: `http` and `https`.
- Accepted hosts for the MVP: `www.vinted.es` and `vinted.es`.
- Accepted path: `/catalog` or `/catalog/`.
- Surrounding whitespace is stripped before saving; the remaining URL string is preserved as entered.
- Query parameters are parsed with blank values preserved and stored by sorted key.
- URL validation must not call Vinted and must not trigger scraping.

## Acceptance Criteria

- A valid Vinted catalog URL can be saved with a name.
- Saved sources are visible after refresh.
- Archived sources are hidden from the default source list and cannot be scheduled or launched.
- Archiving a source pauses it and stops any active monitor session for that source.
- Archiving a source preserves historical rows for audit and result traceability.
- The original URL is preserved unchanged except for surrounding whitespace trimming.
- Query parameters are stored in normalized JSON.
- Invalid URL input is rejected by the API.
- Creating a source does not trigger scraping.

## Verification

- Backend tests for URL validation, API create/list, invalid input, and database persistence.
- PWA build check.
- Live PWA Playwright check against `http://localhost:5173` or `http://127.0.0.1:5173`:
  - navigation targets exist;
  - future buttons are disabled;
  - valid source form submission creates a visible source;
  - invalid source form submission shows an error and does not persist.
- Confirm `search_sources` row includes `url` and `normalized_query`.
- Confirm archived sources keep historical runs and disappear from the default PWA source list.

## Audit

- Navigation to `Busquedas` must land on the full source management flow, including form and list.
- Visible source controls must either work or be clearly disabled.
- Creating a source must be observable through the PWA, API, and database.
- Future actions such as runs, favorites, and purchases must not appear enabled as part of this spec.
