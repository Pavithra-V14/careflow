# Runbook: two patients were merged by mistake

BUILD-SPEC.md 17.2; `careflow/identity.py`; routes `POST /v1/staff/merges/{id}/unmerge` and `GET /v1/staff/identity/merges`. **Status: drafted for the owner's review; nothing here is a clinical decision.**

**What a merge is.** A linking of a subordinate record to a master, proposed by one front-desk person and approved by a SECOND (two-person rule, step-up required). Nothing is deleted or rewritten: the link can be broken, and nothing already decided is undone.

**Who:** a front-desk user (step-up sign-in) breaks the link; a nurse or doctor handles the review task it raises. **Safe to repeat:** yes (the unmerge takes an idempotency key; a replay answers the same).

1. **Find the merge.** `GET /v1/staff/identity/merges` (front desk, nurse, doctor read it) and open the merge by id. Check `status` (`active`) and the two patient ids. Do not discuss names in tickets; use ids.
2. **Break the link.** Front desk calls `POST /v1/staff/merges/{merge_id}/unmerge` with a fresh `X-Idempotency-Key` and a step-up token. For a link that was active this raises a nurse review task and returns `decisions_flagged`: the number of decisions made while the link stood, which a person must re-check. Cancelling a never-confirmed proposal raises none.
3. **The nurse re-checks.** The task lists the patient ids and the window; the nurse decides what, if anything, to change through the normal review screens. Nothing is changed automatically.
4. **Mark the records.** If one record was entered for the wrong person, front desk revokes the identity confirmation (reason from the closed list) so a full match on it goes to a person, not an automatic link.
5. **Record the cause** (data-entry error, shared phone number, planted record) in the incident note, ids only. Repeated causes go to the clinical lead.

**Never:** edit `mpi.merges` or `mpi.patients` rows by hand; unmerge from a script with a service key; proceed if step-up fails.
