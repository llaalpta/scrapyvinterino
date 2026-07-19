# 001 Search Sources

Since 14.34.3, `is_active` represents admission for a manual or recurring monitor session, not proof that no run is draining. Every start owns its internal baseline, and configuration remains locked during either a non-terminal baseline or stop drain. Baseline readiness and drain are derived behavior, not public source fields or commands.

## Goal

Allow the user to configure Vinted catalog search URLs from the private app and persist them as reusable opportunity monitors.

## Scope

- Create an opportunity monitor with a display name and original Vinted catalog URL.
- List configured monitors through API and PWA.
- Store a normalized representation of query parameters.
- Keep new monitors inactive until the user launches them.
- Treat active/inactive as monitor-session state; executing/running still belongs to individual runs.
- Validate that the URL is an anonymous public Vinted catalog URL before saving it.
- Archive monitors from the PWA as the safe delete behavior while preserving historical runs, events, items, and opportunities.
- Allow the API and PWA to change monitor name/URL, filters, cadence, and execution mode without creating a new monitor identity.

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
  - action-first selected detail with an explicit edit mode for name, original catalog URL, filters, cadence, window/duration, and execution mode while stopped and without a local command/run in progress;
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
- The PWA compatibility summary separates product filters applied from the URL, the effective application-controlled `order=newest_first` and `page=1`, URL parameters with no runtime effect, and unsupported parameters that block execution.

## Identity Rules

- The saved display name is trimmed, must contain at least one non-whitespace character and must not exceed the PostgreSQL `varchar(160)` limit after trimming. Create and PATCH apply the same validation before mutation.
- The original catalog URL follows the local URL rules above. A valid edit trims only surrounding whitespace, recomputes `normalized_query` and keeps the monitor ID and all historical ownership unchanged.
- The PWA edits name and URL in the existing stopped-monitor configuration draft and saves them in the same PATCH as the other changed configuration. A rejected name or URL remains visible for correction while the persisted source is unchanged.

### 14.47 compact selected-monitor detail

Status: `done` on `feat/14-47-compact-monitor-detail`.

1. The normal selected-monitor detail has one name/status heading, an `Abrir catalogo` link, the effective URL-filter summary and only the actions valid for the current monitor state before accumulated/session performance. It does not repeat the raw URL or render the edit form.
2. `Modificar` is available only while stopped and idle. It replaces normal actions with `Guardar`/`Cancelar`; invalid save remains editable, cancel restores persisted values, and changing monitor or section with a dirty draft requires an in-app discard confirmation.
3. Proxy-bound anonymous Vinted state is labelled `Contextos HTTP preparados`, remains separate from monitor-session metrics and is collapsed with accumulated logs. Desktop and mobile retain accessible actions and no horizontal overflow.

Representative integration: extend the isolated authenticated monitor-identity PWA/API/PostgreSQL scenario with a second inactive source and one locally seeded diagnostic HTTP context. Prove valid same-ID save, mutation-free invalid save, cancel/discard across monitor and section navigation, active-state action/edit boundaries, DOM order and collapsed diagnosis on desktop and mobile. Worker and watchdog stay stopped, every browser destination is loopback-only, `Abrir catalogo` is not followed, and the complete QA graph is removed.

Verification passed `8` focused cases plus `1` live authenticated Playwright case against a migrated isolated PostgreSQL database, API and strict Vite. It proved valid same-ID persistence, visible mutation-free rejection, cancel/discard decisions across monitor and section navigation, active/manual action boundaries, unique closed diagnosis after selection changes and mobile no-overflow. Browser traffic was loopback-only, the diagnostic context and full QA graph were removed, operational PostgreSQL/Redis fingerprints stayed unchanged, and Ruff plus frontend lint/build passed.

### 14.26 task contract

Status: `done` on `feature/pwa-monitor-identity-editing`.

1. A stopped, idle monitor can save a valid name and catalog URL from its selected PWA detail; the response, table/detail and PostgreSQL row keep the same ID and expose the recomputed normalized query.
2. A trimmed name longer than 160 characters or an invalid/unsupported catalog URL is rejected with `422`, produces a visible PWA error and leaves the complete persisted source unchanged.
3. Identity fields and save remain unavailable while active, draining, running or awaiting the initial runs read; the existing API gate returns `409` for active/non-terminal work.

Representative integration: use one isolated migrated PostgreSQL database, authenticated live API and Vite/Playwright PWA. Edit one inactive monitor successfully, inspect the same row through API and PostgreSQL, then submit an over-limit name and prove the visible error plus byte-for-byte identity/config persistence. Seed an active state locally to confirm the fields are disabled without starting a session or provider.

Worker and watchdog stay stopped. Redis is only fingerprinted by the isolated runner, every browser request is loopback-only, no Vinted/proxy/Telegram request is allowed, and the QA user/source rows, database, Redis lease, processes and logs are removed. This task adds no schema, run behavior, archive hardening or cross-process identity versioning.

Verification passed `8` focused cases plus `1` live Playwright case on an isolated migrated PostgreSQL database, authenticated API and Vite PWA. The live case proved same-ID edit/UI/API/database consistency, a visible over-limit rejection with the draft preserved and PostgreSQL unchanged, and disabled identity fields after a local active-state transition. Ruff, PWA lint/build and Compose rendering passed; worker/watchdog stayed stopped, all traffic was loopback-only, operational PostgreSQL/Redis fingerprints were unchanged and no QA process/log/data residue remained.

### 14.27 task contract

Status: `done` on `fix/pwa-monitor-command-state`.

1. The PWA admits at most one monitor mutation command at a time across create, configuration, session/run, diagnostic and archive actions. The gate is immediate rather than render-dependent, every monitor mutation control reflects it, and a rapid repeated submit produces one HTTP mutation and one PostgreSQL result.
2. After `POST /api/monitors` returns `201` or `DELETE /api/monitors/{id}` returns `204`, that committed outcome is applied locally before optional derived reads. A failed stats/runtime refresh reports the confirmed command plus incomplete refresh instead of claiming that create/archive failed; form/source state remains committed and a reload converges with API/PostgreSQL.
3. A confirmed archive removes every controller/view cache, loading marker, pending command marker and request generation keyed by the archived monitor.

Representative integration: use one isolated migrated PostgreSQL database, authenticated live API and Vite/Playwright PWA. Rapidly submit one monitor creation while locally aborting its first derived stats request, prove one `POST`, one row, cleared create fields, visible committed monitor and an honest refresh warning, then reload successfully. Load its monitor detail, archive it while locally aborting one runtime refresh, prove one `DELETE`, immediate absence, an honest refresh warning and absent API/default-list state after reload.

Worker and watchdog stay stopped. Every browser request is loopback-only; no Vinted, proxy or Telegram request is allowed. The QA user, sessions and complete source graph are removed, isolated PostgreSQL/Redis ownership is released and temporary API/Vite processes and logs are deleted. This task does not split the initial dashboard bootstrap, add cross-tab/API serialization, change server command contracts or redesign per-surface errors; those are separate or conditional outcomes.

Verification passed one live Playwright case against an authenticated API, migrated isolated PostgreSQL database and strict Vite PWA. The test held only delivery of real `201`/`204` responses to React, proved two synchronous submits emitted one `POST` and one row while every monitor mutation control was disabled, then aborted one derived stats read and one runtime read locally. It also held a real source-list snapshot obtained before archive, released it after the `204` and proved the monotonic generation discarded it instead of resurrecting the monitor. The committed monitor remained visible/cleared after create, absent after archive, warnings named the incomplete refresh, and reload/API/PostgreSQL converged. Ruff, PWA lint/build, Compose rendering and the PowerShell runner parser passed; worker/watchdog stayed stopped, all traffic was loopback-only, operational fingerprints were unchanged and no QA process/log/data residue remained.

## Current Command Boundaries

- `POST /api/monitors` validates locally and commits one inactive/manual row. Source reads do not consult Redis or expose baseline readiness.
- `PATCH /api/monitors/{monitor_id}` locks one non-archived row, requires `is_active=false` and rejects any `running/finalizing` run before keeping the same ID. The same PostgreSQL gate covers an inactive session-start baseline and an inactive stop drain; the PWA derives those intervals from loaded runs and also blocks editing. The archive race remains conditional in 14.30.
- Payloads rejected with `422`, active or non-terminal updates rejected with `409`, and missing/archived updates rejected with `404` do not mutate PostgreSQL. The shared name validator owns the database length boundary before either create or PATCH can flush a row.
- URL and blacklist participate in the internal baseline policy hash. Changing either keeps the monitor identity; the next manual or recurring start seeds the resulting hash before activation.
- `DELETE /api/monitors/{monitor_id}` is a soft archive. The first successful call returns `204`; repeating it is idempotent and also returns `204`. Default listing omits the row.
- Archiving makes PostgreSQL inactive, removes future deadlines, closes the open monitor session and purges encrypted context from owned Vinted sessions. It may inspect/cancel Redis queue state, so the no-Redis-residue assertion applies to a newly created QA monitor, not to every archive.
- A task already reserved/executing or a baseline started from another client remains a known archive gap. The local PWA blocks archive during its command; the operator rule is stop, wait for a terminal run and then archive. 14.30 is conditional on a normal-use reproduction; the former 14.31 exactly-once Redis/SQL convergence project is not part of the personal MVP.

## Acceptance Criteria

- A valid Vinted catalog URL can be saved with a name.
- Saved monitors are visible after refresh.
- Multiple saved monitors are shown in one compact selectable table with active monitors first, status chips/styles per row, and one selected monitor detail visible at a time.
- The monitor detail updates when a different monitor row is selected.
- The selected monitor detail shows one compact persisted identity/filter summary and state-valid actions first. Its configuration form exists only in explicit stopped/idle edit mode; accumulated/session performance follows, then collapsed HTTP-context diagnosis and accumulated logs.
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
