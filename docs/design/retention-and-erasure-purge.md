# The purge: the exact role, function, grants and tests (DESIGN ONLY: no code, no migration, nothing applied)

Requested by the owner, 2026-10-31, following `retention-and-erasure.md` (shape approved 2026-10-30; periods NOT decided). **SHAPE APPROVED by the owner on 2026-10-31. NO CODE YET: the retention periods and the sign-off of the grants wait for the privacy owner and a clinician.** Retention periods stay REQUIRED settings with NO defaults.

## Owner conditions of 2026-10-31 (binding on whatever is built)
1. **No scheduled job for the demo.** The purge is run BY HAND by a person: always the dry run first, then the real run with the expected-count guard (`p_expected_max`, the number from the reviewed dry run). Section 6's scheduled job is a later decision and is not built for the demo.
2. **The role may not erase anything inside a retention period.** Every function refuses a cutoff that is not strictly older than the clinic's REQUIRED retention setting for that family (an unset setting = the function refuses; there is no default), in addition to the engineering floor of section 5. A row younger than the cutoff is never selected, whatever argument is passed.
3. **The role may NEVER touch audit rows or review decisions.** No function, grant, trigger exception or code path of the purge reaches `audit.log` or `agents.review_decisions` (nor `agents.triage_proposals`, `mpi.identity_confirmations`): their triggers stay exactly as they are, the exception of section 4 is written for the checkpoint tables only, and test 8 below proves those tables are byte-identical after a full run. The ONLY thing the purge writes to `audit.log` is its own one-row-per-call record (an insert; it never reads or changes an existing audit row).

## 0. What this note settles, and what it leaves open
Settles: the one role, the functions, the exact grants, how an append-only table allows a deletion that nobody else can trigger, what each function may erase and what it must never touch, who may call it, its audit trail, and the tests that prove it can touch nothing else.
Leaves open (owner / clinical lead): the retention periods (section 7), where the scheduled job runs (section 6), and the two-person patient erasure (a separate note; it will REUSE the mechanism of section 4 but is not designed here).

## 1. Scope: working state only
The purge deletes **agent working state** and nothing else:

| Family | Tables | Why deletable | Protected (never deleted) |
|---|---|---|---|
| Graph checkpoints | `agents.graph_checkpoints`, `agents.graph_checkpoint_blobs`, `agents.graph_checkpoint_writes` (0021) | Ids, fixed words, (card, version) pairs; never letter text | A thread whose document has an OPEN or ACKNOWLEDGED review task, or a triage proposal with no decision (a paused run); any thread newer than the cutoff |
| Queue | `ops.queue`, `ops.outbox` (0006) | Task type, payload ids | A job that is not finished; a dead letter whose review task is still open |

**Never in scope, for any role or function:** `audit.log`, `agents.review_decisions`, `agents.triage_proposals`, `mpi.identity_confirmations`, `agents.review_tasks` (resolved ones are archived, not deleted), `msg.*`, `docs.*`, every `iam.*` and `mpi.*` table, `kb.*`, `ai.*`. A resolved review task and a human decision record why a person decided: they are clinical record (`retention-and-erasure.md` section 1).
Whether `ops.queue` and `ops.outbox` are append-only today is confirmed when the code is designed (0006 is read then); if they are plainly deletable by their owner, their purge function needs no trigger exception, only the same grants and audit.

## 2. The identity: ONE role, no table privilege at all
`careflow_purge` (a NEW ROLE: this is the owner's decision to take):
- `LOGIN`, with a password held only by the scheduled job's environment (`DATABASE_URL_PURGE`; a new secret, never in CI, never in the frontend, never logged); `NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT`; `CONNECTION LIMIT 2`; `ALTER ROLE ... SET search_path = ''`, `statement_timeout = '60s'`, `lock_timeout = '5s'`, `idle_in_transaction_session_timeout = '30s'`.
- It is NOT a member of `careflow_app`, `queue_worker` accounts, or any other role, and nothing is a member of it.
- Alternative considered and rejected: running the purge as `careflow_app` with a worker account (it would widen the worker, whose whole safety case is that it has no deletion path). Alternative kept as the fallback: no new role, the owner runs the functions by hand with the migrator (no schedule; nobody else can run them either).

## 3. The exact grants to sign off (PROPOSED SQL; not applied)
```sql
create schema retention;
revoke all on schema retention from public;
grant usage on schema retention to careflow_purge;

-- one SECURITY DEFINER function per family, owned by the migrator (BYPASSRLS, as the other definers), search_path pinned
--   retention.purge_checkpoints(p_clinic uuid, p_cutoff timestamptz, p_dry_run boolean, p_expected_max bigint)  returns table (deleted_checkpoints bigint, deleted_blobs bigint, deleted_writes bigint)
--   retention.purge_queue      (p_clinic uuid, p_cutoff timestamptz, p_dry_run boolean, p_expected_max bigint)  returns table (deleted_queue bigint, deleted_outbox bigint)
revoke all on function retention.purge_checkpoints(uuid, timestamptz, boolean, bigint) from public;
revoke all on function retention.purge_queue(uuid, timestamptz, boolean, bigint)       from public;
grant execute on function retention.purge_checkpoints(uuid, timestamptz, boolean, bigint) to careflow_purge;
grant execute on function retention.purge_queue(uuid, timestamptz, boolean, bigint)       to careflow_purge;
-- NOTHING else is granted to careflow_purge: no table, no sequence, no other schema, no other function. careflow_app, anon, authenticated and service_role get NO execute on retention.*.
```
Two supporting objects, owner-only (no grant to anyone): `retention.active_purge` (an append-only record of each purge transaction; section 4) and `retention.window_floor` (the function reads a fixed engineering floor, section 5).

## 4. How an append-only table allows ONE deletion that nobody else can cause
Today the triggers of 0018 and 0021 refuse `update`, `delete`, `truncate` for everyone including the owner. A plain "purging" setting would be settable by anyone, so it is NOT the mechanism. The exception is:
1. The purge function, in its own transaction, inserts one row into `retention.active_purge (xid = pg_current_xact_id(), table_name, clinic_id, started_at)`. Only the function's owner can insert there; `careflow_app` and `careflow_purge` have no privilege on the table.
2. The checkpoint tables' delete triggers are replaced (a NEW migration; never an edit of 0021) by a function that allows a `delete` only when ALL hold: `current_user` is the function owner; a row exists in `retention.active_purge` for THIS transaction id and THIS table; and the row's `started_at` is within the last minute.
3. `update` and `truncate` stay refused for everyone. `audit.log`, `agents.review_decisions`, `agents.triage_proposals` and `mpi.identity_confirmations` keep their triggers UNCHANGED.
A forged session setting is useless (the trigger does not read one); a caller that can insert into `retention.active_purge` is already the table owner, who can disable the triggers anyway (the residual every append-only table here already states).

## 5. What each function does, in order (one transaction)
1. Refuse unless called by `careflow_purge` (`session_user`), and refuse if `p_clinic` is null or does not exist.
2. Refuse a cutoff newer than `now() - interval '7 days'` (an engineering floor against a typo such as "0 days"; it is not a retention period and not clinical). A cutoff is always an argument: the function has no built-in period.
3. Compute the rows that qualify (section 1) and COUNT them. If `p_dry_run` is true (the default), return the counts and stop: nothing is deleted, no active_purge row is written, ONE audit row is written with `dry_run: true`.
4. If not a dry run: refuse when the count exceeds `p_expected_max` (the operator passes the number from the reviewed dry run, so a changed population stops the run), insert the `active_purge` row, delete in bounded batches inside the transaction, write the audit row.
5. Idempotent: a second run with the same cutoff finds nothing and deletes nothing.

## 6. Who may call it, and from where
- **Who:** only the `careflow_purge` login. No application route calls it; no human session does (a person may PAUSE the job by removing its schedule, but cannot run a one-off delete of another range through the application).
- **From where (DEMO: by hand, no schedule: owner condition 1).** A scheduled job, WHEN one is later approved, would be one outside the web application (`python -m careflow.retention`, not yet written), which reads the periods from REQUIRED settings (below), refuses to run when any is unset, and always does the dry run first, printing counts. WHERE it runs is the same open decision as the malware-scanner worker machine: it must not be GitHub Actions against production, and it adds no hosted scheduler without the owner's approval (rule 13). Until decided, the fallback is the owner running it by hand.
- **Settings (names only; NO defaults; unset = the job refuses and says which):** `CAREFLOW_RETENTION_CHECKPOINTS_DAYS`, `CAREFLOW_RETENTION_QUEUE_DAYS`, `DATABASE_URL_PURGE`. The 90-day figure of spec 9.4 stays a documented starting value, not a code default.

## 7. Audit trail
One `audit.log` row per function call (dry runs included), written by the function in the same transaction: `actor_id` null (a system row, as `log_insert_as_self` already allows), `clinic_id` the clinic, `action` `retention.purge`, `resource_type` `retention`, `resource_id` the run's uuid, `details` counts and closed words only: `{"family": "checkpoints", "dry_run": false, "deleted_checkpoints": n, "deleted_blobs": n, "deleted_writes": n, "cutoff_days": d}`. No ids of the deleted rows, no thread ids, no text. The audit writer's allowed-action list gains `retention.purge`. Auditors read these rows through the existing auditor reader.

## 8. The tests that must exist before it ships (live, never committed, run as the real restricted roles)
1. `careflow_app` still cannot delete, update or truncate any of the five tables, and cannot execute any `retention.*` function or read `retention.active_purge`.
2. `careflow_purge` has NO privilege on any table (a catalog query lists every grant of the role: exactly the two function-execute grants and schema usage, nothing else); it cannot `delete from` a checkpoint table directly (the trigger refuses even though it holds no privilege to begin with).
3. A forged setting (`set_config('retention.purging', 'on', true)` or any name) enables nothing; a row inserted by anyone but the owner into `retention.active_purge` is impossible.
4. A thread inside the retention window survives; a thread whose document has an open or acknowledged review task survives; a thread with an undecided proposal survives; a decided, old thread is deleted.
5. A cutoff newer than the floor is refused; a null or foreign clinic is refused; deleting more than `p_expected_max` is refused and deletes nothing.
6. A dry run deletes nothing and writes one audit row; the audit row of a real run carries counts only (a test greps it for ids and text); a second identical run deletes nothing.
7. The function deletes only for ITS clinic: rows of another clinic with the same age survive.
8. `audit.log`, `agents.review_decisions`, `agents.triage_proposals`, `mpi.identity_confirmations`, `agents.review_tasks` are byte-identical before and after a full run (row counts and a checksum over their primary keys).
9. A cutoff inside the clinic's retention period is refused (and an unset period refuses): nothing younger than the period is ever deleted, for any argument.
10. The CI migrate job's catalog check (extended) fails if `careflow_purge` ever gains any privilege other than the ones in section 3.

## 9. Residuals, stated
The table owner (or a superuser) can still disable the triggers, as for every append-only table here. A bug in a purge predicate deletes working state permanently: hence dry run first, the expected-count guard, the floor, and a first production run reviewed by a person. Checkpoint bytes are msgpack: the database cannot confirm they hold no patient text, so deletion removes them but the no-text guarantee still rests on the graph code and its byte-grep test.

## 10. Decisions for the owner before any code
1. Approve the NEW ROLE `careflow_purge` (section 2) with exactly the grants of section 3, or choose the owner-runs-by-hand fallback.
2. Approve the trigger-exception mechanism (section 4) for the checkpoint tables.
3. Decide where the scheduled job runs (section 6).
4. Confirm the protected states of section 1 with the clinical lead; confirm that resolved review tasks are archived (kept), not purged.
5. The retention periods themselves (clinic / clinical lead), as in `retention-and-erasure.md` section 5.
