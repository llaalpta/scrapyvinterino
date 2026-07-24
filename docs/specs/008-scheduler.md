# 008 Bounded Concurrent Scheduler and Runtime Cache

## Planned proxy trust and traffic block

The program is split after its documentation-only planning branch. `14.49` owns proxy-only runtime in spec 010, and `14.50` depends on that invariant for its visible configuration/cooldown contract. `14.51` is technically independent and follows them only by agreed product priority. Each implementation requires its own confirmation, branch, real scenario, audit and merge.

### 14.50 honest proxy settings and visible cooldown

Status: `done` on `fix/14.50-honest-proxy-cooldown` after a positive independent audit. Settings describes local configuration, not remote readiness. Monitor start remains the only real data-plane check: it creates the monitor-owned sticky, validates its neutral egress/country and only then may call Vinted.

Acceptance criteria:

1. The independent `Test IP` control, endpoint and client state are removed without a tombstone; Alembic `0023` removes durable `last_test_*` fields. Profile creation and activation require host, port, credentials, target country and a valid sticky template; concurrency is labelled as a local limit.
2. Monitors and Settings show an active cooldown only while `cooldown_until` is in the future, including failure count, expiry and remaining time. Terminal SSE/commands refresh proxy state without recreating the stream, and a known all-proxies-cooling state blocks start until local expiry.
3. A failed baseline is not retried automatically and no control clears cooldown early. After expiry, `Reintentar sesion` uses the normal start path; a challenge-invalidated context requires a new sticky, without promising a different physical IP.

Representative integration: against a migrated disposable database and the authenticated live API/PWA, a controlled provider raises one classified challenge through the real start/run transition. PostgreSQL cooldown and the SSE-refreshed PWA agree; a second click is unavailable and produces no request/run. Expiry is advanced within owned QA state, after which one controlled success is admitted. A local negative verifies incomplete configuration cannot activate. Cleanup removes all QA rows and restores service state. External traffic allowance is zero.

Verification passed `46` focused cases and `1` live authenticated Playwright case. The live case migrated seeded `0022` state through `0023`, observed one classified challenge and PostgreSQL cooldown through the existing SSE connection, proved zero second start, rejected incomplete activation, and admitted one normal retry only after owned QA expiry. Ruff and frontend lint/build passed. The single full backend pass reached `538 passed, 9 skipped` before exposing nine obsolete test expectations; their exact paths were corrected and are included in the green `46`-case recheck, so the full suite was not repeated beyond its configured once-per-task limit. Cleanup preserved operational PostgreSQL/Redis fingerprints, restored worker/watchdog and produced no external traffic. The accepted residual is that the live challenge originates in controlled catalog search; the preparation boundary shares the reviewed terminal handler and remains covered by focused/static evidence rather than a second QA setup.

### 14.51 accumulated and session proxy traffic

Status: `done` on `feature/14.51-monitor-session-proxy-traffic` after a positive independent audit. The existing durable per-run transfer estimate remains diagnostic input; the product view moves consumption to the monitor and active/latest-session scopes.

Acceptance criteria:

1. Monitor stats expose a typed proxy-traffic summary with explicit no-run, not-applicable, not-measured, measured and partial states. Accumulated traffic includes every source-owned run, including baseline, diagnostic and failed attempts, without changing business counters or chart ranges.
2. A successful session-start baseline records the monitor session it opened in runtime metadata. Session traffic includes that calibration plus linked later runs; a failed baseline is accumulated only, and historical missing linkage/telemetry is reported partial instead of guessed or zero.
3. The PWA renders compact estimated bytes/requests in `Acumulado del monitor` and `Sesion activa`/`Ultima sesion cerrada`, removes the five-run timing/traffic panel and keeps DataImpulse billing explicitly authoritative.

Representative integration: one controlled loopback baseline and later run traverse the live API/PostgreSQL/PWA path with known transfer observations, then stop exposes the same latest-session total. Focused negatives cover a failed baseline, response-less attempt, malformed/missing historical metadata and direct historical data. Cleanup restores initial state; no Vinted, proxy or vendor request is allowed.

Excluded: vendor usage APIs, monetary estimates, historical backfill, fingerprint changes and a replacement per-run UI.

Verification passed five focused aggregation cases, five activation/linkage cases, Ruff, frontend lint/build and one isolated authenticated Playwright flow through the live API/PostgreSQL/PWA path. The controlled baseline contributed `1000` bytes and the later catalog/detail run `3000`; durable rows, API, accumulated UI, active session and stopped latest session all agreed on `4000` bytes and three observed requests. Cleanup preserved operational PostgreSQL/Redis fingerprints, restored worker/watchdog and made no Vinted, proxy or vendor request. The independent audit found one B documentation mismatch (`direct` is `not_applicable`, not `not_measured`); the focused re-audit was positive after correction.

### 14.52 compact monitor activity

Status: `done` on `feat/14.52-compact-monitor-activity` after a positive independent audit. This is a frontend-only presentation outcome: existing source, session, traffic and chart data remain authoritative and unchanged.

Acceptance criteria:

1. Selected-monitor order is identity/actions, compact filter status, collapsed HTTP-context status, compact accumulated/session performance, accumulated chart and collapsed logs. Compatible filters collapse to counts while unsupported blockers remain visible; one prepared context also exposes its current/max use count in the summary.
2. Performance uses one directly comparable accumulated/session table for time, business counters, failures and proxy traffic, with one billing-authority note. A chart with business activity is 150-170 px tall; a range with no business run shows a compact empty state instead of an empty canvas.
3. At `1440x900`, the default active-session detail through the logs summary fits in one viewport once aligned at its top. At `390x844`, comparison rows stack without horizontal overflow and no blocker or unknown/partial traffic state is hidden.

Representative integration: extend the isolated `monitor-session-proxy-traffic` live API/PostgreSQL/PWA scenario. Its controlled baseline proves the no-business-chart negative; one later run supplies the accumulated/active values and chart, then stop preserves the latest-session comparison. Playwright checks order, collapsed controls, desktop containment and mobile overflow. Cleanup restores PostgreSQL/Redis fingerprints and service ownership. External Vinted, proxy and vendor allowance is zero.

Excluded: API/schema changes, chart semantics/range changes, configurable layouts, monitor table/create-form redesign and expanded-log redesign.

Verification passed five traffic-aggregation cases, five activation/linkage cases, Ruff, frontend lint/build and one isolated authenticated Playwright flow over the live API/PostgreSQL/Redis/PWA boundaries. It proved the compact empty range after baseline, the 170 px populated chart, the accumulated/active and stopped/latest comparison, one prepared context at `7/50`, desktop containment through logs at `1440x900`, and stacked rows without overflow at `390x844`. The audit found one B coverage gap: mobile evidence used only compatible/measured state. The same scenario now also reloads an API-derived partial traffic aggregate and an existing monitor with blocked `color_ids[]`, requiring both honest labels to remain visible and uncollapsed at 390 px; the finding-specific re-audit was positive. Cleanup preserved operational PostgreSQL/Redis fingerprints, restored worker/watchdog and allowed no external destination.

A post-merge check against the operational Vite at `1366x768` exposed that its long-lived generated stylesheet still omitted the already-merged table `min-width: 0`; the generic results-table minimum made the performance table `1180` px inside a `1018` px hidden-overflow wrapper, clipping the proxy column. The original assertion compared the table's own scroll/client width and could pass that overflow. Restarting only the frontend loaded the current source rule, after which real authenticated Playwright observed the wrapper at `1018/1018`, the proxy header and both traffic cells visible, zero external requests and zero QA residue. The permanent guard now compares wrapper scroll/client width, so the stale/clipped state fails.

### 14.53 context lifecycle and proxy-traffic clarity

Status: `done` on `feat/14.53-context-traffic-clarity` after a positive independent audit. This is a presentation and verification outcome: prepared-context eligibility, scheduler behavior, traffic aggregation, API fields and persistence remain unchanged.

Acceptance criteria:

1. The collapsed HTTP-context summary distinguishes per-run context uses from measured HTTP transfers and exposes the current/max uses plus the real expiry. Expanded guidance states that expiry or exhaustion is evaluated when a run selects its context: the next run prepares a replacement on demand, an already-started run is not interrupted, and a preparation failure remains a visible run failure.
2. The optional monitor limit is labelled as completed runs with the same context, including that an empty value does not stop the monitor and context rotation resets this counter. The performance table separates locally observed proxy bytes from observed request count, preserving explicit no-run, not-applicable, unmeasured and partial states without inventing zeroes.
3. The eight-column desktop table remains fully visible at `1366` and `1440` px, the mobile comparison remains overflow-free at `390` px, and chart ticks/axis titles use `10` px while the legend/session marker use `11` px without changing the chart height, ranges or tooltip readability.

Representative integration: extend the isolated `monitor-session-proxy-traffic` API/PostgreSQL/PWA scenario with separate traffic/request cells, measured and partial states, prepared-context expiry and desktop/mobile containment. A focused loopback-provider run starts with an expired prepared context and proves that the business run selects a newly prepared context while the same monitor session remains active. The negative variation makes replacement preparation fail and requires one visible failed run without a hidden retry. Cleanup restores PostgreSQL/Redis fingerprints and service ownership. External Vinted, proxy and vendor allowance is zero.

Excluded: proactive refresh, scheduler/session-limit changes, API/schema changes, vendor usage APIs, traffic backfill, chart-range changes and additional recovery behavior.

Verification passed Ruff, frontend lint/build, five focused traffic cases, eight activation/context-rotation cases and one isolated authenticated Playwright flow over the live API/PostgreSQL/Redis/PWA path. The runtime cases proved both expired and `50/50` contexts are replaced on demand while a recurring monitor remains active, and a failed replacement produces one visible failure without a hidden retry. The PWA separated `4 kB` from `3` observed requests, preserved the partial `3 observed / 1 unmeasured` state, transitioned a prepared context to expired, kept both columns visible at `1366`, `1440` and `390` px, and rendered the agreed chart text sizes. Cleanup removed all QA resources, preserved operational PostgreSQL/Redis fingerprints, restored the initially running worker/watchdog and allowed no external destination.

The independent read-only audit found no A, B or C findings. It confirmed the context-use/request distinction, on-demand expiry and exhaustion behavior, visible no-retry failure path, honest partial traffic states and responsive containment against the final diff and live evidence.

### 14.54 provider-bound sticky lifecycle and bounded recovery

Status: `14.54.1` is current. The `14.50` first-failure cooldown/no-retry contract remains authoritative until `14.54.2` merges; `14.54.2` through `14.54.4` remain ordered standard tasks. Each slice has its own confirmation, branch, real scenario, audit, PR and merge.

DataImpulse on rotating HTTP port `823` accepts a `sessid` username parameter that asks the gateway to retain one exit IP for approximately 30 minutes. The product uses a 25-minute local safety limit and a new session ID rather than integrating the provider-specific rotation API. A monitor session is not capped by that lifetime: prepared HTTP context rotates when either its effective TTL or its completed-run use limit is reached.

#### 14.54.1 per-profile sticky contract and lifetime

Status: `done`.

Acceptance criteria:

1. One migration moves the sticky username template and sticky TTL into `proxy_profiles`; existing profiles are backfilled with `{username};sessid.{session_id}` and `25` minutes, and the obsolete global template is removed from runtime configuration. Both values participate in effective proxy identity, so an edit fences new work and invalidates prior prepared contexts through the existing identity transition.
2. API and PWA create/read/update both fields with a template containing exactly `{username}` and `{session_id}` and TTL `1..120`. Invalid input is rejected without changing the profile, identity generation or prepared context.
3. A saved context expires at the earlier of the global anonymous-context TTL and the selected profile's sticky TTL. The existing maximum uses remain completed runs, not individual HTTP requests; expiry or exhaustion prepares a replacement on demand without stopping the monitor session.

Representative integration: migrate a disposable PostgreSQL database, edit a proxy through the authenticated live PWA/API, save a controlled prepared context and advance owned time/use state until the next real run replaces it while the same monitor session remains open. The negative variation submits an invalid template/TTL and observes no database or provider mutation. One bounded provider conformance check may make at most four DataImpulse-proxied requests to the configured neutral egress diagnostic: two with one `sessid` and up to two with fresh IDs. It must never call Vinted or DataDome, expose credentials or require distinct physical IPs. Cleanup restores service ownership and removes all QA SQL/Redis state.

Verification passed migration `0024`, Ruff, frontend lint/build, the focused identity/TTL cases and one isolated authenticated Playwright flow over the live PWA/API/PostgreSQL/Redis path (`25/25`). The flow created the default DataImpulse contract, exercised its constructed proxy username, changed template and TTL, invalidated and replaced prepared context while preserving the open monitor session, and rejected malformed, out-of-range and non-integer edits without profile/session/run mutation. The complete isolated backend gate passed `564` normal plus `3` loopback-only tests, with 11 opt-in skips. Cleanup preserved operational PostgreSQL/Redis fingerprints; no Vinted, DataDome, proxy or vendor request was made, so the optional conformance allowance remained unused.

The independent read-only audit returned positive with no A, B or C findings. It confirmed migration/backfill, removal of the global fallback, identity fencing and context invalidation, effective TTL calculation, strict API/PWA mutation behavior, loopback-only live evidence and absence of secrets or QA residue in the final diff.

#### 14.54.2 automatic same-profile pre-candidate recovery

Acceptance criteria:

1. One command still owns one run. Before candidates are accepted, Cloudflare/DataDome challenge, catalog `401/403`, unusable anonymous context or proxy/egress transport failure may consume at most two attempts on the selected profile: the current/initial attempt and one fresh sticky. A forced sticky always performs a real proxied egress diagnostic; a repeated observed IP is rejected before Vinted and consumes that second attempt.
2. Only after both attempts fail is that profile penalized once and placed in cooldown. In this slice the run then terminates once with `profile_session_acquisition_exhausted`; it does not select another profile. A successful second attempt clears only that profile's failure state and retains its new context.
3. `429`, Redis/source/identity/configuration failures and internal errors do not enter this loop. Once catalog candidates have been accepted, no work is replayed: a later challenge invalidates context and fails visibly, and a later run resolves a new context through normal pool eligibility.

Attempt events retain safe profile ID/name, attempt/limit, rotation reason and `egress_changed`; they never expose credentials, full sticky IDs or an additional raw IP. All attempt traffic contributes to the single run's transfer totals, while terminal success identifies the profile whose context was retained.

Representative integration: use the live API and PostgreSQL with a deterministic loopback upstream. The selected profile fails its initial bootstrap, obtains a different observed egress through one fresh sticky and succeeds while the command retains one run, seeds one baseline and opens one monitor session. The negative variation returns the known rejected IP for attempt two: no second Vinted request is made, the profile enters cooldown once and one failed run remains without baseline or active session. Focused cases cover both-attempt exhaustion, `429` without retry and the post-candidate no-replay boundary. Cleanup removes all QA SQL state and restores service ownership. External Vinted, proxy and vendor allowance is zero.

#### 14.54.3 atomic cross-profile fallback

Acceptance criteria:

1. After `14.54.2` exhausts profile A, the same running run captures the remaining eligible profiles once in normal pool order and never revisits one. It may consider candidate B only through the serialized egress-admission lock shared by ordinary scheduler selection and fallback. The transaction excludes its own A assignment from accounting, locks and revalidates B, enforces B's capacity, captures B's identity generation and commits the run's durable runtime binding to B before any B provider is constructed. A's penalty is already committed and A's traffic fence is released before this handoff.
2. Once a PostgreSQL run exists, its durable profile/generation binding is authoritative; the original Redis task payload identifies only initial admission and never authorizes traffic through B. Scheduler accounting deduplicates that task ID against the running SQL row and counts the run exactly once against its current binding. Redelivery must resume or close from the durable binding rather than reconstruct authority from stale profile A.
3. Before B traffic, the consumer acquires B's shared identity fence and revalidates activity, country, cooldown, capacity and the captured generation. If B became saturated or changed identity, it constructs no B provider and may consider the next captured candidate through the same bounded admission path. An admitted profile receives the same two-attempt contract from `14.54.2`, once. Pool exhaustion produces one `session_acquisition_exhausted` terminal result; one run owns all safe attempt events and accumulated transfer observations.

Representative integration: use the live scheduler/Redis reservation/consumer/PostgreSQL path with a deterministic loopback upstream. A queued task admitted on profile A exhausts both attempts; the admission handoff binds the same SQL run to B, B succeeds, exactly one baseline/session is created and the task is acknowledged once. The negative variation saturates B or advances its identity at a controlled barrier between binding and provider construction; zero B provider calls occur, capacity is not exceeded, and the run either tries the next preselected eligible candidate or terminates once. Focused tests cover stale-payload redelivery and SQL/task-ID capacity deduplication. Cleanup removes QA SQL/Redis state and restores the worker to its initial state. External Vinted, proxy and vendor allowance is zero.

#### 14.54.4 explicit manual cooldown override

Acceptance criteria:

1. `POST /api/monitors/{id}/vinted-session/retry` accepts one `proxy_profile_id` and bypasses only that profile's current cooldown for one fresh-sticky attempt. It uses the same non-cached egress and known-rejected-IP gate as automatic recovery. Activity, country, capacity, complete configuration, identity fencing and monitor single-flight remain mandatory.
2. The stopped monitor PWA exposes `Reintentar con <perfil>` for one cooling profile and a compact selector when several are eligible. Each completed click permits another explicit attempt, but no click starts an automatic fallback loop.
3. Cooldown is not cleared before traffic. Success completes the normal baseline/activation and clears that profile through the existing success transition; failure preserves or extends its penalty. An inactive, deleted, incompatible or concurrently occupied profile is rejected before provider construction.

Representative integration: against the authenticated live PWA/API/PostgreSQL path and deterministic loopback upstream, an initial failed baseline shows cooldown, the explicit button remains available, one selected retry uses a fresh sticky and a controlled success activates the monitor. The negative variation targets an invalid profile and observes `409/422`, unchanged cooldown and zero provider calls. Playwright checks visible state and API/database agreement. External Vinted, proxy and vendor allowance is zero.

## 14.19 Worker Redis availability

Status: `done`. This is a contained fail-stop correction for the current local worker, not a general dependency-readiness platform.

After startup, the worker supervisor probes Redis on its existing supervision cadence. A failed probe is a process-level dependency loss: it is logged safely and the worker exits non-zero without an internal reconnect loop or fallback. The producer heartbeat is not deleted because a replacement worker may already own a newer signal; instead, process exit stops renewal and the existing timeout makes scheduler availability false. Docker Compose owns restart, and a replacement cannot publish a heartbeat until Redis is reachable at startup.

Acceptance criteria:

1. Losing Redis after a successful worker start causes a sanitized critical log and non-zero worker exit; `restart: unless-stopped` starts a replacement attempt, which remains unavailable until Redis returns.
2. The last PostgreSQL heartbeat is not refreshed after the loss. `GET /api/scheduler` becomes `worker_available=false` and `effective_enabled=false` after the configured timeout, and the existing PWA Settings poll shows unavailable without a new readiness field.
3. With Redis healthy, the idle worker remains available and ordinary non-Redis consumer failure keeps the existing per-thread restart behavior instead of terminating the whole process.

Representative integration: use a migrated disposable PostgreSQL database, a disposable Redis container and a disposable worker container with Docker restart enabled. Keep the scheduler without sources or queued work and block every outbound destination. Observe a fresh heartbeat, stop only the disposable Redis, prove worker restart/non-zero failure plus heartbeat expiry and the live API/PWA unavailable state, restore Redis and prove the worker and PWA become available again. Remove the containers, database, role, Redis state, API/Vite processes and logs, and verify the operational PostgreSQL/Redis fingerprints and initial service state are unchanged. The negative variation keeps Redis healthy while a focused supervisor test crashes a consumer and observes only its thread restart. External Vinted, proxy and Telegram allowance is zero.

Excluded: API-wide readiness, explicit heartbeat deletion, queue/drain redesign, Redis restart ownership and production lifecycle hardening.

Verification: 17 focused scheduler/supervisor tests and one live Playwright outage/recovery case passed against a migrated disposable PostgreSQL database plus disposable Redis/worker containers on an internal Docker network. The real worker exited non-zero, Docker restarted it, the heartbeat stopped advancing, API/PWA became unavailable after timeout and recovered after Redis returned. Cleanup left no QA containers, networks, roles, databases, Redis keys, ports or logs; operational PostgreSQL/Redis fingerprints and service state were unchanged. No Vinted, proxy or Telegram traffic was allowed.

## 14.46 Single scheduler deployment gate

Status: `done`. The redundant global PWA switch and `app_settings.scheduler.enabled` contract are removed. Docker alone owns process lifecycle, `SCHEDULER_ENABLED` is the deployment kill-switch, and each monitor owns recurrence through `Iniciar sesion`/`Detener sesion`. The scheduler remains idle when no recurring session is active.

Acceptance criteria:

1. Settings exposes scheduler status, heartbeat, capacity and tuning without a global enable/disable action; `GET /api/scheduler` omits `enabled` and `PATCH {"enabled": ...}` returns `422` without mutating PostgreSQL.
2. `effective_enabled` depends only on the deployment gate, live worker heartbeat and effective egress capacity. A recurrent start works with those conditions and remains fail-stop when `.env` blocks runtime or the worker is unavailable.
3. Alembic 0021 removes only `app_settings.scheduler.enabled`, preserving every other scheduler setting and `scheduler_worker_heartbeat`; no compatibility reader or invented downgrade value remains.

Representative integration: the isolated authenticated PWA/API/PostgreSQL/Redis scenario uses a real `SchedulerRunner`, queue reservation/ACK and `TaskConsumer`, with a controlled loopback provider only at the external boundary. It verifies the missing buttons and API field, rejects the legacy PATCH, then starts a recurring session, persists its later deadline and completes the same/new/repeated candidate trajectory. Focused negatives cover `.env` disabled and missing heartbeat. Cleanup restores operational fingerprints and service ownership; Vinted/proxy/Telegram allowance is zero.

Verification passed the nine-case isolated scenario including live Playwright (`9 passed`), the disposable PostgreSQL `0020 -> 0021` probe, Ruff, frontend lint/build and the complete isolated backend gate (`525 passed`, `9 skipped`, plus `3 passed` loopback-only). The operational idle database reached 0021 with the old key absent before the API restarted; no active monitor, run, session or queued task existed.

## 14.47 Compact monitor detail and guarded editing

Status: `done`. The monitor table remains a compact selector rather than a second command surface. The selected detail starts with persisted identity, effective catalog filters and state-valid actions; editing is an explicit stopped/idle mode, and performance remains grouped by monitor session rather than by reusable HTTP context.

Acceptance criteria:

1. In normal mode, inactive monitors expose start/edit/archive, active manual monitors expose run/stop, and active recurring monitors expose only stop. Raw URL and configuration inputs are not duplicated outside edit mode.
2. Dirty edit navigation to another monitor or PWA section requires an in-app discard decision. Save success exits editing with the persisted response, save rejection keeps the draft, and cancel/discard restores the persisted source without traffic.
3. Accumulated monitor metrics and active/latest-session metrics remain unchanged. Current ordering and compact presentation are owned by 14.52; logs remain collapsed by default and responsive layout has no horizontal overflow.

The representative real gate is the existing isolated monitor-identity PWA/API/PostgreSQL scenario with a second source, a local HTTP-context diagnostic row and desktop/mobile Playwright. Worker/watchdog and all external destinations remain unavailable; cleanup restores the initial operational fingerprints and service ownership.

Verification passed `8` focused cases plus `1` live Playwright case. The live PWA kept the table selector command-free, enforced read/edit and dirty-navigation boundaries, presented the active manual action set and had no horizontal overflow at `390x844`. Its then-current performance-before-context order is superseded by 14.52 without changing the guarded editing contract. No external request was allowed or observed; cleanup removed all isolated resources and preserved operational PostgreSQL/Redis fingerprints. Ruff and frontend lint/build passed.

## Planned 14.34 session program

Status: `done`. Slices 14.34.1, 14.34.2 and 14.34.3 are implemented and verified. Their current clauses replace the temporary calibration/stop contracts across specs 001, 003, 005, 008 and 010 plus architecture/data-model prose.

The user saves mode, cadence, duration/window and filters while stopped. Every session start observes one internal `baseline` run while the monitor remains inactive. It adds current catalog IDs to the existing monitor/policy seen state, preserves older seen markers, creates no item/opportunity and is returned as `201 RunRead`. Validation failures create no run; operational baseline failure returns its failed run and leaves no active session/deadline. If the Redis baseline marker later expires during an active session, the next ordinary run fails visibly and closes/stops that session; it never recalibrates silently. Restarting is the manual recovery.

No slice adds a migration, compatibility adapter, automatic retry, hidden fallback or hard network cancellation. Baseline events and policy hashes remain internal diagnostics. Configuration stays read-only while a session is active and, after 14.34.3, while it is draining. Vinted, proxy and Telegram traffic allowance is zero; every QA slice cleans its rows, Redis keys and temporary processes and restores initial services.

### 14.34.1 Manual session-start baseline

Status: `done`. This standard vertical slice has no migration: manual session start, its later user-triggered runs and their existing PostgreSQL/Redis state form one outcome. Recurring start and graceful in-flight stop remain in 14.34.2 and 14.34.3. Verification passed the isolated live PWA/API/PostgreSQL/Redis scenario, `515` backend tests plus `3` loopback-only cases, Ruff and PWA lint/build without external traffic or QA residue.

`POST /api/monitors/{id}/start` in manual mode performs the baseline and, only on success, creates one active monitor session with `next_run_at=null`. `POST /api/monitors/{id}/runs` requires that active manual session, attaches each single-flight `Ejecutar ahora` run to it and no longer creates/closes a punctual session per run. The 14.34.3 contract keeps stop available during non-terminal session work, makes the source inactive immediately and lets normal drain close the session after every admitted run reaches terminal; stronger fail-stop reasons remain dominant.

Acceptance criteria:

1. Manual start creates exactly one zero-opportunity baseline run and then one active session without a deadline; baseline failure leaves the monitor inactive with no open session.
2. `Ejecutar ahora` is available only inside that active manual session, reuses its session ID and creates exactly one opportunity for one later unseen passing ID without duplicates.
3. Restarting takes another baseline so entries visible during downtime are ignored; a missing/expired marker fails visibly and requires another stop/start instead of implicit calibration.

Representative integration: with the authenticated live API, PostgreSQL and Redis plus a synthetic provider at the Vinted boundary, start on IDs A/B/C, run the same set with zero opportunities, add D and observe exactly one, repeat D without duplication, stop after terminal, add E and restart with another zero-opportunity baseline. The negative variation makes baseline search fail and proves a visible failed run with no active source/session/deadline. Playwright verifies the manual start, active `Ejecutar ahora`, disabled conflicting controls and persisted API/database state.

### 14.34.2 Recurring session-start baseline

Status: `done` on `feature/recurring-session-start-baseline`.

Recurring start preflights effective scheduler availability/capacity before baseline traffic and revalidates admission after it. Success creates one active continuous/duration/window session and persists the first later deadline from activation without a second immediate business run. With interval 60 and jitter 10 percent, the deadline is 60 to 66 seconds after activation. A post-baseline availability/capacity loss returns the existing `503`/`409`, preserves the successful baseline run and leaves the monitor inactive. Recurring active views do not add a manual override.

After every start path owns calibration, remove `POST /api/monitors/{id}/baseline`, its frontend client/button/status panel and public `baseline_ready`/`baseline_policy_hash` fields. Do not retain tombstones or adapters.

Acceptance criteria:

1. Recurring start produces one zero-opportunity baseline, one active session and one persisted later deadline, with no immediate business run or duplicate initial task.
2. The first scheduled run on the same IDs is a no-op; a later unseen passing ID creates one opportunity and repetition creates none.
3. Preflight failure is traffic- and mutation-free; post-baseline admission loss is visible and leaves no active session/deadline while preserving the completed baseline run.

Representative integration: use the authenticated API, PostgreSQL, Redis, real scheduler queue and consumer with a synthetic provider only at the Vinted boundary. Start on five IDs, verify the 60-to-66-second deadline and no immediate business run, consume the same IDs with zero opportunities, then add one ID and observe exactly one opportunity and terminal ACK. The negative variation uses unavailable scheduler preflight and proves zero run/session/task/provider calls. Playwright verifies that the standalone recalibration contract is absent.

Verification passed with the isolated live PWA/API/PostgreSQL/Redis scenario and real in-process scheduler/queue/consumer, the complete backend gate (`516 passed`, `5 skipped` contractually, plus `3 passed` loopback-only), Ruff, PWA lint/build and Compose rendering. The same-ID run, one-new-ID run and repeat were terminal and ACKed with zero queue residue. Two additional isolated manual-session cycles also passed. No Vinted, proxy or Telegram request was allowed; temporary SQL, Redis, process and log state was removed and operational fingerprints were unchanged.

### 14.34.3 Graceful monitor-session stop

Status: `done` on `fix/session-stop-drain`.

`POST /api/monitors/{id}/stop` makes PostgreSQL inactive first, clears future scheduling and best-effort cancels a ready task. A reserved task rechecks authoritative state under the existing source/admission ownership and cannot create a run while the source remains inactive. Session-owned runs already created reach their normal terminal states; only the last normal terminal closes the monitor session with reason `stopped`. Stronger fail-stop paths keep reasons such as `baseline_required` or `redis_unavailable`. The PWA derives `Deteniendo...` from an inactive source plus a session-owned `running/finalizing` run, or conservatively from the successful local Stop command until a directed runs read is accepted. After a reload, unknown run state does not claim a drain but still blocks edit/archive/start until PostgreSQL is read successfully. A sessionless baseline still makes stop return `409`. The local confirmation latch is transient and never overrides PostgreSQL; there is no new durable stopping state or activation generation.

Acceptance criteria:

1. Stop with no non-terminal run closes the session immediately and admits no scheduled/manual work while the source remains inactive.
2. Stop during existing session work returns promptly, admits nothing else while stopped, preserves every run result and closes the session only after the last normal terminal.
3. A deterministic reserved-task race cannot create a run before a later explicit activation, and the PWA cannot edit, archive or restart during a visible drain.

Representative integration: use real PostgreSQL, Redis queue/consumer and API with a blocking provider only at the Vinted boundary. Stop after the run row exists, release the provider, and verify inactive source first, one terminal run, then `monitor_sessions.stopped_at`/reason with no ready/processing residue. The negative barrier reserves a task before stop but delays run admission until after commit and proves zero run/provider calls while the source remains inactive. Playwright aborts the immediate directed runs read and repeats that failure after a full reload: the local latch first preserves `Deteniendo...`, the reloaded unknown state keeps commands fail-closed, and the real consumer terminal SSE batch finally refreshes PostgreSQL state and unlocks the monitor.

Verification passed the isolated `session-stop-drain` gate (`6` focused plus `1` live Playwright case), the complete backend gate (`519 passed`, `6 skipped`, plus `3 passed` loopback-only), Ruff, PWA lint/build and Compose rendering. The self-review additionally covered a concurrent `finalizing` sibling and preserved the stronger `baseline_required`/`redis_unavailable` reasons. Worker/watchdog remained stopped; all traffic was loopback-only, temporary SQL/Redis/process state was removed and operational fingerprints were unchanged.

### 14.38 Bounded real recurring-session acceptance

Status: `done` on `qa/live-recurring-session-acceptance-final` after `ops/worker-proxy-dns-diagnostic`. The bounded 2026-07-16 attempts remain useful failure evidence, while the final 2026-07-17 pass satisfies all three positive criteria without a scheduler redesign, DNS override or runtime retry. It used one temporary continuous monitor with interval `60`, jitter `10%`, the existing live API/PWA/PostgreSQL/Redis, one eligible ES proxy, and the real Compose worker producer/consumers plus scheduler-watchdog.

Before external traffic there must be no active monitor, non-terminal run, open monitor session or Redis key. The deployment gate is enabled while the worker remains unavailable. The worker-unavailable rejection was already accepted twice on 2026-07-16; the final pass deliberately reused that evidence instead of adding another start command. It then started worker, required two advancing heartbeats, started watchdog and issued exactly one positive PWA `start`.

Acceptance has three criteria:

1. Start persists one successful sessionless `5/0/0` baseline before activation, opens one recurring monitor session, stores the first `next_run_at` between `60` and `66` seconds after `monitor_started_at`, and produces no immediate business run or queued task.
2. The real producer, Redis reservation and consumer ACK exactly three scheduler tasks. All three runs are terminal successes in the same monitor session, reuse the prepared Vinted session without reprepare (`request_count` progresses from `1` to `4`), and leave no ready, processing, pending or dead-letter residue. At least one persisted opportunity must belong to an ID absent from the baseline; no baseline ID or repeated later ID may produce an opportunity twice.
3. PWA stop after the third terminal run clears the deadline, closes the session with `stopped` and prevents a fourth run. Cleanup stops watchdog then worker, leaves their converged current-Compose containers stopped, lets the last real heartbeat expire instead of forging an older value, removes only QA authentication/source/run/event/session/opportunity/orphan-item rows plus source/queue Redis keys, and preserves pre-existing data and ordinary proxy telemetry.

No standalone prepare/proxy test, manual override, fourth run, detail retry run or provider retry outside product behavior is allowed. The browser blocks every non-loopback destination, including Vinted CDN images; only the backend may use the configured proxy, egress diagnostic, Vinted and DataDome endpoints.

With `catalog_per_page=5`, detail limit `5`, one catalog retry and serial detail mode, the hard allowance is `45` logical external operations: one six-operation baseline preparation plus, conservatively, one six-operation reprepare, two catalog attempts and five detail requests for each of three scheduler tasks. Any reprepare or terminal failed run fails the gate and triggers an immediate local stop; the larger allowance only contains traffic already initiated before that observation. Redirect hops are not logical operations: Vinted calls have the provider's explicit redirect bound, while egress/DataDome library redirects remain a declared transport residual.

The 2026-07-16 negative path passed: with the deployment gate enabled and worker/watchdog stopped, start returned `503` with zero run, prepared session, Redis key or provider phase. After rebuilding only the stale executors, the worker became healthy, two heartbeats advanced and watchdog remained running. The single allowed positive start then created one failed baseline run after three logical external operations: the required isolated egress diagnostic exhausted its `15`-second timeout, so `egress_country_code` was unavailable; the DataDome collector correctly skipped with `base_context_incomplete`, and the subsequent diagnostic catalog probe returned `200 accepted_json` but could not make the context reusable without `datadome`. The session was persisted `incomplete` and start failed with `VintedSessionRequiredError`. Per the predeclared fail-stop rule, watchdog and worker were stopped immediately and no second provider attempt or scheduler run was allowed. Consequently none of the three positive criteria is accepted and no scheduler-cadence conclusion can be drawn from this attempt.

Cleanup removed the one failed run, `18` events/outbox rows, one error, one incomplete Vinted session, the QA source/user and four owned browser sessions. Redis 0 returned to zero keys; active sources, non-terminal runs, open monitor sessions and every QA SQL count returned to zero; the pre-existing item count remained one and the initially absent scheduler setting was removed. API, PostgreSQL, Redis and the existing Vite process were not restarted. A later bounded connectivity diagnostic permitted one retry, but its real start still had to fail-stop if any required transport or context was unavailable; relaxing country or DataDome readiness was not an acceptance workaround.

The authorized retry repeated the negative path with worker/watchdog stopped and the deployment gate enabled. The PWA visibly blocked its start before POST because `worker_available=false`; one authenticated same-origin POST then returned `503`. It created no run, monitor session, Vinted session, event, error, Redis key or provider phase. After the current worker became healthy, two heartbeats advanced five seconds apart; watchdog stayed running with no critical log and no inactive-source work.

Before that representative scenario, an invalid comparison between the container label and `docker compose config --hash` caused one unnecessary no-build API recreation. A Compose dry run corrected the classification; the API became healthy and every SQL fingerprint, Redis zero state and the existing Vite PID were unchanged before the gate began. No QA/provider action occurred during that setup mistake, but it remains an avoidable verification deviation rather than acceptance evidence.

The single positive PWA start then passed criterion 1. Its sessionless baseline completed `5/0/0`, emitted one five-ID HMAC-only evidence set, prepared one ready anonymous session with request count `1`, opened one recurring monitor session and persisted `next_run_at` exactly `60` seconds after activation. No business run or queue entry existed before that deadline, and the baseline consumed five logical external operations without exposing IDs, IPs, cookies or credentials.

The first due task was submitted, received and ACKed exactly once, with no requeue, dead letter or queue residue. It reused the same prepared session and advanced its request count to `2`, but both allowed catalog attempts ended before HTTP after `8.007 s` and `8.005 s`: the worker recorded curl code `5` while resolving the proxy gateway. Redacted events contained neither raw username, password nor authenticated proxy URL. The run ended failed at `0/0/0`; there was no reprepare, detail work or opportunity, and the gate stopped without another start or scheduler retry after seven total logical operations. Consequently criteria 2 and 3 remain unaccepted and this run cannot establish three-run cadence or post-baseline deduplication.

The fail-stop harness stopped watchdog and worker immediately; its in-context emergency click did not complete, so one corrective PWA `Detener sesion` then cleared the still-future deadline and closed the session as `stopped` while both executors were already down. Cleanup removed seven monitor-owned Redis keys, two runs, `44` events, `44` publications, one error, one prepared session, one monitor session/source, four authenticated QA sessions, one revoked preauth session, one QA user and the scheduler runtime-settings row. All pre-existing non-telemetry SQL fingerprints returned exactly, including the single prior item; Redis returned to zero, worker/watchdog/frontend containers remained stopped, Vite kept its PID and API/PostgreSQL/Redis remained healthy. The real heartbeat was allowed to expire and the proxy's ordinary failure/cooldown/last-used telemetry was preserved. The later worker-boundary diagnostic below removes this prerequisite block but does not accept criteria 2 or 3.

The final 2026-07-17 pass captured deterministic fingerprints before traffic, confirmed scheduler availability through the live PWA and observed two advancing worker heartbeats before starting watchdog. Its only positive `POST /start` completed a sessionless `5/0/0` baseline, opened one monitor session and persisted the first deadline exactly `60.0` seconds after activation with no immediate business run or queue entry. The five baseline IDs remained in memory and were represented only by keyed HMAC evidence.

Exactly three later scheduler runs completed `success` in that monitor session. The same prepared Vinted session remained `ready`, was never re-prepared and advanced `request_count` from `1` after baseline to `2`, `3` and `4`. Worker logs and Redis state matched exactly three scheduler enqueues, three consumer receives and three ACKs, with no requeue, coalescing, dead letter, recovery or queue residue. Eight unique opportunities referenced IDs disjoint from the baseline and belonged to those scheduler runs; no baseline or repeated ID produced a duplicate. The complete trajectory used `22` logical external operations, below the allowance of `45`.

The PWA stop ran after the third terminal, cleared `next_run_at`, closed the monitor session with `stopped` and no fourth enqueue, receive or run appeared after the former deadline plus a scheduler poll. Cleanup stopped watchdog before worker, let the real heartbeat expire, removed only the QA graph and attributed Redis payloads, and closed Playwright with no residual browser process. Stable SQL, non-runtime settings, proxy configuration/identity and the pre-existing item fingerprints matched exactly; Redis returned to zero keys, ordinary proxy telemetry was preserved, API/PostgreSQL/Redis kept their original containers and restart counts, frontend/worker/watchdog returned stopped, and Vite retained PID `16912`. Preliminary harness corrections ended locally before any positive start/provider call and cleaned their owned rows; they are not product evidence.

#### 14.38 worker proxy DNS diagnostic

Status: `done` on `ops/worker-proxy-dns-diagnostic`. This standard prerequisite diagnoses only the failed worker transport boundary; it does not redesign the scheduler, add a runtime fallback or repeat the recurring acceptance.

Acceptance has three criteria:

1. With zero active source, non-terminal run, open monitor session or Redis work, a current worker-boundary container resolves the configured proxy gateway and records only safe booleans/counts for its resolver result.
2. The same boundary completes one HTTPS GET to the configured echo endpoint through a monitor-style sticky identity, with a present result and no redirect, while output and process logs contain no proxy URL, username, password, IP, cookie, token or raw exception. A local deliberately invalid hostname is the mutation-free negative variation and must fail visibly without an external HTTP request.
3. PostgreSQL and Redis fingerprints remain unchanged, no proxy telemetry or prepared session is created, and worker/watchdog return to their initially stopped state. Any temporary container or process is removed.

The hard allowance is two logical HTTPS GETs to the configured IP echo endpoint through the existing eligible proxy, stopping after the first success. DNS lookup and TCP establishment are transport phases, not extra application requests. Vinted, DataDome and Telegram allowance is zero. The diagnostic may apply a minimal Compose/configuration or code correction only if a reproducible cause is isolated; otherwise it records operational evidence and leaves product code unchanged. It does not use the manual diagnostic scripts held by 14.24, modify proxy credentials, add another endpoint, retry inside runtime or relax 14.38.

The 2026-07-17 representative one-off used the current worker image, environment, bind mount and Compose network without starting producer or consumers. The local `.invalid` hostname was rejected before HTTP; the configured gateway resolved to three addresses in one family. The first and only sticky-proxy GET then completed in `1064 ms` with HTTP 2xx, JSON object, IP and country present, ES match, no redirect and zero cookies. Output contained only safe counts, booleans and status class. The one-off was removed and worker/watchdog remained stopped.

Read-only inspection found API and worker on the same Compose network and Docker resolver, with identical effective environment, root filesystem layers and proxy-host construction; no DNS override, image or code difference explained the prior curl code 5. The diagnostic selected the sole eligible ES profile, built a fresh monitor-style sticky username in memory and closed its SQL session before transport. It called no telemetry/event/Redis writer. Active sources, non-terminal runs, open monitor sessions, prepared sessions and Redis remained zero; the one pre-existing proxy, `26` runs and `598` events remained present, Vite retained PID `16912`, and API/PostgreSQL/Redis stayed healthy. The initial raw `pg_dump` hash was discarded because PostgreSQL 17 randomizes its `\\restrict` token; the corrected content hash excluding only those control lines repeated identically at `adbc57dd...1e15`. With no reproducible cause, the minimal correction is no product/config change. The previous failure is treated as transient Docker DNS/network state, and the final 14.38 retry must still fail-stop on any recurrence rather than add a DNS override or retry.

Ruff, Compose rendering and eight focused pure proxy/egress tests passed. An initial broader host pytest selection inherited the container-only database hostname `postgres`; its database cases and cleanup failed before connecting, so it was discarded rather than redirected at the operational database. The backend suite and frontend checks were not repeated because product, schema, configuration and frontend code did not change.

## Goal

Automatically execute active opportunity monitors on safe, bounded intervals with enough concurrency and runtime cache support to keep opportunity alerts fast without relying on post-MVP optimizations.

## Scope

- Enable or disable scheduler globally.
- Enable, stop, or archive each monitor.
- Treat inactive monitors as configured but not launched; active monitors are launched for recurring execution.
- Treat running/executing as run state, not as persistent monitor state.
- Start and stop recurring monitor execution using the monitor's persisted filter definition, cadence, and duration/window mode.
- Persist each recurring activation as a monitor session until it is stopped, archived, expired, or blocked by a stopping failure.
- Let manual start calibrate and open an active session without scheduler state or a deadline; later manual runs are explicit commands inside that session.
- Start a monitor for a bounded duration from now, with `monitor_until` stored on the monitor.
- Configure interval seconds per monitor, default `300`, minimum `60`, maximum `3600`.
- Add jitter/randomization between runs, default `20%`, minimum `0%`, maximum `50%`.
- Configure an optional per-monitor stop limit for Vinted session use count, `stop_after_vinted_session_uses`, empty by default, minimum `1`, maximum `1000`.
- Support one daily allowed execution window configured by start/end timepickers and stored as `HH:MM-HH:MM`.
- Record scheduler-triggered errors in the same run/error model.
- Record safe run progress events for anonymous session bootstrap, catalog API request, retries, detail fetches, and failures.
- Record professional run logs with level, phase, sanitized URL, status code, duration, timeout, attempt/retry details, Redis state, filter outcome, and opportunity outcome.
- Run multiple active monitors concurrently with explicit limits.
- Allow at most `2` active monitor runs globally by default.
- Allow at most `1` active run per monitor.
- Keep monitor execution fair so one noisy monitor cannot starve others.
- Require Redis for per-monitor seen state and processing locks.
- Do not run a monitor when Redis is unavailable.
- Require an explicit initial catalog snapshot before any monitor run can process opportunities.
- Isolate anonymous public Vinted session cookies per monitor and prepared proxy sticky identity.
- Use a deterministic fast catalog flow: create one `curl_cffi` session, load or automatically prepare the encrypted monitor-owned Vinted session for the same proxy sticky identity, diagnose egress with it when configured, then call `/api/v2/catalog/items` with API parameters translated from the saved catalog URL and with browser-coherent headers.
- Keep proxy usage globally managed by the scheduler.
- Support UI-managed proxy profiles with encrypted credentials and a declared proxy country; locale, `Accept-Language`, viewport, and Vinted `x-screen` context are resolved internally from country/domain presets.
- Assign proxy/session identity consistently for a run; do not mix cookies across proxies.
- Require an active global proxy matching the target country for every normal catalog run; absence of eligible proxy capacity fails locally without host egress.

## Interfaces

- Worker:
  - scheduler loop;
  - process supervisor that exits when producer progress expires;
  - independent fail-stop watchdog for active recurring monitors;
  - bounded monitor run executor;
  - Redis seen cache client;
  - isolated provider/session factory.
- API/PWA:
  - scheduler limits and egress settings persisted in `app_settings`;
  - monitor inactive/start/stop/archive controls;
  - monitor configuration save control separated from launch;
  - monitor historical stats endpoint for active monitor performance cards;
  - global proxy pool and scheduler tuning controls; the PWA does not own process lifecycle or a global scheduler gate.
- Configuration:
  - ownership rule: `.env` owns deployment, secrets, worker and anti-bot defaults; UI `app_settings` owns daily operation only;
  - deployment scheduler enable flag in `.env` as an operational gate;
  - deployment-owned producer heartbeat interval and timeout; the scheduler producer refreshes its own heartbeat while waiting between polls;
  - deployment-owned watchdog poll interval and startup grace, both bounded against the producer heartbeat contract;
  - global concurrency limit, default `2`;
  - per-monitor concurrency limit, default `1`;
  - target Vinted country and internal locale/header/viewport/Vinted-screen presets, with deployment-owned defaults for explicit development diagnostics;
  - per-proxy run concurrency limit stored on each proxy profile;
  - catalog results per run, detail fetch candidate limit, request timeout, proxy cooldown, and stop-after-failures settings;
  - monitor interval seconds: default `300`, minimum `60`, maximum `3600`;
  - monitor jitter percent: default `20`, minimum `0`, maximum `50`;
  - optional allowed windows as local `HH:MM-HH:MM` ranges;
  - optional `stop_after_vinted_session_uses` per monitor, counting completed runs in the active monitor session that used the same `vinted_session_id`;
  - scheduler timezone, default `Europe/Madrid`;
  - optional UI-managed proxy profiles.
- Run event log:
  - `level`: `debug`, `info`, `warning`, or `error`;
  - stable machine phase such as `run_started`, `redis_check_error`, `anonymous_session_bootstrap_success`, `catalog_api_request_success`, `detail_fetch_error`, `item_discarded`, or `opportunity_created`;
  - `duration_ms`, `status_code`, `timeout_ms`, `attempt`, and `retry_reason` when available;
  - exact event timestamp and one non-interactive operational checklist entry per event in the PWA console;
  - safe cookie, token, HTTP session, and proxy sticky-session markers with name, length, `first4****last4` masked preview for long values, and fingerprint, never the full value;
  - egress diagnostic data collected through the same HTTP session/proxy, including IP and country when the diagnostic endpoint returns them.
  - accumulated history is loaded through REST; an SSE connection without a cursor starts at the current publication tail, while `last_event_id` query input takes precedence over the standard `Last-Event-ID` resume header;
  - the stream cursor is a monotonic publication position independent from `run_events.id`; it represents observed publication order rather than exact database commit timing, so transactions that become visible out of ID order remain deliverable exactly once to the PWA;
  - every persisted monitor event creates indexed outbox work in its own transaction; publication assigns the durable cursor and removes that work atomically without rescanning event history;
  - the SSE stream announces `stream_ready` with its initial cursor in both `id:` and JSON data plus a three-second reconnect delay, drains complete 100-event backlog batches without polling pauses, and emits both a transport comment and named cursor-neutral `stream_heartbeat` every 15 seconds while idle;
  - the SSE response disables intermediary caching/transformation and proxy buffering, and closes promptly after client disconnect while preserving the run-event redaction contract.
- Run telemetry:
  - total run duration is derived from persisted `started_at` and `finished_at` and is never duplicated as mutable state;
  - `runs.runtime_metadata` keeps the detail-fetch elapsed time plus the existing aggregate filter and persistence/opportunity timings;
  - proxied terminal HTTP events carry numeric curl transfer observations only, and the run aggregates them under `proxy_traffic_estimate` as observed request, upload-body, response-header and raw download-body bytes;
  - `total_observed_bytes` is the sum of those four curl counters. It is always labelled as an estimate because CONNECT/TLS framing, HTTP/2 header representation and provider accounting are outside the application boundary;
  - manual redirect hops are counted once each, transport failures without a response increase `unobserved_attempts`, direct runs declare no proxy consumption, and historical proxied runs without telemetry remain `not measured` rather than zero;
  - the runtime does not call a proxy vendor usage API and does not derive money from a hard-coded tariff. Provider usage remains the billing authority and may be reconciled separately.
- Database:
  - `app_settings`;
  - `search_sources.scheduler_config`;
  - `search_sources.monitor_mode`, `duration_minutes`, `monitor_until`, `next_run_at`, and `filter_definition`;
  - `monitor_sessions`;
  - `proxy_profiles`;
  - `runs.trigger` and optional `runs.monitor_session_id`;
  - `items` for opportunity items only;
  - `errors`.
  - `run_event_outbox`, with one pending row per committed monitor event until publication;
  - `run_event_publications`, with one monotonic stream position per persisted monitor event.

## Acceptance Criteria

- Scheduler can be disabled completely through the deployment gate `SCHEDULER_ENABLED`; the PWA has no redundant global enable switch.
- A monitor can be stopped without deleting it.
- A new monitor is inactive until launched.
- Manual start creates one sessionless baseline and opens one active session only after success; later explicit runs reuse that session until stop or fail-stop.
- Scheduler-triggered runs are not dispatched outside configured local-time windows. Session start performs only its internal baseline before scheduling later business runs inside the configured window.
- Time window UI exposes one start time and one end time; empty start/end means no daily window restriction.
- A bounded monitor started for N minutes stores `monitor_until = now + N minutes`.
- Launching a recurring monitor from the PWA uses the monitor's already persisted configuration, performs one sessionless zero-opportunity baseline while inactive and activates only after it succeeds.
- Activation persists `next_run_at` from the post-baseline activation timestamp, interval, jitter and allowed window. The scheduler treats this PostgreSQL value as authoritative over any in-process due-time cache, so start cannot enqueue or execute an immediate business run.
- Initial recurring admission is serialized with a PostgreSQL transaction-scoped advisory lock before capacity and egress selection. With capacity one, two concurrent starts produce exactly one `201` and one `409` without exceeding capacity.
- Recurring start revalidates scheduler availability/capacity after the baseline. Success commits the terminal baseline, active state, monitor session and future deadline together; a post-baseline `503`/`409` commits the successful baseline alone and leaves no active source, session or deadline.
- Starting an already active recurring monitor is rejected without changing its session, activation timestamp, deadline, or run history.
- The scheduler rechecks the persisted deadline after locking a due source and persists window deferrals independently from later capacity failures.
- With a 60-second interval and 10% jitter, the minimum interval floor makes the first post-activation due time 60 to 66 seconds after activation, plus scheduler tick latency.
- Every `Iniciar sesion` creates the Redis snapshot before activation; later manual and scheduler runs require it.
- Starting, preparing a Vinted session or probing detail is blocked when the saved URL contains catalog filters that cannot be translated to the fast API.
- `Guardar` is the only PWA action that persists monitor configuration.
- `Iniciar sesion` is disabled when the selected monitor has unsaved configuration changes and must not send `PATCH /api/monitors/{id}`.
- Monitor active state is controlled only by `POST /api/monitors/{id}/start` and `POST /api/monitors/{id}/stop`; monitor configuration `PATCH` rejects legacy `is_active` payloads.
- Active monitor configuration is read-only; after stop, both PWA and direct `PATCH` remain blocked until every `running/finalizing` run is terminal.
- Launching a recurring monitor is rejected when the effective scheduler is disabled or no scheduler capacity is available.
- Recurring activation requires a fresh heartbeat written by the scheduler producer itself. Missing, malformed, naive, implausibly future, or expired heartbeat state returns `503` and leaves the source, deadline, monitor session and runs unchanged.
- `GET /api/scheduler` exposes no persisted `enabled` field. It exposes `runtime_enabled`, `worker_available` and nullable UTC `worker_last_seen_at`; `effective_enabled` is true only when the deployment gate, capacity and the live producer are all available. `PATCH /api/scheduler` rejects the removed `enabled` input while retaining the other tuning fields.
- The PWA treats scheduler refresh failure as unavailable/unknown, discards any previously usable scheduler state, and blocks recurring launch. It never labels missing producer availability as a degraded operating mode.
- Invalid deployment scheduler configuration terminates worker startup. Once started, the worker supervisor terminates the process when its Redis probe fails or its own producer heartbeat expires; Compose owns restart and reports heartbeat health. Redis loss does not add another public readiness field: process exit stops heartbeat renewal and the existing timeout makes API/PWA availability false.
- The scheduler watchdog starts only after API health confirms API-owned migrations are complete. After its startup grace, an expired producer heartbeat locks active non-manual sources and rechecks liveness before changing them.
- If liveness is still absent after the lock, the watchdog makes PostgreSQL authoritative first: it clears active/deadline/duration state, closes the active monitor session with `scheduler_worker_unavailable`, and persists one sanitized warning event per stopped source. Manual monitors remain unchanged.
- Ready-task cleanup happens only after the PostgreSQL stop commits. Redis cleanup failure is logged visibly and never rolls back the inactive source; a later consumer must treat that inactive database state as authoritative.
- An unexpected watchdog iteration error terminates the watchdog process so Compose restarts it; it is not converted into a silent polling loop.
- A successful manual or recurring activation creates one open monitor session. Manual business runs reuse it; recurring runs associate with it until stopped, expired or failed.
- A recurring monitor with `stop_after_vinted_session_uses=N` stops automatically after the Nth completed run in that active monitor session that used the same `vinted_session_id`, and records `vinted_session_use_limit_reached`.
- The scheduler only considers active recurring monitors.
- Expired active monitors are stopped before scheduler planning.
- Jitter prevents fixed exact polling intervals.
- Scheduler failures are logged without stopping the worker.
- Invalid scheduler config is rejected clearly: interval outside `60..3600`, jitter outside `0..50`, `stop_after_vinted_session_uses` outside `1..1000`, malformed allowed windows, unsupported keys such as `pause_windows`, or an invalid scheduler timezone.
- No more than `2` monitor runs execute at the same time by default.
- The same active monitor never has two active runs at the same time.
- The scheduler uses only active healthy target-country proxies from the global pool.
- Proxy capacity is the sum of active healthy target-country proxy profile `max_concurrent_runs` values; there is no separate UI-level global per-proxy cap.
- When no eligible proxy capacity is available, manual/recurring start and later runs fail locally before run, prepared-session or provider creation.
- Manual and scheduler-triggered runs share the same Redis seen state, item identity, monitor dedupe, detail fetch, redaction, and error behavior.
- Manual and scheduler-triggered runs share the same URL-filter compatibility validation and fast API parameter translation.
- Manual and recurring start own their initial snapshot and never create opportunities from it. Scheduler-triggered runs require that session-start marker.
- Redis stores safe task and cache data: IDs, timestamps/deadlines, policy hash, counters/types and processing/seen markers. Candidate detail payloads and delayed detail retries are not persisted.
- Redis never stores cookies, tokens, HTML, raw Vinted payloads, proxy credentials, addresses, or payment data. Prepared Vinted cookies/tokens are stored only in the database encrypted with the local app secret.
- Run logs show operational progress with sanitized URLs, request headers after redaction/masking, response headers after redaction/masking, status codes, per-request durations in milliseconds, egress mode, proxy profile id when used, auth mode, IP/country from the egress diagnostic, filter snapshot, Redis/cache decisions, candidate decisions, persistence decisions, opportunity outcomes, and safe counts only.
- Run logs show the translated fast API parameters and URL filter compatibility in safe structured details.
- Run logs never expose raw cookie, token, authorization, proxy credential, HTML, or raw Vinted payload values. Cookie/token/session data is represented only as masked/fingerprinted markers; short values show no characters.
- Persisted event details have one read contract for REST and SSE: strict safe markers survive their JSONB roundtrip under marker containers, sensitive fields and sanitized headers, while caller-forged marker shapes and raw secret canaries are redacted before persistence and cannot reappear on either transport.
- Run logs show Redis availability, seen-cache hits/misses, seen-cache marks, detail fetch start/success/error/skipped, filter pass/discard, item persisted/reused, and opportunity created/skipped events.
- Run logs show catalog session context checks before the API request: impersonate, CSRF, anon id, access token, DataDome cookie, `v_udt`, locale, viewport, Vinted `x-screen`, egress country match, and any missing required key.
- Run logs show Vinted session lifecycle decisions: selected existing session, automatic preparation start/end, proxy sticky marker, probe outcome, use count, max requests, stop-after-use limit, session end reason, and recovery action.
- Run log timestamps are assigned per event and must not reuse a transaction-wide database timestamp.
- A rolled-back monitor event leaves neither an event nor outbox work. A committed event leaves exactly one pending outbox row until a serialized publisher atomically creates its unique publication cursor and removes the pending row.
- Migration 0017 backfills only committed monitor events missing a publication. Runtime publication reads bounded indexed outbox batches; it does not perform a historical anti-join on every SSE poll.
- Tail startup drains only the outbox rows visible in one repeatable PostgreSQL snapshot while holding the global publication lock, so a continuously active producer cannot prevent `stream_ready`. Events committed after that boundary, including a previously reserved lower event ID, receive later cursors and remain resumable.
- Normal SSE polls try the publication lock without waiting. Contention yields an empty poll so heartbeat and disconnect checks continue; a later poll publishes the pending rows after the tail fence is released.
- The Monitors view owns exactly one SSE connection while it is open. Renders and statistics refreshes do not recreate it, leaving the view closes it, and returning or reconnecting resumes from the last received publication cursor.
- Historical REST loading starts only after `stream_ready`; explicit per-monitor history-loaded state is independent from live event presence. REST history and live events are merged by event ID, including live events received while the historical request is still pending; each event appears at most once.
- Only `run_succeeded` and `run_failed` schedule a debounced runtime refresh. A terminal batch refreshes current sources, the affected monitor run histories and statistics once; opportunities refresh only when a terminal reports a positive `opportunities_created` count or omits that count. It does not refresh the unused global run list.
- A terminal batch already received remains pending across navigation away from Monitors and is applied or retried without requiring another terminal event.
- The monitor log follows the newest event while the reader remains at the bottom. Scrolling upward suspends forced scrolling and exposes a new-event control that returns to the tail on desktop and mobile.
- An SSE error or 22.5-second silence is presented as a reconnecting state; the dashboard closes the failed/quiet instance, performs one bounded authentication revalidation and creates one replacement after three seconds with the latest explicit publication cursor. The liveness timer starts during `CONNECTING` and is rearmed by `open`, `stream_ready`, `stream_heartbeat` and valid monitor events. If that replacement also fails during a prolonged outage, it may schedule the next sequential attempt; only one timer, one auth check and one current connection may exist at any instant.
- Every `open`, `error`, `stream_ready`, `stream_heartbeat` and `monitor_event` callback is scoped to the `EventSource` instance that registered it. Once replaced or closed, stale callbacks are inert: they cannot change status/readiness, advance the cursor, append events, close the current stream or schedule another reconnect.
- Run logs show `baseline_snapshot_seeded` when session start seeds the initial catalog snapshot and `baseline_required` when an ordinary run is blocked because that snapshot no longer exists.
- Run configuration logs identify the evaluation contract, policy hash, description-only filter scope, detail mode, early-filter mode and head byte limit. Detail/filter logs expose received bytes, match counts and durations without response content.
- Run cards expose total duration, detail-fetch elapsed time, filter time, combined item/detail persistence plus opportunity time, and estimated proxy traffic. The combined persistence label must not imply that opportunity creation alone took the whole interval.
- Rejected HTTP responses use a safe body observation containing lengths and type flags; response body snippets are never persisted or returned.
- The PWA Monitors view renders selected monitor accumulated logs as a non-interactive operational checklist: one wrapped multi-line block per event with run id, exact time, state, label, method, URL, status, ms, recovered/missing context, safe cookie flags, API parameters, and failure/skip reason when available, whether the monitor is active or stopped.
- Redacted JSON `run_events.details` remains available through API/database for technical audit, but the main PWA log timeline does not render expandable JSON details.
- The selected monitor log console supports local level filtering and text search without mutating persisted `run_events`.
- The selected monitor log timeline is collapsed by default so it does not dominate monitor detail. Its native summary toggles the complete existing timeline without pausing event collection; selecting another monitor starts collapsed again. `Limpiar vista` remains inside the expanded panel, stores the currently visible event IDs as hidden in the browser session and never deletes persisted `run_events`.

For manual opportunity-pipeline diagnosis, preserve the run id and the events for configuration, catalog response, Redis seen result, each candidate detail/early-filter result, filter decision, persistence/opportunity result, Redis terminal transition and final run status. Export only API/PWA-redacted events; never attach `.env`, raw cookies, proxy URLs with userinfo, response bodies or complete HAR files.
- The PWA Monitors view is organized as three top-level cards: new monitor configuration, the single compact monitor table, and the selected-monitor detail. The table and detail are stacked instead of nested inside a parent card.
- Active monitors appear before inactive monitors in the PWA's single compact monitor table, using status chips and row styling instead of separate active/inactive sections, and show a selected-monitor detail with session summary, read-only configuration, performance card, logs, and a working stop control.
- Active recurring monitor detail does not show `Ejecutar ahora` because periodic execution is already configured; active manual detail shows exactly one explicit `Ejecutar ahora`.
- Every non-archived monitor can be selected from the compact monitor table to show one performance block directly below its identity: accumulated monitor metrics first, explicitly including the active session, followed by the same business metrics for the active session or latest closed session. `Encontrados` is the post-deduplication candidate count and `Oportunidades` the committed passing count; no raw-catalog, redundant-new or filter-diagnostic item counter is shown as a product result. Configuration remains editable only while stopped and without a non-terminal run; the default all-history full-width bar chart of `items_found` remains accumulated and its range controls affect only that chart.
- Monitor detail views with no sessions yet show no session/acumulated metric rows until the first launch produces data.
- The performance chart supports fixed operational ranges labeled `Minuto`, `Hora`, `Dia`, `Mes`, and `Todo`.
- Fixed performance chart ranges use deterministic current-period buckets: current minute by 5-second bucket, current hour by 5-minute bucket, current day by 1-hour bucket, and current calendar month by 1-day bucket.
- The month chart runs from day 1 at 00:00 to day 1 of the following month at 00:00, with the final visible X-axis mark at the next month boundary.
- The all-history performance chart range uses automatic buckets: 5-minute buckets up to 1 hour of history, 1-hour buckets up to 24 hours, 1-day buckets up to 90 days, and 1-month buckets after that.
- The performance chart labels the X axis as time and the Y axis as found items, and its tooltip shows the exact bucket interval plus found/run counts.
- The performance chart renders each bar as the exact bucket interval from `bucket_start` to `bucket_end`; bars must not be centered on bucket midpoints or rely on automatic categorical bar width.
- The performance chart draws a vertical marker for the active session start when it falls inside the visible range.
- Idle inactive monitors appear after active or draining monitors in the compact monitor table; selecting an inactive monitor shows editable configuration and launch/archive controls only when no run is non-terminal, plus historical performance and archive confirmation without implying the monitor is running.
- The PWA can receive monitor log updates from the existing SSE stream.
- The PWA monitor detail follows the catalog URL contract in spec 001: it distinguishes URL-applied filters, effective application-controlled order/page, parameters with no runtime effect and unsupported filters; only the unsupported group blocks session start and other traffic-producing actions.
- Redis hits avoid DB item lookups and detail fetches for already seen monitor candidates.
- Candidates with an already existing opportunity for the same monitor are marked seen and skipped before filter/detail work if Redis lost that seen state.
- If Redis is unavailable, the affected run fails and the monitor is stopped/blocked until retried.
- Anonymous public cookies/tokens are encrypted at rest only in prepared Vinted sessions and are isolated per monitor plus proxy sticky identity.
- Proxy settings are global; monitor-level proxy selection is not exposed or accepted.
- Proxy profile creation/editing accepts proxy connection data, country, strict sticky username template and sticky TTL. `locale`, `Accept-Language`, viewport and Vinted `x-screen` are not user-editable API/PWA inputs and are recalculated from internal presets when the country changes.
- Explicit development diagnostics may construct a direct transport outside the PWA, monitor API and queue; they are not monitor runs and cannot be selected as fallback.
- Worker retry attempts, browser impersonation, human delay ranges and DataDome challenge penalty are deployment settings and are not editable from the PWA. Sticky username format and maximum sticky lifetime are persisted per proxy profile and editable from Settings.
- Proxy passwords stored through the UI are encrypted at rest. The username remains in plaintext and is returned raw by the current API even though the PWA renders its mask; 14.12.8 closes that credential-contract gap.
- Proxy pool entries can be `own`, `datacenter`, or `residential`; target-specific/special proxy classes are not exposed for Vinted.
- If a proxy request fails, only the affected run/source is failed and logged with redacted details.
- Repeated items in the same monitor cannot generate duplicate opportunities.
- A globally known item can still create an opportunity in a different monitor if that monitor sees it.

## Verification

- Unit tests for next-run calculation.
- Unit tests for activation-time persistence of the first recurring deadline, the 60-second jitter floor, and persisted-deadline precedence over stale scheduler runtime state.
- PostgreSQL/API tests for missing, fresh, expired, malformed, naive and future producer heartbeat plus mutation-free recurring `503`; producer tests cover heartbeat during disabled/idle operation and scheduler polls longer than the heartbeat interval.
- Supervisor/watchdog tests cover invalid startup configuration, producer expiry grace, recurring-only locked stop, heartbeat recovery during lock acquisition, session/event persistence, DB-first Redis cleanup failure, and unexpected-error process termination.
- Real-container verification blocks producer progress without external traffic and observes worker exit plus Compose restart; a separate disposable Redis-loss case observes a non-zero worker exit/restart, unchanged expired heartbeat, live API/PWA unavailability and recovery after Redis returns. A QA recurring source/session/task proves watchdog database stop and visible Redis-cleanup failure, followed by complete cleanup and service restoration.
- SSE contract tests for tail startup, query/header cursor precedence, duplicate-free resume, backlog batches larger than 100, reconnect advice, heartbeat, disconnect, buffering/cache headers, and redaction.
- Real PostgreSQL/API verification persists one legitimate marker event and one forged/raw-canary event through the production writer, then confirms identical safe `details` through monitor REST and SSE and complete cleanup of event, outbox, publication and source state.
- PostgreSQL/outbox tests cover event commit and rollback, concurrent/inverted commits, serialized duplicate-free publication, atomic publication rollback, bounded batches, historical migration backfill and tail high-water behavior.
- PostgreSQL tests for inverted event commit order, monotonic duplicate-free publication, JSONB marker roundtrip, atomic activation rollback, concurrent initial admission at capacity one, repeated start rejection, locked-deadline revalidation, and durable window deferral.
- Unit tests for interval, jitter, allowed-window, and disabled-source validation.
- Unit tests for concurrency limit and per-source single-flight behavior.
- Unit tests for Redis hit, miss, processing lock, seen mark, policy-hash reevaluation, and Redis-unavailable failure.
- Unit tests confirming Redis cache contents do not include cookies, tokens, raw payloads, HTML, or proxy credentials.
- Live Playwright check through PWA/API/PostgreSQL/Redis plus the real scheduler queue/consumer with a 60-second interval and 10% jitter: one baseline, no immediate business run, later deduplicated scheduler runs, initial persisted deadline in `60..66` seconds, terminal ACK and bounded loopback-only cleanup.
- Confirm run records identify scheduler-triggered executions.
- Confirm a manual baseline is sessionless, its successful start opens one session, later manual runs reuse it, and stop/fail-stop closes it; confirm recurring session ownership separately.
- Confirm monitor stats aggregate session, historical, and chart bucket data.
- Confirm selecting inactive monitors still shows the all-history chart, accumulated counts, and accumulated log timeline after manual or stopped recurring runs.
- Confirm `Limpiar vista` hides the selected monitor's visible timeline without deleting events from `/api/monitors/{monitor_id}/events`, and new event IDs remain visible after the cleanup.
- Confirm monitor logs include run configuration resolution, selected egress, HTTP session creation/close, egress IP/country when available, Redis decisions, candidate evaluation, detail requirements, filter decisions, item persistence/reuse, opportunity outcomes, exact timestamps, and request durations in milliseconds.
- Confirm safe cookie/token/session/proxy markers use masked/fingerprinted values only and never include raw secret values.
- Confirm the PWA log console renders one non-interactive wrapped checklist block per event, level filtering, text search, no visible JSON details, and no horizontal overflow on mobile.
- Confirm the Monitors view renders three top-level cards for creation, list, and selected detail without nesting the table or detail inside another card.
- Confirm the compact monitor table selects active and inactive monitors, updates the full-width detail panel, and scrolls the detail into view on mobile without horizontal overflow.
- Confirm active monitor details show read-only configuration and stop/log controls; manual mode also shows one `Ejecutar ahora`, while recurring modes do not expose a manual override.
- Confirm inactive monitor launch is blocked while there are unsaved changes, save persists the configuration, and launch then starts using that persisted configuration without another PATCH.
- Confirm manual and recurring start create one zero-opportunity snapshot before activation; recurring activation exposes one later deadline and no immediate business run/task.
- Confirm a recurring monitor configured with `stop_after_vinted_session_uses=1` stops after one completed run, logs the limit, and leaves the encrypted Vinted session history available for diagnosis.
- Confirm inactive monitor start and other traffic-producing actions are blocked when URL filters are unsupported.
- Confirm idle inactive monitor details show editable configuration above the performance chart and use an in-app archive confirmation dialog; drain keeps both unavailable.
- Confirm two different monitors can run concurrently up to the global limit.
- Confirm a third due monitor waits when the global limit is reached.
- Confirm no monitor API or PWA path exposes proxy selection per monitor.
- Confirm proxy API/PWA writes reject manual `locale`, `Accept-Language`, viewport and Vinted `x-screen` fields while read views expose only the resolved context for diagnostics.
- Confirm scheduler capacity reflects only active healthy target-country proxy capacity.
- Confirm periodic activation is blocked when scheduler is disabled or capacity is exhausted.
- Confirm every new business run records `egress_mode=proxy` with its proxy identity while historical `direct` metadata remains readable only as legacy evidence.
- Confirm a proxied run aggregates each completed curl response once, preserves upload/download/header/request components and category totals, marks response-less failures as partial, and never treats direct or historical unmeasured runs as zero proxy traffic.
- Confirm the PWA run card renders total and stage durations plus compact estimated proxy bytes without exposing raw transfer details or credentials; the API, PostgreSQL row and visible value agree.
- Confirm provider requests use the deterministic order `egress diagnostic` when configured, saved `/catalog?...` document URL, then `/api/v2/catalog/items`.
- Confirm repeated overlapping-monitor items use Redis monitor-scoped dedupe and do not duplicate opportunities within a monitor.
- Confirm Redis miss plus existing monitor opportunity is skipped before filters and logged as `candidate_existing_opportunity_skipped`.
- Confirm a no-proxy start/run returns a visible local capacity error and creates no run, prepared session or provider.
- Confirm proxy credentials are never returned or logged.
- Confirm anonymous session refresh failure marks only the affected run failed and does not stop the scheduler loop.
- Confirm redaction tests cover nested details, URLs, bearer tokens, cookies, token-like assignments, masked values, and fingerprints.
- Confirm PWA build succeeds after adding the log timeline and stream event fields.
- Confirm Playwright observes one pending SSE request while Monitors remains open, no repeated REST traffic while idle, sub-two-second single delivery, one directed terminal refresh, cursor resume after navigation, and tail-follow/new-event behavior on desktop and mobile.
- Confirm Playwright restarts the live API while Monitors is open, keeps at most one current replacement SSE with `last_event_id`, proves late callbacks from a closed request do not change cursor/events or create another stream, then navigates away/back and renders one backend-produced local event exactly once.
