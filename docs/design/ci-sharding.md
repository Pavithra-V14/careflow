# CI: the live job outgrew one job (evidence, safety check, proposal)

Written 2026-10-24 after run `40087935071` (PR #1, commit `3e1c4ff`) hit the 90-minute limit.

**Status (owner approved Steps A, B and C on 2026-10-24): implemented on branch `next-backend`.**
- Step A: every whole-table count in the live tests is scoped to the test's own clinic (commit message lists each test; no assertion weakened).
- Step B: `.github/workflows/ci.yml` now has a `migrate` job, then a `live` matrix of three shards (60-minute limit each, a concurrency group
  per shard number, never cancel-in-progress), then an aggregate job named `test` (the required check keeps its name).
- Step C: `tests/test_ci_shards.py` fails if a live file is in no shard or two, if the workflow loses the properties below, or if an unscoped
  whole-table count of a table another file commits appears.
- `scripts/ci_peak_connections.py` samples the database's backend connections while each shard runs and prints one `PEAK-CONNECTIONS` line;
  the first sharded run is the measurement and its numbers go in the report to the owner.
- PR #1 (`ci-fixes-d3-d4`) is deliberately untouched.

## Note: the 200-booking test hung in cancellation through the transaction pooler (and what bounds it now)

On 2026-10-24 `test_200_concurrent_bookings_of_one_slot_produce_exactly_one_booking` exceeded its own 300-second bound at 12:04:13. Its bound
used `asyncio.wait_for`, which on a timeout cancels the work and then WAITS for the cancellation to finish. The work was stuck on statements
queued behind one row lock, through Supabase's transaction-mode pooler. A cancel does not reach a statement waiting on a lock, and the rollback
queues behind it, so the unwind never completed and the job sat silent for 23 minutes until the 90-minute limit cancelled it. Now:

1. `tests/bounds.py` `bounded()` cancels, allows `CANCEL_GRACE_SECONDS` (20) to unwind, and raises regardless. It cannot itself hang
   (`tests/test_bounds.py` proves it against work that swallows cancellation).
2. Every transaction in that file runs `SET LOCAL lock_timeout = '120s'` and `SET LOCAL statement_timeout = '300s'` first, so the DATABASE gives up
   on a stuck wait (SET LOCAL lasts for the transaction only, so it is safe through the transaction pooler).
3. The test has its own 900-second bound (the 200 attempts are serial on one lock) and disposes its engine with a 30-second limit.
4. The semaphore (20 in flight) keeps any waiter behind at most 20 others, never behind all 200.
5. `faulthandler_timeout = 300` (pyproject.toml) still dumps all stacks if any test runs over five minutes, and each shard has a 60-minute limit.

## Known gap, accepted: a migrate job from another run can change the schema under running shards

The `migrate` job has its own concurrency group, separate from the shard groups. So a `migrate` job from run Y (another branch, or a newer run
of the same branch that did not cancel the older one) can apply NEW migrations to the shared CI database while run X's shards are still
executing against the old schema. A group cannot cover it without serialising the whole gate again. It is not covered by any group.
Accepted by the owner (2026-10-24) while one person pushes. If it ever bites, the symptom is a shard failing on a column or table that "does
not exist" or "already exists" in the middle of a run; re-running the shard after the other run finishes is the remedy. Two branches whose
migration sets differ (for example PR #1's branch has no 0014 to 0016) make it worse, not better: the ledger check in the later run would see
migrations its checkout does not have.

## The proposal as originally written (kept for the record; it is what was implemented)


## What the log shows

| Fact | Evidence (job log) |
|---|---|
| 926 tests passed, 1 failed, then the job was cancelled at 12:27:32 (90 min) | `PASSED` count 926, `FAILED` count 1, `##[error]The operation was canceled.` |
| Progress when it stopped: 94% | the last progress marker, after `tests/test_scheduling.py` |
| The last test that started | `tests/test_scheduling_concurrency_live.py::test_200_concurrent_bookings_of_one_slot_produce_exactly_one_booking`, at 11:59:13 |
| One faulthandler dump (a test over 300 s) | 12:04:13, exactly 300 s into that test; every thread idle, the main thread inside the asyncio event loop's `select` (waiting on the database), no frame of our code |
| Nothing at all after the dump | 23 more minutes of silence until the cancellation |
| `--durations` | not printed: the run was cancelled before pytest's end-of-run report |

## Slow or stuck: both, and they are different problems

1. **The suite is slow, and growing.** Up to 94% it ran 809 counted tests in 61.7 minutes. Per file it was about 35% slower than the green run of
   an hour earlier (role matrix 12.3 to 16.7 min, screenings 6.9 to 9.4, identity 5.3 to 7.2, queue 4.7 to 6.3). Hosted-database latency varies
   from run to run; the suite has no margin for it. Without the stuck test the same run would have needed roughly 75 to 80 minutes.
2. **One test is stuck, in its cancellation path.** The 200-booking test queues every attempt on ONE slot row lock, so the 200 attempts run one after
   another; each is several round trips to a database in another continent. It did not finish within its own 300-second bound. When that bound
   expired, `asyncio.wait_for` cancelled the work and then WAITED FOR THE CANCELLATION TO FINISH, and the cancellation never finished (work
   stuck on a statement through the transaction-mode pooler does not unwind on a cancel). That wait is the 23-minute silence.

## The fixes already made (branch `next-backend`; nothing pushed to `ci-fixes-d3-d4`)

- `tests/bounds.py` `bounded()` no longer waits for cancellation to finish: it cancels, allows 20 seconds to unwind, then raises regardless.
  Tested offline against work that swallows cancellation (`tests/test_bounds.py`).
- The 200-booking test sets `lock_timeout` (120 s) and `statement_timeout` (300 s) with `SET LOCAL` inside every transaction (safe through the
  transaction-mode pooler), has its own 900-second bound, and disposes its engine with a 30-second limit. A waiter queues behind at most 20 others
  (the semaphore), never behind all 200, so the lock timeout is far from reachable by a healthy run.
- **The one FAILED test is a test defect, not a schema defect.** `test_a_hold_cannot_point_at_another_clinics_slot_or_patient` expected SQLSTATE
  `23503` (foreign-key violation) but probed slots that already had an active hold, so the one-active-hold-per-slot unique index fired first with
  `23505`. The traceback was lost with the cancellation, so this is reasoned from the schema, not read from the log; the probes now use slots with
  no hold. The cross-clinic insert is still refused either way. **PR #1's branch still contains the defective test**; it needs this fix too.

## Safety check: can the live job be split into parallel jobs on ONE shared CI database?

Not freely. Many live tests commit rows and many run as the migrator, which sees every session's committed rows (the migrator bypasses RLS), and
several of those tests COUNT a whole table. They pass today only because the files run one after another and each cleans up. Committers and
whole-table counters, found by reading the tests:

| Table | Committed by | Counted globally by |
|---|---|---|
| `sched.holds`, `sched.bookings` | `test_api_role_matrix`, `test_scheduling_concurrency_live` | `test_scheduling_live` (holds == 1, == 0; bookings == 0, == 1) |
| `mpi.patients` | `test_api_role_matrix`, `test_mpi_live`, `test_identity_live`, `test_scheduling_concurrency_live` | `test_rls_mpi_scheduling_live` (`== 0`) |
| `mpi.merges` | `test_api_role_matrix`, `test_identity_live` | `test_identity_live` (`== 1`) |
| `agents.review_tasks` | `test_api_role_matrix` (unmerge raises a task) | `test_identity_live` (`== 0`), `test_rules_live` (`== 2`, `== 1`, `== 0`) |
| `ops.queue`, `ops.outbox` | `test_queue_live` | `test_queue_live` only |
| `ops.key_rotation_batches`, `msg.messages` | none in committed form | `test_rotation_live`, the message harnesses (before/after inside one test) |

Other findings that are SAFE across shards: the "no committed approved red-flag rule" precondition (nobody commits rules); the migration-ledger
tests (they use their own scratch schemas); the dedicated message harnesses (never commit); every test that uses random uuids for its clinics and
accounts.

**Concurrency groups.** The live job currently has the job-level group `careflow-ci-database` with `cancel-in-progress: false`. Three shard jobs
sharing that name would queue behind each other, and a third job REPLACES the pending second one (so a shard would be cancelled). Each shard needs
its own group name. Two runs of different branches could then overlap, shard 1 of one with shard 2 of the other: the same hazard as parallel shards
inside one run, so the partition must be safe for any two shards, not just within a run.

**Connections.** The matrix pool (up to 10), the 200-booking test (20), the queue tests (8) and the migrator sessions would run at the same time. The
pooler's real limit on the CI project is not known; a first sharded run is the measurement.

Result of the check: **splitting by file is only safe if each connected set of the conflicts above stays inside one shard.** Connected, that set
is {role matrix, identity, rules, scheduling, scheduling concurrency, mpi, rls_mpi_scheduling}, about 45 to 50 minutes at today's pace, so
an unmodified split cannot go below two shards of roughly 50 and 33 minutes.

## Proposal

**Step A (tests only, no workflow change): make the counting tests independent of other sessions' committed rows.** Scope each whole-table count
to the test's own clinic (or to its own ids), in four files: `test_scheduling_live` (6), `test_identity_live` (4), `test_rules_live` (5),
`test_rls_mpi_scheduling_live` (1). About 16 assertions; none is weakened (a scoped count is a stricter statement about the test's own rows,
and it stops depending on unrelated data). They are run serially first, in the existing single job, to prove they still pass.

**Step B (workflow): three parallel shards after a separate `migrate` job**, each its own concurrency group, each `timeout-minutes: 60`:

| Shard | Files (minutes at this run's pace) | Total |
|---|---|---|
| 1 | `test_api_role_matrix` 16.7, `test_identity_live` 7.2, `test_rules_live` 3.4, `test_api_screen_message_live` 1.8, `test_api_read_message_live` 1.8, `test_patient_binding_live` 0.6 | about 32 |
| 2 | `test_rls_screenings_live` 9.4, `test_queue_live` 6.3, `test_rotation_live` 4.9, `test_rls_msg_live` 3.2, `test_mpi_live` 2.1, `test_rls_iam_audit_live` 1.8, `test_migrate_live` 1.0, `test_api_real_token_live`, `test_rls_child_tables_live`, `test_rls_ai_registry_live`, `test_rls_spend_and_approval_live` | about 32 |
| 3 | `test_scheduling_live` 4.9, `test_rls_mpi_scheduling_live` 1.1, `test_scheduling_concurrency_live` (the 200-booking test: 6 to 15) | about 15 to 25 |

Every shard keeps `-v --durations=25`, the zero-skips check and `faulthandler_timeout`. Wall-clock for the live gate would be about 35 minutes.
No new package.

**Step C (a gate against silent loss): a test that reads `.github/workflows/ci.yml`** and fails unless every live test file (`tests/*live*.py` and
`tests/test_api_role_matrix.py`) appears in exactly one shard and the files of each conflict set in the table above are in the same shard (until
Step A makes that unnecessary). A file added to no shard can never go unrun.

**Not recommended: shrinking the 200-attempt count.** It is the spec's number (BUILD-SPEC §21); it gets its own shard budget instead.

Needs your decision: Step A first (I would do it on `next-backend`, run serially in CI, then Step B), or Step B with only two shards now.

## Rebalance of 2026-10-27 (owner decision), from the first PR #3 run

Measured on run 40113599668 (the committed workflow at that commit; hosted-database latency varies from run to run):

| Shard | Tests | pytest time | Wall time of the job | PEAK-CONNECTIONS (total / careflow_app / other) |
|---|---|---|---|---|
| 1 | 193 | 31:13 | about 31 min | 31 / 16 / 15 |
| 2 | 372 | **50:42** (limit 60) | about 51 min | 20 / 10 / 12 |
| 3 | 80 | 11:26 | about 12 min | 31 / 16 / 15 |

Shard 2 had grown with every new live file (ai_registry, spend, docs, triage, worker, hold, checkpoints) while shard 3 stayed small. The averages per test were 9.7 s (shard 1), 8.2 s
(shard 2) and 8.6 s (shard 3, whose time is dominated by two concurrency tests: the 200-booking test 296 s and the concurrent-holds test 75 s; its other 78 tests averaged 4 s).
Only the 25 slowest calls are in the log, so per-FILE times were estimated from the earlier per-file table above (screenings 9.4 min for 89 tests, rotation 4.9 min for 24 tests, scaled by
about 1.17 for today's latency) and from test counts.

**Change: `test_rls_screenings_live.py` and `test_rotation_live.py` move from shard 2 to shard 3.** Both are safe next to shard 3's files: the partition gate (`tests/test_ci_shards.py`) still
holds (every live file in exactly one shard, and no whole-table count of a table another file commits), `msg.*` and the rules table are never committed, and rotation is the only file that commits
`ops.key_rotation_batches`.

| Shard | Files | Estimated |
|---|---|---|
| 1 | role matrix (now with the committed half of the referral routes), identity, rules, screen message, read message, patient binding | about 33 min |
| 2 | queue, msg, mpi, iam_audit, migrate, real_token, child_tables, ai_registry, spend, docs, triage, mpi_worker, referral_hold, checkpoints, triage_writers | about 34 min |
| 3 | screenings, rotation, scheduling, rls_mpi_scheduling, scheduling_concurrency, api_referrals, dead_letters | about 33 min |

**So that the next rebalance does not guess:** every shard now writes `--junitxml=shard-times.xml` and prints `SHARD-TIMES` / `SHARD-TIME` lines (seconds, test count and file, slowest first) from
`scripts/ci_shard_times.py` in a step that runs even when tests fail. Compare the estimate above with those lines after the next run and move files again if a shard is over 40 minutes.


**Measured after the rebalance (run 40529226066, all green, total about 47 min):** shard 1 24:54, shard 2 42:21, shard 3 46:44 (pytest time). Shard 3 and shard 2 are now over the 40-minute line the previous paragraph set; the next rebalance should start from the `SHARD-TIME` lines of that run.

**One-off timing failure, recorded:** `test_concurrent_reads_from_different_identities_neither_mix_rows_nor_leak_connections` failed once with `QueuePool limit of size 5 overflow 5 reached ... timeout 30.00` (run 40521334349, shard 1: 40 concurrent reads queued behind a slow pooler) and passed unchanged in run 40529226066. No change was made to the test or to the app pool settings; if it recurs, check what the 40 requests wait on before changing anything.

**Timeout headroom (checked 2026-10-27):** every live shard has `timeout-minutes: 60`. In run 40529226066 the shard JOBS took 25:07, 42:32 and 46:55, so the slowest has about 13 minutes of headroom (more than the 10-minute line the owner set). No rebalance was made; the next one is due when a shard's headroom falls under 10 minutes (job over 50 minutes) or after the next set of `SHARD-TIME` lines.

## Leftover rows from killed runs (2026-10-28)

**What happened.** Run 40693737334 failed in shards 1 and 2 on tests that read a whole shared table (`agents.review_tasks`, `ops.queue`, `ops.outbox`) and expected exactly the rows they had just made. The most likely cause (not proven: nobody has inspected the CI database) is that the `main` run for the PR #3 merge was cancelled mid-test; a cancelled job skips its teardown and leaves committed rows behind. The workflow now never cancels a run triggered by a push to main (only pull-request runs are superseded).

**After the sweep, which tests can still be polluted?** Every live test now reads its own clinic's rows (random clinic ids). What is still global BY DESIGN:
- `tests/test_rotation_live.py` rotates and counts EVERY non-erased message at key version 1 across all clinics (`message_version_usage(db)` and `rotate_message_bodies(db, ...)` without `clinic_id`), and sums the global rotation ledger `where from_version = 1 and to_version = 2`. A committed message still on key version 1 in any clinic would break it. No live test commits messages today, so this needs an outside leftover to bite.
- The red-flag-rule precondition in `test_rules_live`, `test_api_screen_message_live`, `test_api_referrals_live` and `test_rls_screenings_live`: `kb` is not clinic-scoped, and the tests REFUSE TO RUN (a loud precondition, not a wrong pass) if a committed approved `origin = 'test'` rule with criteria exists.
- Committed fixtures of `tests/test_api_role_matrix.py` (clinics, accounts, patients, all `origin = 'test'` with random ids): tagged and harmless to the scoped tests; they accumulate if a run is killed before its teardown.
Not affected: advisory locks and open transactions die with the killed runner's connections.

**How to look, by hand, read-only** (a migrator connection to the CI project, never the dev project):
```sql
-- the one global dependency of the rotation tests: messages still on key version 1
select clinic_id, origin, count(*) from msg.messages where erased_at is null and body_key_version = 1 group by 1, 2;
-- approved test-origin rules with criteria (the loud precondition)
select count(*) from kb.red_flag_rules where origin = 'test' and status = 'approved' and (cardinality(phrases) > 0 or cardinality(patterns) > 0);
-- how much committed test data is lying around
select 'clinics' as t, count(*) from iam.clinics where origin = 'test'
union all select 'accounts', count(*) from iam.accounts where origin = 'test'
union all select 'queue', count(*) from ops.queue where origin = 'test'
union all select 'review_tasks', count(*) from agents.review_tasks where origin = 'test';
```
**How to clean it safely.** Do NOT delete rows by hand: most of these tables are append-only on purpose (triggers refuse `DELETE`, `UPDATE` and `TRUNCATE` even for the owner), so cleaning them means disabling those triggers, which is exactly what the guards exist to prevent. Order of preference: (1) do nothing; the scoped tests ignore other clinics' rows. (2) If the first query above returns rows (the rotation tests' only global dependency), the CI project needs those messages gone or rotated: ask before touching anything. (3) Last resort: recreate the CI Supabase project (it is separate from dev) and let the migrate job rebuild the schema from the migrations, then re-enter its connection secrets. No automatic delete is added to CI, by decision.

## Rebalance of 2026-10-29 (owner decision), from PR #5's first run (40719139488)

**Measured (job time / pytest time):** shard 1 30:54 / 30:40, shard 2 35:25 / 35:12, **shard 3 55:16 / 55:05** against the 60-minute `timeout-minutes`: under 5 minutes of headroom, so the shard had to be rebalanced. Shard 3 was slower than in the previous green run (46:55), partly from the new referral tests.

**Per-file seconds in shard 3:** test_api_referrals_live 1297 (27 tests), test_rls_screenings_live 614, test_scheduling_concurrency_live 608 (the 200-booking test alone 430), test_rotation_live 332, test_scheduling_live 327, test_rls_mpi_scheduling_live 70, test_dead_letters_live 55.

**Moves:** `test_rls_screenings_live.py` (614 s) shard 3 -> shard 1; `test_scheduling_live.py` (327 s) shard 3 -> shard 2. The `test_ci_shards` gate accepted both pairs; every live file is still in exactly one shard.

| Shard | Before | After (estimate) |
|---|---|---|
| 1 | 30:54 | about 41 min |
| 2 | 35:25 | about 41 min |
| 3 | 55:16 | about 40 min |

**The headroom rule (standing):** every live shard job must finish with at least 10 minutes of headroom against its 60-minute timeout. If any shard job takes more than 50 minutes in a green-or-red run, rebalance in the next change, before adding more live tests to that shard. Re-measure from the `SHARD-TIME` lines after the first run on the new split.

## Rebalance of 2026-10-29 (standing rule applied), from PR #6's run (40857011070)

Shard 1 took 50:11 (the role matrix alone 1428 s, test_rls_screenings_live 604 s); shard 2 39:07; shard 3 27:23. Shard 1 passed 50 minutes, so by the standing rule (a shard job over 50 minutes is rebalanced in the next change) `test_rls_screenings_live.py` moves from shard 1 to shard 3. Estimates after the move: shard 1 about 40, shard 2 about 39, shard 3 about 37 minutes. The `test_ci_shards` gate accepts the pairs. Note the spread: the same shard varied by 10 minutes between runs of the same code (30:54 to 50:11), so the headroom rule is applied to the worst run seen, not the average.
