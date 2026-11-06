# Runbook: rollback drill (timed)

BUILD-SPEC.md 14.6 ("rollback drill: executed end to end, under 5 minutes"), 16; `careflow/drills.py`; `ops/predeploy-evidence.yaml`. **Status: the procedure is drafted; the drill has NOT been run.** Running it needs real hosting, which is the owner's. Do it against the staging or dev deployment, never against production data.

**What a rollback is here:** go back to the previous application version and switch the new work off. **Migrations are never rolled back** (merged migrations are never edited): a database change is fixed forward with a new migration, and the previous application version must keep working with the current schema (the schema only ever adds).

**The recorded steps, in this order** (the names the checker expects):
1. `flags_off`: turn off the `FEATURE_*` flags added since the last good release.
2. `previous_version_deployed`.
3. `readiness_confirmed`: `GET /readyz` and the health route answer.
4. `smoke_checks_passed`: sign in as a test staff account; open the review queue; a read-only route answers.

Write down the start time, then for each step when it started, when it ended and whether it passed. Do not skip the order and do not run steps in parallel. Stop and call it a failed drill if any step fails or the total reaches 300 seconds.

Put the record in a drill file (the shape is in `tests/test_drills.py`). `careflow.drills.rollback_evidence` turns it into the claim for `ops/predeploy-evidence.yaml` (`rollback_drill`). A record that is incomplete, out of order, failed or too slow produces **no** claim and says why.

**Never** claim the drill from a dry reading of this page, a run on someone's laptop, or a record without the evidence pointer.
