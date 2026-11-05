# Five design notes awaiting the owner's OK (2026-11-04)

**Nothing here is built.** Each note says what changes in plain words, whether it needs a new grant, role or external service, and what the owner has to decide. They answer the owner's decision B of 2026-11-04. Keep-waiting items (revoke wiring, retention purge, durable spend ledger, safety-net second opinion, supervisor role and admin alert) are not repeated.

## 1. M1: front desk can overwrite or erase patient identity data (review finding M1; pre-launch item 36)

**Today, in plain words.** Front desk's database permission lets it change a patient's encrypted name, date of birth and phone, their key-version numbers, and the erased flag (migration 0012, a column list). One statement can therefore wipe a patient's identity or replace it with different encrypted bytes, and nothing writes an audit row. The same table-wide update permission exists on identity tokens (0002), so a token can be moved to another patient, and on match candidates (0003). No route does any of this; it is only the permission that is too wide.

**Proposed change (migration 0031).**
1. `mpi.patients`: take away front desk's UPDATE on the encrypted columns, the key versions and `erased_at`. Nothing in the application updates them; key rotation runs as the migrator (`docs/runbooks/key_rotation.md`) and is unaffected. **This only removes a permission.**
2. `mpi.identity_tokens`: take away UPDATE altogether (tokens are written once; rotation also runs as the migrator). **Only removes a permission.**
3. `mpi.match_candidates`: narrow UPDATE to `status`, `resolved_at`, `resolved_by` (the only columns the engine sets; migration 0029 already makes the pair immutable). **Only narrows.**
4. **Erasure** (the one part that would be NEW): today there is no way to erase a patient on purpose except by the wide permission above. A proper path would be one new SECURITY DEFINER function, `mpi.erase_patient(patient, reason code, idempotency key)`, granted EXECUTE to the application role, which clears the ciphertexts, sets `erased_at`, refuses if the patient has a live merge link, and writes the audit row in the same transaction. **That is a new grant (EXECUTE on a definer function).** Messages are not touched by it.

**Recommendation.** Do 1 to 3 now (they only remove or narrow permissions; a reviewer pass and live tests that the old statements are refused). Do NOT build 4 yet: erasure belongs with the retention and erasure design (`retention-and-erasure-purge.md`), which waits for the privacy owner's periods; building a one-off erase function first would be the same decision made twice. Until then nobody can erase a patient through the application, which is the safer state.
**Decision needed:** approve 1 to 3 now; defer 4 to the purge design (yes / no).

## 2. Who creates slots, providers and locations (pre-launch item 45)

**Today.** The tables, policies and permissions already allow front desk and admin to create locations, providers, visit types and slots (migration 0002). There is simply no route, so a clinic cannot set itself up without the seed script. **No migration, no new grant, no new role.**

**Proposed.** Four small staff routes, front desk and admin of the clinic only, each with an idempotency key, an audit row and role-matrix cases: create a location, a provider, a visit type, and a slot (or a day's series of slots up to a fixed cap). A slot that overlaps the same provider's live slot is the database's existing exclusion error, returned as the fixed 409. Priority levels are NOT created here (they are clinician-owned, items 16 to 22). Editing and deactivating (not deleting) are a second step.
**Decision needed:** approve the four routes as written (yes / no), and whether admin should be able to create a slot (recommendation: yes, same as front desk).

## 3. Outbox producers, event-to-job, and the expired-hold sweep (pre-launch item 42)

**Today.** The outbox table and the relay exist; nothing writes an event, and a referral is started by a person pressing "run" (`POST /v1/staff/referrals/{id}/run`). Expired holds are filtered out when slots are read, so a booking is never wrongly blocked, but the hold rows and the 'held' slot flag are never cleaned up in production.

**Proposed.** (a) Keep referral intake manual until launch, as the owner decided: no producer is added. (b) Schedule the expired-hold sweep in the worker's tick. **This needs a new grant:** the worker's database identity appears not to hold UPDATE on `sched.holds` or `sched.slots` today (it is deliberately a narrow identity; the reviewer pass would confirm this from the final grants). Options: give the worker UPDATE on exactly those two tables' status columns through a worker-only policy, or one SECURITY DEFINER function `sched.release_expired_holds()` that does only that and is granted to the worker only. **Recommendation:** the definer function (smaller than a table grant), reviewed like any migration.
**Decision needed:** approve the sweep with the definer function (new grant, EXECUTE for the worker only) or keep expired holds as harmless rows until later (nothing breaks either way).

## 4. Staff invitation and role management (pre-launch item 31)

**Today.** Accounts and role grants are created by `scripts/seed_dev_staff.py` run by the owner. The application role cannot insert into `iam.accounts` or `iam.staff_roles`, and creating a sign-in needs the Supabase Auth admin API (the secret key).

**Options.**
- **A. Keep it a script (no new grant, no secret key in the web process).** The owner or an operator runs a reviewed script to invite and grant; a small set of routes covers only the TIGHTENING actions an admin needs: revoke a role, suspend an account. Revoking needs an UPDATE the admin policies may already allow (to be checked). **Recommendation for launch.**
- **B. Full routes (invite, grant, revoke, suspend) for the clinic admin.** Needs insert grants and policies on `iam`, a two-person rule for granting admin, step-up, and the Supabase secret key inside the API process to call the Auth admin API. That is a new external-service use and the largest secret in the system moving into the web process.
**Decision needed:** A or B (recommendation: A for launch; B only if clinics must self-serve).

## 5. Phase 3 delivery with NO external service at launch (pre-launch item 29)

**Decision recorded by the owner:** the front desk copies the drafted text and sends it from the clinic's own phone. SMS and DLT registration only if that proves too slow.

**What that means in the product.** The offer draft already exists as text on the staff screen. What is missing is a truthful record: the "send" action must say that a PERSON delivered it, not that the system did. **Proposed:** the offer's send step records the channel as `manual` (a closed word) with who and when, and nothing is ever sent by the system; the patient's reply is typed in by the front desk as a staff-entered message and goes through reply interpretation (A8) with the red-flag rules first. Offer expiry is a list the front desk works ("offers past their time"), not a background job, so no worker grant is needed. **No external service, no new grant, no new role.** The frontend adds a "copy text" button and a "mark as sent from the clinic phone" action (frontend work, not touched here).
**Decision needed:** approve recording `manual` as the delivery channel and the expiry list (yes / no), and confirm that an unsent or unmarked offer is never shown as sent.
