# 005 Fast Detection, Redis Seen Tracking, and Detail Enrichment

Manual and recurring calibration are owned by session start. The baseline marker and policy hash remain internal Redis/runtime state; there is no standalone public calibration contract.

## Goal

Detect public Vinted items as fast as possible, use Redis to decide whether each monitor already processed an item, and prevent duplicate work and alerts within the same monitor.

## Scope

- Use Vinted's public catalog JSON API as the fast catalog path.
- Bootstrap and refresh anonymous public cookies/tokens when the catalog API needs them.
- Do not use catalog HTML parsing as a normal fallback for fast runs.
- Request `newest_first`, `page=1`, and a small configurable `per_page` window, default `5`.
- Force `newest_first` for the fast API request even if the saved catalog URL has another `order`.
- Require Redis before a monitor processes candidates. If Redis is unavailable, fail the run and stop/block that monitor execution.
- Capture the initial catalog snapshot inside every `Iniciar sesion` before manual, continuous, duration or window activation.
- Use Redis seen keys scoped by monitor and evaluation policy hash as the source of truth for whether an item should be processed.
- Store the initial snapshot marker in Redis by monitor and evaluation policy hash with the same TTL as seen keys.
- Use short-lived Redis processing locks to avoid concurrent duplicate work for the same monitor/item.
- Use `items.vinted_item_id` as normalized catalog identity only for items that become opportunities.
- Count `items_found` as unique candidates newly claimed by Redis for that monitor/policy in that run, after excluding an already-persisted opportunity. Raw catalog rows remain event diagnostics and are not a second product metric.
- A session-start baseline seeds visible IDs but reports zero `items_found`; it calibrates the starting point and is excluded from monitor/session performance statistics.
- Fetch item detail for every Redis-new candidate before filter evaluation and opportunity creation, bounded by the configured per-run limit.
- Parse the public item document's JSON-LD and structurally discovered Next/React Flight records; do not depend on dynamic Flight record ids or the Cloudflare-challenged direct detail API.
- Anchor every Flight section to the requested item id. Recommendations or unrelated products in the same record must never contribute plugins, photos, availability, shipping, or pricing.
- Extract detail fields needed for second-stage filtering and opportunity display: title, description, brand, size, physical status, base price/currency, semantic color, category, minimum displayed shipping price, buyer protection fee, total excluding shipping, the complete public photo set, seller rating/badges, and public availability signals when visible.
- Preserve the catalog `favourite_count` and optional non-negative `view_count` on the opportunity item when exposed by the same catalog response. Missing views remain null and never trigger another Vinted request.
- Validate a configurable required-field allowlist before filter evaluation. The default clothing policy requires title, observed description (which may be empty), brand, size, physical status, base price/currency, and at least one photo.
- Persist signed Vinted CDN photo URLs only; image bytes are loaded directly by the PWA and never through the residential Vinted proxy.
- Keep candidate detail work inside the run that observed it. An ordinary transport, response or parser failure waits two seconds and receives one immediate retry; a second failure closes the candidate without an opportunity.
- Never persist a candidate payload or delayed detail retry in Redis. A process crash may leave only the short owner-checked processing lock; expiry permits a later catalog observation, and manual session relaunch remains the accepted recovery.
- Keep terminal candidate transitions recoverable across the PostgreSQL commit and Redis finalize boundary so a `finalizing` run converges without emitting a contradictory terminal event.
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
  - configurable TTL and per-monitor cap.
- Database:
  - `items` for opportunity items only;
  - detail fields on `items`.

## Acceptance Criteria

- Catalog fetch uses the JSON API in the fast path.
- If the JSON API returns a challenge, rejects the anonymous session or rate-limits the request, the first response marks the run failed and invalidates the prepared context without refresh or retry.
- The app/worker keeps running after that terminal task result, and the consumer ACKs it instead of requeueing it.
- HTML catalog parsing is not used as a fallback for a failed fast run.
- Redis availability is checked before candidate processing; unavailable Redis marks the run failed and no detail/opportunity work happens.
- Item catalog identity is checked idempotently against Redis seen state before detail/filter work.
- An ordinary run without a valid initial snapshot fails before catalog/detail/filter/opportunity work, closes/stops its session and requires a new start. It never recalibrates silently.
- Every session start fetches the current catalog window while inactive, marks visible IDs as seen, records a sessionless baseline run and creates no item or opportunity.
- First time an item appears in a monitor/policy, it is considered new for that monitor.
- Re-running the same monitor with the same top items does not create another opportunity.
- The same item appearing under another monitor can be considered new for that other monitor.
- Changing the monitor URL or monitor-owned filter definition changes the policy hash and can reevaluate visible items.
- Changing the monitor URL or monitor-owned filter definition requires the next session start to seed the new policy hash.
- Non-opportunity candidates are not persisted as `items`.
- `items_found` is fixed immediately after Redis claim and durable-opportunity exclusion. It counts every monitor-new candidate admitted to detail evaluation, whether the run later succeeds, fails on a provider/session challenge, creates an opportunity, matches a blacklist, lacks valid detail or exceeds the per-run detail budget. Seen/processing hits and an already-existing monitor opportunity count zero.
- `opportunities_created` counts only new monitor opportunities committed by that run. The API exposes no redundant `items_new` field, and the PWA presents only `Encontrados` and `Oportunidades` as item-result metrics; filter outcomes remain available in technical events.
- Details are fetched for monitor-new candidates before opportunity creation and are bounded by the configured per-run limit.
- An ordinary detail transport, non-terminal HTTP or parser failure receives exactly one retry after two seconds in the same run. A second failure creates no opportunity, counts as pending diagnostics and is marked seen. Genuine `404/410`, valid incomplete detail, a rate-limited concurrent-wave deferral and candidates beyond the per-run detail budget are terminal without delayed work.
- A Cloudflare/DataDome detail challenge or classified prepared-session/context failure terminates the whole task on its first response, closes the monitor session and releases the claimed processing locks. It never waits, preserves or replays that candidate batch.
- Valid detail that lacks a configured required field is a terminal `detail_incomplete` outcome, names the missing fields, creates no opportunity, and is marked seen.
- Optional fields absent from a valid document remain null and do not block an opportunity. An observed empty description is valid and contributes no blacklist text.
- Money amount and currency are selected from the same source and must be finite, non-negative, and internally consistent. Invalid optional prices remain null with a validation warning.
- Public availability is conservative: any observed blocking signal wins, and `buyable` is emitted only when every required positive signal is explicit and no reservation, stock, visibility, processing, permission, or shipping blocker is present.
- Redis seen state is marked only after a terminal outcome. A candidate that exhausts its immediate retry is terminal even though it creates no opportunity.
- Processing locks have an owner token. Expiry allows retry, while a stale worker cannot release a lock reacquired by another worker.
- Detail requests remain sequential by default. Experimental concurrency is limited to two isolated persistent HTTP lanes cloned from the same prepared context and sticky proxy; PostgreSQL, Redis and persisted events stay on the caller thread.
- Concurrent scheduling uses strict waves, preserves catalog input order and does not persist a cookie branch until all results are joined. The selected context is the lane of the last logical successful request, never completion order, and canary mode validates it against the catalog before commit. An ordinary failed prefetched result may retry once through the reconciled provider in the same run.
- Blacklist head inspection is observational in shadow mode. It may terminate a response only when the canonical item id matches, a safely isolated description suffix proves exclusion, and enforced mode is enabled; a partial no-match never passes an item.
- Blacklist evaluation uses the public item description only. Catalog title, brand, size, status, seller, color, category and badges never contribute filter text.
- Enforced head rejection may inspect only a description suffix safely separated from an exact catalog-title prefix. Ambiguous metadata, a missing/mismatched canonical, or a partial no-match continues the same HTTP response to EOF; it never starts a second detail request.
- Reserved, hidden, processing, closed, shipping-unavailable and otherwise non-buyable public states still create an opportunity after the description passes. Availability is decision data, not an exclusion rule.
- Missing optional shipping, buyer-protection, total-price or availability signals remain null/unknown and do not block opportunity creation.
- A run is reported successful only after its PostgreSQL effects and Redis candidate transitions are durable. Recovery paths must not leave contradictory terminal events.
- Concurrent monitors may share one global `Item` row without either run failing; each monitor still owns its opportunity independently.
- The evaluation policy hash includes a versioned description-only contract so Redis state produced under earlier multi-field filtering is never mixed with new decisions. Every policy needs a baseline owned by session start.

## Verification

- Run the fast provider and confirm it calls the catalog JSON API.
- Simulate expired anonymous session and confirm one bootstrap-and-retry.
- Simulate retry failure and confirm a failed run plus error row.
- Run with repeated seen Redis IDs and confirm no detail fetch.
- Confirm a manual start marks visible IDs as seen without creating opportunities and opens a session only after success.
- Confirm a recurring start does the same, persists only a later deadline, and lets the real scheduler/queue/consumer create exactly one opportunity for one later unseen passing ID.
- Confirm manual and recurring ordinary runs without a marker fail before candidate work, preserve their configured mode, close/stop and ask for a new session start.
- Run the same fixture twice and confirm no duplicate opportunity or repeated filter work.
- Run the same item under two monitors and confirm each monitor can count the item once without duplicating alerts inside either monitor.
- Confirm `items_found` and `opportunities_created` reflect monitor-new candidates and committed opportunities, while raw catalog count remains confined to run events.
- Confirm Redis unavailable fails the run and does not create opportunities.
- Confirm discarded candidates are not inserted into `items`.
- Inject failure before/after the PostgreSQL commit and during Redis finalize; confirm the `finalizing` run converges to one item/opportunity and terminal Redis state before any new catalog run.
- Make the first ordinary detail attempt fail and the second succeed after a two-second wait; confirm one run, one opportunity, two requests and no Redis candidate payload. Repeat with both attempts failing and confirm a terminal seen candidate with no opportunity.
- Expire and reacquire a processing lock, then confirm a stale release cannot remove the new owner's lock.
- Process the same item concurrently under two monitors and confirm one global item plus one opportunity per monitor.

The bounded 14.37 real acceptance observed five public catalog IDs during session start, persisted the marker plus five seen entries without items or opportunities, and immediately observed the same five IDs in the manual run as `items_found=0` and `opportunities_created=0`. The run reused the prepared anonymous session; no detail request or retry entry was needed. Exact source cleanup returned operational Redis to zero keys, created no item and left the pre-existing global item row present.

The 14.38 real recurring gate owns the complementary positive proof. It snapshots hashes of the five baseline IDs, then lets exactly three real scheduler/queue/consumer tasks observe the live catalog. At least one opportunity must come from a later ID whose hash was absent from that baseline; baseline IDs must produce none, and the unique monitor/item contract must prevent duplicates across later runs. The gate is invalid if it needs a fourth run, manual execution, standalone preparation or synthetic provider state to obtain that evidence.

The bounded 2026-07-16 attempt produced no deduplication or opportunity evidence because required session preparation failed before baseline persistence. The accepted-JSON diagnostic probe did not override the missing egress-country/DataDome context, no recurring task was admitted and the test was stopped without retry. This proof therefore remains open rather than being inferred from focused or synthetic tests.

The authorized retry later produced a real five-ID baseline with `5/0/0`, stored its marker and activated the recurring session without an immediate business run. The first real scheduler task reused that prepared session, but both catalog attempts failed before HTTP with curl code `5` because the worker could not resolve the proxy gateway. It was ACKed as one failed run with no reprepare, candidate work, item or opportunity. Therefore the baseline half is now evidenced, but the required later-ID opportunity and duplicate-free repetition remain open; no synthetic state, manual run, second start or fourth observation substitutes for them.

The final bounded 2026-07-17 pass closes that proof. One five-ID real baseline produced no item or opportunity, then exactly three real scheduler/Redis/consumer runs reused the same prepared session and persisted eight unique opportunities whose keyed HMACs were all disjoint from the baseline set. Every opportunity belonged to one of those three runs, the monitor/item uniqueness contract held across repeated observations, and PWA stop prevented a fourth run. Exact cleanup removed the QA opportunities and their new orphan items while preserving the pre-existing item fingerprint and all other stable SQL/Redis state.

## Audit

- Confirm the fast path has no hidden HTML catalog fallback.
- Confirm a failed source run does not stop API, PWA, worker, or other sources.
- Confirm no cookies, tokens, checkout payloads, addresses, payment data, or pickup point data are persisted.
- Confirm detail fetches are bounded by configurable limits and concurrency.
- Confirm configured concurrency does not activate while detail fetch mode is `serial`, and that canary mode never exceeds two in-flight documents.
- Confirm a five-item wave returns decisions in catalog order even when HTTP completion order differs, and that SQL/Redis/event writes occur only on the caller thread.
- Confirm early-filter shadow mode never changes persistence, while enforced early discard produces the same terminal Redis/filter result as a complete matching detail.
- Confirm terms present only outside the description never discard, and an ambiguous head falls through to the full-description decision on the same request.
- Keep `enforced` as the default only while every isolatable audit sample has normalized meta suffix equal to the normalized Flight description and early matches remain a subset of final description matches; any counterexample returns the default to `shadow`.
- Confirm `view_count` accepts zero and a non-negative integer from catalog JSON and remains null when absent/invalid.
- Confirm overlapping monitors cannot duplicate alerts within one monitor but can independently alert on the same catalog item.
