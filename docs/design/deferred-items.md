# Deferred items and open findings

Everything here is **known and intentionally not done yet**. Each line says what it is and what it waits for. If you finish
one, delete its line in the same change. Source of the first group: the msg / iam design review of 2026-10-23
(`docs/design/msg-screening-binding-design.md`).

## Waiting for the patient portal phase
- **Claim function**: the database enforces the BUILD-SPEC 17.2 rule (verified contact AND date of birth match) when an
  account claims a patient record. Migration 0013 only protects an existing binding.
- **Accounts with no `mpi.patients` row** cannot have a message thread (threads are keyed by patient). 17.5 says `safety`
  is visible to every registered patient, so the portal design must decide.
- **`iam.account_stage()`**: derive from `auth.uid()` only (never an arbitrary account id), clinic-scoped (an approval in
  clinic A must not unlock care in clinic B), none unless the account is `active`, and revocable.
- **`iam.consent_records`**: order by a bigint identity (not a client-settable timestamp); a `recorded_by` for staff or
  guardian entries; guardian and dependent consent needs counsel (20.1); consent gating before AI processing and external
  calls.
- **`iam.consent_notices`**: every published notice version kept (9.4); two-person publish; clinic-scoped "current". The
  notice wording is legal content and is never written by an engineer.
- **`iam.grievances`**: a narrow SECURITY DEFINER insert with rate limiting (an unauthenticated caller has no identity under
  RLS); the description is patient data; no response deadline is invented.
- **`iam.activation_requests`**: approver forced to `auth.uid()` and clinical staff; decisions not flippable; who triggers
  `pending_activation` "after first visit" is undecided (no attendance data exists).
- **Patient-side `msg` policies**: the thread's patient has `account_id = auth.uid()`; topic gating on threads and
  messages (`care` only after activation); a deferred constraint requiring a screening for every patient-authored message.
- **`msg.reads`**: skipped until a route needs it.
- **Guardian binding** in `mpi.patients.account_id` (0013 binds `patient` accounts only): waits for the guardian design and
  counsel.

## Waiting for a later phase or feature
- **Staff message read route**: decrypts through `careflow/messages.py` only; adds its own role-matrix case in the same change.
- **Application-level get-or-create for threads and messages** (`ON CONFLICT (idempotency_key) DO NOTHING`, then a keyed
  SELECT that compares thread, sender and clinic; a different key for the same patient and topic returns the existing
  thread; a message-id collision is an error): arrives with the route that writes them. The database half is tested.
- **Erasure procedure** (migrator-run, audited, with a runbook). Order is pinned in migration 0011: update
  `mpi.patients.erased_at` FIRST, then erase message bodies in later statements of the same transaction.
- **Watchdog metrics** (Phase 4, `metrics-catalog.yaml`): messages with no screening older than N seconds; an erased
  patient with a non-erased message.
- **Admin escalation (12.3)** uses alerts with class, count and opaque id only, through the Phase 4 watchdog. Admin gets NO
  read access to message-sourced review tasks (decision 2026-10-23: rule codes can reveal a condition).
- **Merged patients**: threads and messages on a patient who is the subordinate of an active merge (0008) land on a thread
  nobody routes to; routing belongs to the future read route.
- **A deferred "no live message for an erased patient" constraint** on `mpi.patients.erased_at` would make the erasure order
  structural instead of documented.
- **Rule-content immutability**: a trigger on `kb.red_flag_rules` (the rule-set hash covers ids and versions, not text) and a
  length cap on `rule_code`.
- **Emergency reply wording and template**: clinician-authored, never written by an engineer. `patient_reply` records only
  that the send is pending.
- **Red-flag gold-set regression test**: blocked on clinician-authored rules.
- **Phase 2 acceptance item**: the BUILD-SPEC 7.3 ceilings (12 model calls, 25 tool calls, 40 steps, 40 s per tick) need a
  runner that enforces and tests them.
- **Queue worker HTTP auth** (`/internal`): proposal based on 18.4 (short-lived signed token per job) when the Phase 2 worker
  is built.
- **Pre-existing, outside the new code**: `rules.py escalate_red_flag` still derives its task key from the source message
  id for non-message sources (voice transcripts included); a planted key in another clinic's invisible rows would make it
  raise "already used". Give it its own item when the voice path is built (rule 11).

## Residual risks, stated (BUILD-SPEC 18.1)
- Anyone who can run arbitrary SQL as `careflow_app` can set the request claims and call the definer functions. The database
  narrows who may call them and rejects inconsistent input; it cannot prove the Python rule engine ran, nor that the text it
  screened is the text stored.
- The migrator/owner can disable triggers or set `session_replication_role`, so append-only and the erased-patient guard
  bind the application role and every ordinary session, not a migrator-level actor. The migrator is trusted, as for `audit.log`.
- A staff member can mislabel a relayed patient message as `staff` and so avoid the screening requirement (0012). The
  database cannot close that; the worst-of view lists every unscreened message regardless of label.
- A demo-profile deployment on live origin raises an `unassessable` nurse task for every message (fail-safe but noisy).

## Open findings on the 0012 design (re-review of 2026-10-23, verdict BLOCKED, NOT yet fixed)
These must be resolved in the design before 0012 is written; 0012 does not proceed until they are:
- **HIGH: the per-message advisory lock can be held by any app-role session** (`pg_advisory_xact_lock` is executable by
  PUBLIC), so a known message uuid lets a caller block `record_screening` for that message. Proposed fix: serialise on a
  row lock the app role cannot take, `select ... from msg.messages where id = p_message_id for no key update` as the
  definer (the app role has no UPDATE privilege on messages), and take the same lock in the table trigger; the lock test
  changes to match.
- **HIGH: `rule_set_hash` is NULL when the in-force rule set is empty** (`string_agg` over no rows is NULL), and the column
  is NOT NULL, so every `unassessable` in a clinic with no approved rules (the state 0007 ships in) would be refused.
  Proposed fix: `coalesce(string_agg(...), '')` before hashing, an empty-set parity fixture and test, and an exception
  fallback to a sentinel hash for red_flag and unassessable.
- **MEDIUM**: the stored caller-key hash column must be non-unique (or unique only with the message id); strike the v3.1
  sentence that turns a `unique_violation` into an error for red_flag and unassessable (re-select and return the existing
  row instead); NULL `p_idempotency_key`, NULL provenance and an unknown `p_profile` must not refuse a red_flag (coalesce,
  treat unknown as `clinical`, never declare the function STRICT).
- **LOW**: the migration must DROP 0007's `review_tasks_select` and `review_tasks_insert` before creating the new ones
  (permissive policies OR together); existing tests to change: `tests/test_rules_live.py` lines 417, 425, 449-457 and 407-414,
  and `tests/test_rules.py:328`.
