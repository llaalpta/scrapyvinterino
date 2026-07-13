# 001 Search Sources

## Goal

Allow the user to configure Vinted catalog search URLs from the private app and persist them as reusable opportunity monitors.

## Scope

- Create an opportunity monitor with a display name and original Vinted catalog URL.
- List configured monitors through API and PWA.
- Store a normalized representation of query parameters.
- Keep new monitors inactive until the user launches them.
- Treat active/inactive as monitor scheduling state; executing/running belongs to individual runs.
- Validate that the URL is an anonymous public Vinted catalog URL before saving it.
- Archive monitors from the PWA as the safe delete behavior while preserving historical runs, events, items, and opportunities.
- Allow the API to change monitor name/URL and both API/PWA to change filters, cadence, and execution mode without creating a new monitor identity.

## Out of Scope

- Launching/stopping monitors and executing searches; those contracts belong to specs 003 and 008.
- Validating that Vinted returns results.
- Scheduler settings beyond monitor cadence/mode.
- Authenticated Vinted sessions.

## Interfaces

- API:
  - `GET /api/monitors`
  - `POST /api/monitors`
  - `PATCH /api/monitors/{monitor_id}`
  - `DELETE /api/monitors/{monitor_id}`
- PWA:
  - monitor creation form;
  - monitor count and visible monitor table with a selected monitor detail panel;
  - editing of filters, cadence, window/duration, and execution mode while `is_active=false`;
  - archive/delete action with confirmation.
- Database:
  - `search_sources`.

## URL Rules

- Accepted scheme: `https`.
- Accepted hosts for the MVP: `www.vinted.es` and `vinted.es`.
- Accepted path: `/catalog` or `/catalog/`.
- Surrounding whitespace is stripped before saving; the remaining URL string is preserved as entered.
- Query parameters are parsed with blank values preserved and stored by sorted key.
- Only catalog URL filters that can be translated to the fast catalog API are accepted.
- Supported product filters are `search_text`, `catalog[]`, `brand_ids[]`, `size_ids[]`, `status_ids[]`, `price_from`, `price_to`, and `currency`.
- `page`, `time`, and `order` are accepted but ignored by execution because runs force page `1` and `newest_first`.
- Empty `search_by_image_uuid` and `search_by_image_id` query parameters are accepted and ignored because Chrome catalog navigations can include them as blank placeholders.
- Non-empty `search_by_image_uuid` or `search_by_image_id` values are rejected because image-search filters are not translated to the fast catalog API.
- Any other query parameter is rejected with a clear validation error before saving.
- URL validation must not call Vinted and must not trigger scraping.

## Current Command Boundaries

- `POST /api/monitors` validates locally, commits one inactive/manual row, and only then derives baseline fields from Redis for the response. Redis unavailability is represented as `baseline_ready=false`.
- `PATCH /api/monitors/{monitor_id}` locks one non-archived row with `is_active=false` and keeps its ID. This gate does not exclude a simultaneous manual run; that race belongs to roadmap item 14.25. The PWA currently sends execution/filter configuration; name/URL editing is API-only until 14.26.
- Payloads rejected with `422`, active updates rejected with `409`, and missing/archived updates rejected with `404` do not mutate PostgreSQL. Names beyond the database limit currently fail with `500` without a row and are tracked in 14.29.
- URL and blacklist participate in the baseline policy hash. Changing either keeps the monitor identity and may require calibration unless a baseline for the resulting hash still exists; Redis read failure is not distinguishable from baseline absence in the current response.
- `DELETE /api/monitors/{monitor_id}` is a soft archive. The first successful call returns `204`; repeating it is idempotent and also returns `204`. Default listing omits the row.
- Archiving makes PostgreSQL inactive, removes future deadlines, closes the open monitor session and purges encrypted context from owned Vinted sessions. It may inspect/cancel Redis queue state, so the no-Redis-residue assertion applies to a newly created QA monitor, not to every archive.
- A task already reserved/executing, stale `monitor_started_at`, and both directions of Redis/SQL split-brain during archive are known open ownership gaps tracked in roadmap items 14.30 and 14.31.

## Acceptance Criteria

- A valid Vinted catalog URL can be saved with a name.
- Saved monitors are visible after refresh.
- Multiple saved monitors are shown in one compact selectable table with active monitors first, status chips/styles per row, and one selected monitor detail visible at a time.
- The monitor detail updates when a different monitor row is selected.
- The selected monitor detail shows name, URL, session state when available, configuration editable while `is_active=false`, performance chart, and active logs in that order.
- Archived monitors are hidden from the default monitor list and cannot be scheduled or launched.
- Archiving a monitor prevents new scheduler admission, closes its open monitor session, and preserves historical rows for audit and result traceability.
- Archiving invalidates every prepared Vinted session owned by the monitor and purges its encrypted cookie/token payload while preserving safe session metadata.
- Archiving from the PWA uses an in-app confirmation dialog, not a browser alert.
- Repeating archive is idempotent; editing an archived monitor returns not found.
- Changing a monitor name or URL through the API keeps the same monitor identity and historical results.
- The original URL is preserved unchanged except for surrounding whitespace trimming.
- Query parameters are stored in normalized JSON.
- Invalid URL input is rejected by the API.
- URLs with unsupported catalog filters are rejected by the API and do not create or update monitors.
- Monitor details show whether saved URL filters are compatible with the fast catalog API.
- Creating or editing a monitor does not trigger scraping, a run, a monitor/Vinted session, an event, or an opportunity.

## Verification

- Backend tests for URL validation, API create/list, invalid input, and database persistence.
- PWA build check.
- Live PWA Playwright check against the configured development origin (normally `http://localhost:5173`; use `127.0.0.1` only when CORS is configured for it):
  - navigation targets exist;
  - future buttons are disabled;
- Playwright checks:
  - valid monitor form submission creates a visible monitor;
  - invalid monitor form submission shows an error and does not persist;
  - selecting a compact monitor table row updates the detail panel;
  - scheduler-inactive monitor detail saves execution/filter configuration on the same ID;
  - scheduler-inactive monitor detail exposes an archive confirmation dialog;
  - mobile monitor layout keeps the table above the detail without horizontal overflow and scrolls the selected detail into view.
- Confirm the `search_sources` row contains trimmed `name`/`url`, normalized query data, `is_active=false`, and `monitor_mode=manual`.
- Confirm a valid API name/URL PATCH keeps the ID and that invalid UI create/API PATCH requests leave the row hash/count unchanged.
- Confirm URLs with unsupported filters are rejected without mutating persisted monitors.
- Confirm archive returns `204`, hides the monitor, retains the row with `archived_at`, rejects later PATCH with `404`, and returns `204` when repeated.
- For a fresh QA monitor, confirm zero related runs, monitor/Vinted sessions, events/outbox/publications, opportunities, task keys, and seen keys before deleting its complete QA graph.
- Keep worker and watchdog stopped, do not click execution/session/detail actions, and restore service, heartbeat, PostgreSQL sequence, and Redis state.

## Audit

- Navigation to `Monitores` must land on the full monitor management flow, including form and list.
- Visible monitor controls must either work or be clearly disabled.
- Creating a monitor must be observable through the PWA, API, and database.
- Authenticated future actions such as favorites and purchases must not appear enabled as part of this spec.
