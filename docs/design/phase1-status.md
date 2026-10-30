# Phase 1 status (BUILD-SPEC.md Appendix D)

**Updated 2026-10-30 to the current truth.** Phase 1 is STILL NOT COMPLETE; what changed since the 2026-10-24 audit: PR #1 and the later PRs (#2 to #8) are merged; main's migrations run to 0024 (0025 is in PR #9); the live suites that were "not yet run in CI" now run and pass on every shard (PR #8's run: 224 + 318 + 157 = 699 live tests, plus 1257 offline); every route in the served OpenAPI document has its role-matrix case; human decisions write `audit.log` rows; the staff-account runbook, `/v1/iam/me` clinic fields and CORS exist for the frontend. What still blocks completion is unchanged in kind: the patient portal backend, Priya's `STRONG_IDENTIFIERS` sign-off, the step-up claim spike, clinician-authored red-flag rules, hosting and an India region, and a key-store decision for production. See the "Update 2026-10-30" section at the end.

Owner decisions of 2026-10-24, recorded against the Phase 1 close-out audit (a read-only spec-compliance review of the repository on branch
`ci-fixes-d3-d4`, PR #1, CI green with 967 live tests and 0 skipped). **Phase 1 is not complete.** Each item below is one of: **built**,
**accepted** (a deviation the owner accepted, with the reason), **moved** (to a named later package), **blocked** (waiting on a person), or
**open**. Nothing here has been verified against a real patient or a real clinician-authored rule.

Appendix D, Phase 1: "Scheduling ... fully working via the staff console and a minimal patient portal; MPI with its matching, encryption,
and merge/unmerge, including the actual key store and rotation runbook; the red-flag rule engine (deterministic layer only) with its own
gold-set regression tests; the transactional outbox and the Postgres-backed queue (7.4) with the ceilings from 7.3 enforced and tested."

## Scheduling

| Item | Status | Evidence or next step |
|---|---|---|
| Slots, holds, GiST exclusion, idempotent booking, waitlist (backend) | **Built** | `careflow/scheduling.py`, migrations 0002 and 0004, `tests/test_scheduling_live.py`, `tests/test_rls_mpi_scheduling_live.py` |
| Staff API routes | **Built** | `/v1/staff/scheduling/*`, role-matrix covered; draft/send offer routes are in PR #9 |
| Staff console (frontend) | **Open**, approved in principle | Frontend packages F0 to F5, in that order, local development only. No package is installed until the owner approves an exact list |
| Minimal patient portal | **Blocked** on a backend package | Needs patient-facing routes and RLS policies, the `iam` consent, activation and grievance tables, guardian binding, and the SMS provider path (Appendix B, MSG91 Send-SMS-Hook). Frontend F6 to F9 wait for it. Scheduling of that package is the owner's decision |
| 200 concurrent bookings of one slot resolve to exactly one (BUILD-SPEC 21; 14.3 hard gate) | **Built, run in CI (green)** | `tests/test_scheduling_concurrency_live.py` |
| Concurrent duplicate hold and booking retries replay the original | **Built, run in CI (green)** | same file |
| Clinic isolation for child tables | **Built, run in CI (green)**; the finding it made is **fixed** by migration 0014 | `tests/test_rls_child_tables_live.py`; see "Finding: unpinned foreign keys" below |
| Slot sweep and waitlist backfill running on their own | **Moved** to P2-1 | They need the tick (see Queue) |

## Identity (MPI)

| Item | Status | Evidence or next step |
|---|---|---|
| Matching, encryption, merge and unmerge | **Built** | `mpi.py`, `identity.py`, `crypto.py`, migrations 0008 and 0009, routes behind step-up |
| `STRONG_IDENTIFIERS` | **Blocked** on sign-off by Priya (Health-information lead) | Reachable over HTTP through `POST /v1/staff/patients`. **No real patient data until the sign-off is recorded** |
| Step-up claim shape (`aal`, `amr`) | **Blocked** on the owner's spike | `docs/design/step-up-spike.md`. The merge routes read an unverified shape; the check is not verified until a real token is inspected |
| Staff second-factor enrolment at login | **Moved** to frontend F2 (the owner decided it is enforced there) | |
| `aal2` database policy | **Deferred**, tracked | `docs/design/deferred-items.md` |
| Key store | **Accepted**: environment variables for now | `CAREFLOW_ENC_SECRET_V<n>` and `CAREFLOW_HMAC_SECRET_V<n>` via `DemographicVault.from_env`. BUILD-SPEC 17.2 names the hosting provider's secret manager; nothing in code or tests loads from a real one. **Revisit when hosting is decided** |
| Rotation runbook | **Built** | `docs/runbooks/key_rotation.md`, `careflow/rotation.py`, `scripts/rotate_keys.py`, `tests/test_rotation_live.py`. No drill at realistic volume has been done |
| Labeled-pair merge gate (14.3: no wrong merge on labeled identity pairs) | **Moved** to Phase 5 | Needs clinician-reviewed labels. Tracked |

## Red-flag rules

| Item | Status | Evidence or next step |
|---|---|---|
| Deterministic rule engine (mechanism) | **Built** | `careflow/rules.py`, migrations 0007 and 0013, `tests/test_rules.py`, `tests/test_rules_live.py` |
| Gold-set regression tests and the 100% recall gate (14.3) | **Blocked** on clinician-authored rules | `kb.red_flag_rules` holds only an inert `draft_unreviewed` stub. No rule, phrase or pattern may be written by an engineer (`CLAUDE.md` rule 1, BUILD-SPEC 20.2). Phase 1 cannot be called complete until clinicians supply and approve rules |
| Emergency reply template wording | **Blocked** on clinicians | Nothing sends a reply; the screening only records that one is pending |

## Queue and ceilings

| Item | Status | Evidence or next step |
|---|---|---|
| Transactional outbox and `QueuePort` over Postgres | **Built** | migration 0006, `careflow/queue.py`, `tests/test_queue.py`, `tests/test_queue_live.py` |
| Ceiling: 5 attempts then dead-letter | **Built** | `MAX_ATTEMPTS = 5`, a database CHECK, tests through the real pooler |
| Ceilings: 40 steps, 25 tool calls, 12 model calls (warn at 8), 40 s wall-clock per tick | **Moved** to Phase 2 package P2-1; **built there** (`careflow/budget.py`, `tests/test_budget.py`, `tests/test_tick.py`) | The red-flag path and safety net are never budget-limited (a SAFETY call class, tested). Enforced inside the tick and the gateway; no agent runs yet |
| Scheduled minute tick and immediate poke on enqueue (7.4) | **Moved** to P2-1; **built there** (`careflow/tick.py`) | In-process, off unless `CAREFLOW_RUN_WORKER` is set. No handler is registered yet, so a job is dead-lettered visibly as "no handler"; no route calls the poke yet |
| Second, unwired Redis/SQS adapter stub (7.4) | **Accepted deviation** | Appendix A rejects a message broker for this workload (jobs wait days; a broker adds a second always-on service and a split-brain risk with the database). The `QueuePort` interface keeps a later adapter possible. Not written. Revisit only if volume ever demands it |

## Finding: unpinned foreign keys (child-table isolation) — FIXED by migration 0014

Every clinic-scoped table carries `clinic_id` itself (the only tables without it are `iam.clinics` and `iam.accounts`), so the spec's "child
table with no clinic column" does not exist. The realistic gap was a child whose foreign key did not pin the clinic. Three single-column
foreign keys did not: `sched.bookings.hold_id`, `mpi.merges.candidate_id` and `ops.outbox.job_id`. A caller who knew another clinic's id
could make their own row point at it (an integrity gap and a cross-clinic existence probe, not a read leak).

`db/migrations/0014_pin_clinic_on_child_foreign_keys.sql` replaces them with composite `(child_id, clinic_id)` keys, backed by
`unique (id, clinic_id)` on `sched.holds`, `mpi.match_candidates` and `ops.queue`. Origin is not part of these keys: the sibling keys in
`sched`, `mpi` and `ops` pin the clinic only. Checked before writing it: the dev database holds no booking, merge or outbox row that points
at a hold, candidate or job at all (counts 0, 0, 0), the default constraint names exist as the migration expects, and the migration itself
re-checks and stops with counts if a database ever held a violating row. Reviewed by the RLS reviewer (SAFE TO MERGE: no policy or grant
changes; the foreign-key probe across clinics is closed). `tests/test_rls_child_tables_live.py` now has `KNOWN_GAPS` empty and a refused
cross-clinic insert for each of the three.

## Hosted infrastructure facts the owner still has to settle

- Hosting provider and an India region (BUILD-SPEC 16.5). Dev and CI are Tokyo and synthetic only.
- Patient-portal backend package scheduling.
- LLM provider choice (Phase 2).

## Open items waiting on the owner or clinicians

Step-up claim spike; `STRONG_IDENTIFIERS` sign-off; LLM provider; hosting and India region; patient-portal backend scheduling; clinician
inputs (red-flag rules, emergency reply template, `unassessable` task priority and resolution path, real priority levels, protocol cards,
a clinician-reviewed referral gold set, counsel on advertising, privacy notice and under-18 rules); the owner's own clean-up (`Careflow.zip`
and the `.env.cli` keys, scratch files) and merging PR #1.

## Update 2026-10-30 (what is true now)

- **Merged to main (PRs #1 to #8):** scheduling, MPI, rules mechanism, queue and ceilings, the Phase 2 stack (extraction, grounding, retrieval, decision tier with fakes, the Front Door graph, triage), blocker 1 (0022), planted-token option A (0023, 0024), CORS and `/v1/iam/me` clinic fields, audit rows for human decisions, the pooler graph test, Phase 3 slice 1 (mechanism only), demo placeholders.
- **In PR #9 (CI running):** identity-confirmation revoke (0025), review-task routes, Phase 3 slice 2 (mechanism only).
- **CI facts:** three live shards (60 min limit; PR #8: 37, 46 and 47 minutes), serialised per shard on the one shared CI database; every route has a role-matrix case; the migration ledger is checksummed and checked in the migrate job.
- **Phase 1 items still open, unchanged in cause:** minimal patient portal (backend package not scheduled); `STRONG_IDENTIFIERS` sign-off; step-up claim shape spike; clinician-authored red-flag rules and the 100% recall gate; emergency reply wording; hosting and an India region; production key store (env variables accepted for now); labeled-pair merge gate (Phase 5); the production secret manager.
- **New pre-launch items since the audit** live in `phase2-status.md` (pre-launch blockers 1 to 12).
