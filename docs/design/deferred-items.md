# Deferred items and open findings

**Migration numbering.** The design called the `account_id` protection 0013 and the screenings migration 0012. The
`account_id` protection was built first, and the runner rejects a migration numbered below the highest applied one, so it
took **0012** (`0012_patient_account_binding.sql`) and the screenings migration will take **0013**. Wherever the design
document says "0012 (screenings)" read 0013, and where it says "0013 (account binding)" read 0012.

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
- **Step-up claim shape: required spike (Appendix B).** The merge routes read `aal` and `amr` entries of the form
  `{"method": "totp", "timestamp": <int seconds>}`; that shape comes from documentation, not a real token. The owner enrols TOTP on
  a dev staff account and pastes the `aal`/`amr`/`iat` claims; until then the step-up check is not verified.
  See `docs/design/step-up-spike.md`.
- **Around step-up**: enforcing MFA enrolment at first staff login; an `aal2` restrictive policy in the database; a clock-skew
  allowance for a fresh timestamp (zero today); reuse of `require_step_up` for publish card/rule, autonomy-mode change, erasure and
  role change when those routes exist.
- **`STRONG_IDENTIFIERS` is reachable over HTTP** (`POST /v1/staff/patients`) and is pending Priya's sign-off; no real patient data
  until it is recorded.
- **The merge-propose idempotency key is global, not per clinic** (`mpi.merges.idempotency_key` is unique across the table). A client
  in clinic B that reuses a key clinic A used gets a 409, which is a small existence oracle for client-chosen keys across clinics.
  Revisit with the portal design (per-clinic key scope or a server-derived prefix). `identity.py` is unchanged.
- **An unmerge replay reports `decisions_flagged` as 0** instead of the original count (`identity.py` returns 0 on the replay path; the
  review task itself is returned correctly). Revisit when `identity.py` is next changed.
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
- **A human resolution path for an `unassessable` message.** Today an `unassessable` screening row can never be cleared: the worst-of
  view (`msg.message_screening_status`) reports `unassessable` permanently, and only the nurse task carries any resolution state.
  Design when the nurse console is built: a resolution record written by a human decision, never by the engine.
  This also covers a body that is `unassessable` only because a key version was missing from one instance (a transient deployment fault):
  it stays `unassessable` until that path exists.
- **Patient registration and a "matched" outcome store no idempotency key.** If a different person is later registered under the same key,
  a retry of the original `matched` request returns 409 instead of `matched`. An edge case, not a rule-5 violation; revisit with the portal
  registration design.
- **Voice sessions must run under a nurse, doctor or queue_worker identity.** `escalate_red_flag` (the path for non-message sources
  such as a live transcript) is refused for front desk and admin since migration 0013, so a voice session under either identity
  could not raise its red flag (CLAUDE.md rule 11).
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

## Screenings migration (as built: 0013): design settled, awaiting owner approval
The earlier open findings (advisory lock, NULL hash on an empty rule set, key hash column, NULL/unknown inputs, policy
replacement) are fixed in the design (amendments v3.5 and v3.6 in `msg-screening-binding-design.md`), and the last scoped review
returned SAFE TO MERGE. What remains is implementation and the owner's approval of one deviation: the serialising lock is on
the **thread** row, not the message row, so key rotation can never stall an escalation. Items to implement with the migration:
- NULL-safe comparisons (`IS DISTINCT FROM`), `clear` refused on any NULL claim or empty key; the rule-set hash never NULL
  (empty set = sha256 of nothing; zero-hash fallback flagged `rule_set_unverified`, never a clear).
- Explicit DROP of 0007's `review_tasks_select` and `review_tasks_insert`; front desk and admin see and insert only
  `unmerge_review`.
- Existing tests that change in the SAME commit: `tests/test_rules_live.py` lines 407-414, 417-422, 449-457;
  `tests/test_rules.py` lines 328 and 345; `tests/test_rls_iam_audit_live.py` adds `screenings` to the msg table map.

## State of the dev project (not a code item)
- The dev project holds **leftover `origin = 'test'` rows** from a stray run of the live suites on 2026-10-23 (a `.env` made
  pytest connect to dev; the run was killed before its teardown). Counts at the time: 4 clinics, 28 accounts, 24 staff roles,
  92 patients, 2 locations, 2 providers, 4 visit types, 280 slots, 4 priority levels, 60 bookings, 4 waitlist entries; the
  one `kb.red_flag_rules` row is the legitimate stub from migration 0007. By decision nothing was deleted. Test clinics
  carry no real data, but they appear in any dev query that does not filter on origin.
- `audit.log` is append-only for every role (including the table owner), so any `audit.log` rows written by tests or
  scripts against a database can never be cleaned up; keep audit-writing tests inside rolled-back transactions.
