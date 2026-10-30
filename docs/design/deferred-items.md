# Deferred items and open findings

**Migration numbering.** The design called the `account_id` protection 0013 and the screenings migration 0012. The
`account_id` protection was built first, and the runner rejects a migration numbered below the highest applied one, so it
took **0012** (`0012_patient_account_binding.sql`) and the screenings migration will take **0013**. Wherever the design
document says "0012 (screenings)" read 0013, and where it says "0013 (account binding)" read 0012.

Everything here is **known and intentionally not done yet**. Each line says what it is and what it waits for. If you finish
one, delete its line in the same change. Source of the first group: the msg / iam design review of 2026-10-23
(`docs/design/msg-screening-binding-design.md`).

## Test speed
- **`tests/test_api_referrals_live.py` takes about 21 minutes in one file (1297 s for 27 tests, run 40719139488).** Find out why (fixture setup per test versus the worker ticks and the 14-identity grid) before Phase 3 adds more live tests. No change now.

## Planted tokens (pre-launch blocker 10)
- **Revoking a mistaken identity confirmation (owner request 2026-10-28):** a confirmation is permanent today (append-only, one row per patient). Design only: `docs/design/identity-confirmation-revoke.md` (an append-only event log, latest event wins; front desk, one person, no step-up; the matcher treats the record as unverified again; a nurse review task lists what was linked meanwhile). Not built.
- **Open item: step-up on confirm-identity.** Left out for now (a confirmation only makes a record match normally). REVISIT WITH THE CLINICAL SAFETY LEAD.
- **Option C (later):** the registration function takes the id of a registered `docs.documents` row of the clinic and refuses a second patient from the same document: bounds a worker to one record per human-registered document and gives every worker-created record an audit anchor. Not needed for option A; revisit with the review console.
- **Option B (rejected, 2026-10-28):** the database derives the tokens. Reasons: the HMAC key and the plaintext name, date of birth and phone would cross into SQL (logs, `pg_stat_statements`, error text); key rotation and the per-field subkeys would have to be duplicated in SQL; it contradicts "the database holds only ciphertext and keyed tokens".
- **A console for confirm-identity:** until a review console exists, a repeat referral for a worker-created patient goes to review (accepted workload).

## Waiting for a real vision route (P2-4)
- **Checklist for any real vision provider (none approved; the fakes stay):** (1) the owner's approval of the provider; (2) a key, kept out of the repository; (3) a registry row built with `careflow.adapters.vision.vlm_row`; (4) a data-flow-register entry and its register test; (5) an explicit `timeout_seconds` (required by `vlm_row`, no default).
- **Extraction cache.** Crash-resume re-reads and re-bills vision; a persisted encrypted cache needs schema + privacy review. The in-process memo (bounded to 8 documents) stays until then.
- **Tier 2 (layout).** Stays an empty port; revisit after the real-PDF spike. (A model-based layout tier is not covered by the confirmed carve-out and must return to the owner first.)
- **Vision timeout.** BUILD-SPEC 16.1 gives vision a per-page limit (worker only). It is the registry row's `timeout_seconds`; a real `vlm` row must be built with `careflow.adapters.vision.vlm_row`, where `timeout_seconds` is REQUIRED (no default). The value is the approver's decision. Also needed with a real route: the approval, a key, a data-flow-register entry and its test.
- **Large-image threshold.** `IMAGE_COVERAGE_THRESHOLD = 0.20` (careflow/extraction.py) is a conservative technical setting, PENDING OWNER CONFIRMATION.
- **Narrow rules-first carve-out** for image-to-text transcription (BUILD-SPEC 5.2/10.4, CLAUDE.md rule 2): CONFIRMED by the owner on 2026-10-27 (commits 91d5289, 2df1ec9). Nothing else may precede the rules; a model-based layout tier is not covered.

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
- **Unpinned single-column foreign keys**: found at the Phase 1 close-out and FIXED by migration 0014 (see `docs/design/phase1-status.md`).
  Remaining small item from its review: a cross-clinic idempotency-key collision inside `queue.relay_outbox` (only a worker holding
  `queue_worker` in two clinics can hit it) now surfaces as an untranslated `23503` database error instead of a clear queue error.
- **Labeled-pair merge gate** (BUILD-SPEC 14.3, "no wrong patient merge on the labeled identity-matching pairs"): moved to Phase 5, needs
  clinician-reviewed labels.
- **Minute tick, immediate poke and the 12/25/40/40 s ceilings** (BUILD-SPEC 7.3, 7.4): moved to Phase 2 package P2-1 and built there
  (`careflow/budget.py`, `careflow/tick.py`). Still open: no route calls the poke after enqueueing, and no handler exists.
- **Documents (P2-3)**: no HTTP routes yet (the signed-upload hand-out and the register call need `/v1/staff/documents/...` routes, each with its own
  role-matrix case in the same change, when a console or the extraction worker consumes them); a periodic reconciliation of storage against
  `docs.documents` (an object can be orphaned only if the caller's own transaction fails to commit after a successful insert); erasure of a
  document (DPDP) is not designed; no `audit.log` row is written on registration; the real Supabase Storage adapter, its bucket policies on
  `storage.objects`, and a malware scanner (a pre-launch blocker, `docs/design/uploads-supabase-storage.md`). Privacy decisions flagged to the owner:
  front desk can read every document of its clinic (it keys referral facts in by hand without a model route), and a doctor can read but not register.
- **Per-clinic daily spend**: persisted in `ops.spend_ledger` (migration 0016) and read per origin by `careflow.spend.PostgresSpendLedger`;
  the daily ceiling has no default and arrives by configuration (owner). Still open: a ledger cannot be corrected (append-only, no negative
  amounts), so a mistaken entry needs the migrator; a reversal mechanism is a later change. The gateway is not yet constructed with the Postgres
  ledger anywhere (no caller exists).
- **Registry approver**: now enforced in the database (migration 0016). Still open: it proves the two accounts are eligible and different, NOT
  that the approver consented; real attestation needs a signed-in approver action. A registry write path from the console would also need
  audit rows.
- **Uploads**: Supabase Storage behind an `UploadPort` instead of Uploadcare (accepted deviation, `docs/design/uploads-supabase-storage.md`);
  no malware scanner exists, a pre-launch blocker, and real uploads stay disabled in `clinical` until one does.
- **Prompt versioning**: `prompts/` and its scan exist; the version-and-content-hash loader and the "every model call records which prompt
  version answered" part of BUILD-SPEC 17.4 arrive with the first prompt.
- **`aal2` database policy** (a restrictive policy requiring `aal = 'aal2'` for sensitive tables): deferred. Staff second-factor
  enrolment at login is enforced in frontend F2, not in the database.
- **Key store**: environment variables stay (accepted); revisit when hosting is decided and the hosting provider's secret manager is known.
- **Redis/SQS adapter stub** (BUILD-SPEC 7.4): accepted deviation, see `docs/design/phase1-status.md`.
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
- **Denied-access monitoring** (Phase 4 watchdog): repeated refused message reads by one actor. Refused reads (404/410) write no
  audit row by decision; the route logs one line of ids only (message, actor, status), which is what the watchdog would count.
- **Message read route, still open**: messages on the subordinate of an active merge are read by their own id (no routing to the
  master yet); no list endpoint; no step-up (not listed in BUILD-SPEC 18.1). Admin, business owner and auditor have no read access to
  message bodies by decision (the spec's "admin-only, every view audited" is about LLM prompt/response bodies).
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

## P2-7 referral to identity (added with careflow/referral.py)
- Role split for escalate vs identity insert, and a review-task path for an unassessable document screening: see docs/design/p2-7-referral-identity.md.
- No route yet (and so no role-matrix case yet); add both in the same change when the route is built.

## From the P2-3/5/6/7 spec-compliance review (2026-10-24)
- P2-3 TOCTOU: a signed upload URL stays valid up to 600 s after `begin()`. If Supabase signed uploads can OVERWRITE an existing object, bytes could change after the verified sha256 was recorded. Unspiked (not in Appendix B yet; the owner owns BUILD-SPEC.md, so it is listed here): spike the overwrite behaviour with a real call before the adapter exists; consider single-use paths, and a re-hash in P2-4 before extraction.
- P2-3 readiness: the design said a clinical deployment without a scanner should report not-ready. Not built (the block is inside `DocumentIntake`, fail-closed, `scanner_configured` is set by nothing). Add the readiness check when the scanner or the route lands.
- P2-3 route design: force `registered_by` to the caller's `auth.uid()`; otherwise `register()` could delete the object at the clinic-derived path on a 42501.
- P2-5: the wiring package (P2-8) must read facts only through `rule_value()`/`usable_by_rules` (`GroundedFact.value` is readable directly); add an end-to-end gate then. Tier 2 offers two options (`supported`/`not_supported`), not accept/verify/reject.
- P2-6: scope keys (clinic/status) are not enforced beyond non-empty; define them with the card schema. Re-verify scope at the join (Postgres truth does not take it yet). The BM25-only version check is fixed (16782ae).
- P2-7: extractor data-flow entry and gateway data class when the extractor is wired.

## Owner decisions of 2026-10-24 on those items
- DONE: `DocumentIntake.read_verified` re-hashes (size and sha256) before any worker gets the bytes and refuses `ContentChanged` (covers the overwrite window). Every future reader of a document MUST go through it.
- DONE: `check_readiness(..., malware_scanner_configured=False)`: clinical (and any unrecognised profile) is not ready without a scanner; `/readyz` passes `app.state.malware_scanner_configured`, which nothing sets.
- STAYS on the future-route list: force `registered_by` to the caller's `auth.uid()`.
- Appendix B now lists the signed-upload overwrite/validity spike (appended, no existing text changed).
- Retrieval scope keys: wait for the card schema.

## Hosted model providers: DRAFT wording only (2026-10-29; owner has not yet answered; nothing real is built, nothing in BUILD-SPEC.md or CLAUDE.md is edited)
Proposed reading of spec 16.5 ("models that touch patient data run locally"), for the owner to accept or change:
- A hosted model row (any model served by a third party: a hosted vision model, a decision model reached through a router, a hosted translation service) can NEVER be `data_policy: phi_approved` without a new, written owner decision that names the provider and the data it may see.
- Every hosted row in the model registry must therefore be `data_policy: synthetic_only`, and the gateway refuses it outside profile `demo`.
- A free-tier hosted row is `synthetic_only` by definition (free tiers may retain or train on inputs).
- Until the owner approves a provider for synthetic demo use, the registry keeps fakes only and no adapter is built. If approved: adapters sit behind the gateway, each row has an explicit timeout through `vlm_row`, its own data-flow-register entry and test, keys are read only from the env names in `.env.example`, and each role has a spend ceiling.

## Documents routes (batch4)
- The upload routes (`/v1/staff/documents/uploads`, `/register`) and the referral queue are built and role-matrix covered; **there is still no real `UploadPort` adapter**: it needs the Supabase Storage bucket and its `storage.objects` policy (an owner decision; stop-and-ask per CLAUDE.md) and the Appendix B signed-upload spike. Until then `document_intake` is None in the real app and the upload routes answer 503.
- Test residue: `docs.documents` is append-only for every role, so the clinics a role-matrix run registers documents in cannot be deleted (the teardown leaves them, like confirmed patients). Nothing reads them; every suite is scoped to its own clinic.
