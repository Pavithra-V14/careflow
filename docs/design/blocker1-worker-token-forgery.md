**FIRST LINE: this fix needs ONE new migration (0022) and ONE new grant: `EXECUTE` on a new `SECURITY DEFINER` function for `careflow_app`. In exchange it REMOVES three of the worker's `INSERT` policies. Net: the queue worker ends with fewer privileges than today. No code yet; waiting for the owner's OK.**

**OPEN QUESTIONS (answers needed before any code), each with my recommendation and what it blocks:**

1. **Should the registration function also close the front-desk route (`POST /v1/staff/patients`), or only the unattended worker?** Recommendation: only the worker. Front desk is a person's authenticated request, and one change at a time keeps the review small. *Blocks:* the scope of the migration and of the pipeline change; if you say "both", the front-desk policies and the route tests are in scope too.
2. **Is "three worker INSERT policies removed, one `EXECUTE` grant on one `SECURITY DEFINER` function added" acceptable as the grant change?** Recommendation: yes; it is the only way to make "attach a token to an existing patient" impossible, because a row-level predicate cannot express "the record this call just created". Net, the worker has fewer privileges than today. *Blocks:* migration 0022 and everything after it, including the rls-security-reviewer pass.
3. **Should the function write an `audit.log` row for each registration?** Recommendation: no, not in this change; it belongs to the audit-log work (item c) and would be the first writer of `audit.log` from a definer function, which deserves its own review. *Blocks:* nothing in this fix; it only decides whether the function's signature and tests grow now or later.

# Pre-launch blocker 1: the worker can attach identity tokens to any patient and forge possible-duplicates (design note)

Source: 0019's header, residual risk 4. Owner decision 2026-10-24 accepted it for synthetic data only; it must be closed before any real patient data.

## 1. The attack (what a compromised or buggy worker can do today)

The queue worker may `INSERT` into `mpi.patients`, `mpi.identity_tokens` and `mpi.match_candidates` (0019). The foreign keys pin only the clinic, not "the record this call just created". So, with a queue-worker token, in its own clinic:

1. **Token re-binding.** `insert into mpi.identity_tokens (patient_id = <ANY existing patient>, kind, token, ...)`. A later referral whose name/date of birth hashes to that token now "confidently matches" that patient, so a referral for person B is linked to person A's record (wrong-patient link). Nothing is updated or deleted, so no policy stops it.
2. **Forged possible-duplicate.** `insert into mpi.match_candidates (patient_id = X, candidate_patient_id = Y, status = 'pending')` between two existing patients. Front desk then sees a duplicate review that nobody's data produced: a nuisance today, a mislead toward a bad merge later.
3. **Free choice of `id`** on rows it inserts (residual 6).

Who can do it: the worker process itself (a bug or a compromise of it), or anything holding a worker token. HTTP routes do not expose this; the reach is through the database connection the worker already holds.

## 2. The fix: a definer function is the ONLY way the worker writes these tables

New function `mpi.register_patient_from_referral(...)`, `SECURITY DEFINER`, `set search_path = ''`, owned by the migrator (which bypasses RLS, the same pattern as `msg.record_screening`, 0013).

- **Inputs:** clinic, origin, the three ciphertexts and key versions, the idempotency key, the identity tokens (kinds and values, one key version), and the list of near-match candidates (other patient ids with kinds and reason).
- **What it does, in one atomic call:** checks the caller is a `queue_worker` of that clinic (`iam.has_clinic_role(p_clinic, array['queue_worker'])`, else SQLSTATE 42501); inserts the patient (the database generates the `id`, nobody passes one in); inserts the tokens **for that new id only**; inserts the candidate rows with `patient_id` = that new id, `status = 'pending'`, resolved columns null, and each `candidate_patient_id` required to be an existing, non-erased patient of the same clinic and origin, never the new id; returns the new id.
- **What it cannot do by construction:** attach a token to an existing patient (no patient id is accepted), create a candidate between two existing patients (the `patient_id` side is always the new row), choose an `id`.
- **Bounds:** at most 2 tokens (kinds from the allowed set), at most 20 candidates, a non-blank idempotency key. Duplicate idempotency keys still raise the existing unique violation.
- **Grants:** `revoke all on function ... from public`; `grant execute on function ... to careflow_app` (the function, not the tables). The worker's three `INSERT` policies (`patients_insert_worker`, `tokens_insert_worker`, `match_candidates_insert_worker`) are **dropped**. Its four `SELECT` policies stay (matching still reads). No `UPDATE` or `DELETE` appears anywhere.
- **Front desk is unchanged.** The front-desk route (`POST /v1/staff/patients`) keeps inserting directly under its own policies: it is a person's authenticated request, not the unattended worker. (Question 1 below.)

## 3. Code change (small, after OK)

`PatientIngestionPipeline.ingest` gains a registrar choice: the default (front desk) keeps today's three inserts; the Front Door's ingestor is built for the function path and calls the function instead of the inserts. Matching, replay check, canonicalisation, tokens, locks and results are untouched. No change to the graph, the routes or the role matrix.

## 4. Tests

- **Offline:** the function path sends exactly one function call with the same parameters the inserts used; the default path is unchanged; the migration text has `security definer`, `set search_path = ''`, a worker-role check, the revoke from public, and drops the three policies (a static test, like `test_migration_syntax`).
- **Live, as the restricted role** (extending `tests/test_rls_mpi_worker_live.py`): the worker's direct `INSERT` into each of the three tables is refused (42501); the function as a worker creates record + tokens + candidates and a replay returns the original; **the forgery attempts have no way to be expressed** (the signature has no patient id) and a candidate naming a patient of another clinic, an erased patient, a different origin, or the new patient itself is refused; the function as front desk, admin, a worker of ANOTHER clinic, a patient and a suspended account is refused; matching and the near-match path (`review`) still work end to end through the Front Door live test; positive controls so a refusal cannot be a vacuous pass.
- **Role matrix:** no route changes, so no new matrix case (rule 8 covers routes); the front-desk registration route's existing cases stay green.
- **rls-security-reviewer** on the migration before it is pushed.

## 5. Migration, ledger, rollout

`0022_worker_registration_function.sql`. The checksummed ledger forbids editing 0019, so 0019's policies are dropped by 0022 (fix forward). Applied to the CI project by the migrate job in the PR; to dev only after merge. No data change (no table rewrite).

## 6. What remains after the fix (stated, not closed)

- A worker can still create NEW patient records (that is its job) and can compute tokens (it holds the vault key). It could register a record carrying a real person's token, but the matcher treats two records with the same name-and-date token as ambiguous and sends it to a person, so this surfaces as a review, not a silent wrong link. Residual risks 1, 2, 3 and 5 of 0019 are untouched.

## 7. Questions for the owner

1. Close it for the front-desk route too (the same function for both), or leave front desk as is because it is a person's authenticated request? Recommendation: leave it; one change at a time.
2. Is "fewer worker privileges, plus one function-execute grant" acceptable as the grant change? (It is the only way to make the worker unable to attach to existing patients; a predicate cannot express it.)
3. Should the function also write an `audit.log` row for each registration? Recommendation: no, that belongs to the audit work (item c) and would be its first writer from a definer function.
