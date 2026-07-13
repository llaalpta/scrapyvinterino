# ADR 0002: Practical Spec Driven Development Workflow

## Status

Accepted; revised 2026-07-13 to replace the universally serialized planning/audit workflow.

## Context

The project spans scraping, API, worker, PostgreSQL, Redis, Docker and a private PWA. SDD and real integration checks corrected genuine scheduler, SSE, authentication and concurrency defects.

The first workflow revision then treated every audit finding as another mandatory, linearly dependent roadmap task. Two nominally granular changes reached 53 files/+2,942 lines and 24 files/+2,209 lines; the latter added a 1,518-line race suite. Thirty-two pending `Now` items placed documentation maps, exactly-once crash recovery and production security ahead of user-visible alerts.

The actual product target remains a personal, single-user local scraper. Manual maintenance and relaunch are acceptable; unattended 24/7 and exactly-once recovery are not current requirements.

## Decision

Keep SDD, one-outcome branches, current documentation, integration-first acceptance, implementer self-review and an independent audit, but apply them proportionally.

Classify work as:

- micro: mechanical, no behavior/schema/coordination;
- standard: one observable outcome, up to three boundaries/criteria and one representative QA setup;
- program: multiple outcomes or QA setups, planned and split before product code.

Only programs require a separate `plan/<scope>` branch. One task confirmation authorizes its complete lifecycle through automatic non-destructive merge to `develop` after a positive independent audit and a green remote gate. Starting the next task remains separately user-gated. Real dependencies must be merged first; unrelated tasks are ordered by product value rather than a global serial chain.

Use one representative real integration scenario plus focused unit matrices. Full regression runs occur at most once and only when shared risk warrants them. Approximate file/line/time thresholds trigger a scope checkpoint, not additional ceremony.

The independent audit runs automatically after implementer self-review and is limited to the diff, owning contract, real evidence and two declared risks. A positive verdict gates the closure commit, normal push and PR creation; normal merge follows automatically once the PR is mergeable and its configured required checks pass. An unavailable reviewer or unresolved verdict blocks publication. The audit blocks reproducible acceptance/security/data failures, may accept small same-outcome hardening and must not expand the branch for adjacent or speculative findings.

For the current local operating model, process restart and best-effort queue recovery are sufficient. Ambiguous failures may require visible manual relaunch. Durable exactly-once ledgers, coordinated drain, AOF recovery, production restart policy, CSP and durable login abuse controls require a later explicit product decision.

`AGENTS.md` is the concise non-negotiable index, `docs/sdd-process.md` owns workflow detail, feature specs own acceptance and `docs/roadmap.md` remains a short priority queue.

## Consequences

- Real integration evidence and fail-stop behavior remain mandatory when the task promises service coordination.
- Small changes avoid planning branches, irrelevant containers, whole-repository audits and repeated full suites.
- A task that grows beyond one outcome is split instead of being made "atomic" only by name.
- Audit findings do not automatically create `Now` work; normal-use value, secret/data safety or a selected deployment target must justify promotion.
- The roadmap can move to Telegram value after a small local-reliability gate instead of completing production/distributed hardening first.
- Manual restart/relaunch, rare crash duplicates and best-effort recovery are explicit accepted risks for the personal local MVP.
- Published Git history and branches remain preserved while the process and backlog are simplified.
