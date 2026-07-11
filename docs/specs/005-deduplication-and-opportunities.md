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
- Require an explicit initial catalog snapshot before manual, continuous, duration, window, or scheduler runs can process candidates.
- Use Redis seen keys scoped by monitor and evaluation policy hash as the source of truth for whether an item should be processed.
- Store the initial snapshot marker in Redis by monitor and evaluation policy hash with the same TTL as seen keys.
- Use short-lived Redis processing locks to avoid concurrent duplicate work for the same monitor/item.
- Use `items.vinted_item_id` as normalized catalog identity only for items that become opportunities.
- Count `items_new` as candidates newly claimed by Redis for that monitor/policy in that run.
- Fetch item detail for every Redis-new candidate before filter evaluation and opportunity creation, bounded by the configured per-run limit.
- Parse the public item document's JSON-LD and structurally discovered Next/React Flight records; do not depend on dynamic Flight record ids or the Cloudflare-challenged direct detail API.
- Extract detail fields needed for second-stage filtering and opportunity display: title, description, brand, size, physical status, base price/currency, semantic color, category, minimum displayed shipping price, buyer protection fee, total excluding shipping, the complete public photo set, seller rating/badges, and public availability signals when visible.
- Validate a configurable required-field allowlist before filter evaluation. The default clothing policy requires title, observed description (which may be empty), brand, size, physical status, base price/currency, and at least one photo.
- Persist signed Vinted CDN photo URLs only; image bytes are loaded directly by the PWA and never through the residential Vinted proxy.
- Keep recoverable detail failures in a bounded Redis retry queue so an item is not lost after leaving the top-five catalog window.
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
  - due-time detail retry entries containing only a sanitized catalog candidate, attempt count, failure kind, and next attempt time;
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
- A monitor without a valid initial snapshot is rejected before detail/filter/opportunity work and must be recalibrated explicitly.
- Recalibrating the initial snapshot fetches the current catalog window, marks visible IDs as seen, records a baseline run, and creates no opportunities.
- First time an item appears in a monitor/policy, it is considered new for that monitor.
- Re-running the same monitor with the same top items does not create another opportunity.
- The same item appearing under another monitor can be considered new for that other monitor.
- Changing the monitor URL or monitor-owned filter definition changes the policy hash and can reevaluate visible items.
- Changing the monitor URL or monitor-owned filter definition also requires a new explicit initial snapshot for the new policy hash.
- Non-opportunity candidates are not persisted as `items`.
- Details are fetched for monitor-new candidates before opportunity creation and are bounded by the configured per-run limit.
- Detail transport, challenge, response, or parser failures do not create opportunities and remain retryable for three total attempts; candidates skipped by the per-run detail budget are queued without consuming an attempt.
- Valid detail that lacks a configured required field is a terminal `detail_incomplete` outcome, names the missing fields, creates no opportunity, and is marked seen.
- Optional fields absent from a valid document remain null and do not block an opportunity. An observed empty description is valid and contributes no blacklist text.
- Redis seen state is marked only after a terminal outcome; pending retries retain their sanitized candidate even if the item leaves the catalog window.
- A processing lock expiring allows retry instead of permanently losing a candidate.
- Detail requests are sequential inside one Vinted session so response cookie rotation cannot race.

## Verification

- Run the fast provider and confirm it calls the catalog JSON API.
- Simulate expired anonymous session and confirm one bootstrap-and-retry.
- Simulate retry failure and confirm a failed run plus error row.
- Run with repeated seen Redis IDs and confirm no detail fetch.
- Confirm manual and continuous runs without initial snapshot are rejected.
- Confirm explicit recalibration marks visible IDs as seen without creating opportunities.
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
