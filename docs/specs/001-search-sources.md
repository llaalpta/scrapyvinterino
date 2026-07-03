# 001 Search Sources

## Goal

Allow the user to configure Vinted catalog search URLs from the private app and persist them as reusable opportunity monitors.

## Scope

- Create an opportunity monitor with a display name and original Vinted catalog URL.
- List configured monitors through API and PWA.
- Store a normalized representation of query parameters.
- Keep new monitors paused until the user launches them.
- Validate that the URL is an anonymous public Vinted catalog URL before saving it.
- Archive monitors from the PWA as the safe delete behavior while preserving historical runs, seen items, and opportunities.
- Allow monitor URL, filters, cadence, proxy, and execution mode to change without creating a new monitor identity.

## Out of Scope

- Executing searches.
- Validating that Vinted returns results.
- Scheduler settings beyond monitor cadence/mode.
- Authenticated Vinted sessions.

## Interfaces

- API:
  - `GET /api/monitors`
  - `POST /api/monitors`
  - `PATCH /api/monitors/{monitor_id}`
  - `POST /api/monitors/{monitor_id}/start`
  - `POST /api/monitors/{monitor_id}/stop`
  - `DELETE /api/monitors/{monitor_id}`
  - legacy `/api/sources` aliases during migration.
- PWA:
  - monitor creation form;
  - monitor count and visible monitor list;
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
- Saved monitors are visible after refresh.
- Archived monitors are hidden from the default monitor list and cannot be scheduled or launched.
- Archiving a monitor pauses it and preserves historical rows for audit and result traceability.
- Changing a monitor URL keeps the same monitor identity and historical results.
- The original URL is preserved unchanged except for surrounding whitespace trimming.
- Query parameters are stored in normalized JSON.
- Invalid URL input is rejected by the API.
- Creating a monitor does not trigger scraping.

## Verification

- Backend tests for URL validation, API create/list, invalid input, and database persistence.
- PWA build check.
- Live PWA Playwright check against `http://localhost:5173` or `http://127.0.0.1:5173`:
  - navigation targets exist;
  - future buttons are disabled;
- Playwright checks:
  - valid monitor form submission creates a visible monitor;
  - invalid monitor form submission shows an error and does not persist.
- Confirm `search_sources` row includes `url` and `normalized_query`.
- Confirm archived monitors keep historical runs and disappear from the default PWA monitor list.

## Audit

- Navigation to `Monitores` must land on the full monitor management flow, including form and list.
- Visible monitor controls must either work or be clearly disabled.
- Creating a monitor must be observable through the PWA, API, and database.
- Future actions such as runs, favorites, and purchases must not appear enabled as part of this spec.
