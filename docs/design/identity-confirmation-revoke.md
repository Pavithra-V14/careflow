**Design only (owner request 2026-10-28). No code, no migration. Goes to the deferred list.**

# Revoking a mistaken identity confirmation

**The gap.** Migration 0023 records a front-desk confirmation in `mpi.identity_confirmations`: append-only, one row per patient. A WRONG confirmation (the wrong person confirmed, or a planted record confirmed by mistake) is therefore permanent: that record keeps matching confidently, and nothing in the schema can undo it.

## Recommended shape: an append-only event log, "latest event wins"

Replace "one confirmation row per patient" by an event log, so a record can be confirmed, revoked and confirmed again without ever editing or deleting a row.

**What a revoke row holds** (same table as confirmations, `kind = 'revoke'`; or a sibling table with the same columns): `id`, `clinic_id`, `patient_id` (composite FK to the patient, as today), `kind` (`confirm` | `revoke`), `acted_by` (the signed-in account, forced to `auth.uid()` by the policy), a `reason` from a CLOSED list of data-quality words (for example `wrong_person`, `planted_or_suspect`, `mistaken_click`; the list is an operational vocabulary, not clinical content, but is listed in the clinician sheet for confirmation), `origin` (the patient's, enforced by the trigger), `created_at`, and an `idempotency_key` (unique). No free text (it could hold patient text), no ids of other records.

**Verified means** (the matcher's rule): the patient is front-desk-created and has no revoke after it, OR the patient's LATEST event is a `confirm`. A front-desk-created record can be demoted by a revoke too; a revoke on a record whose latest event is already a revoke is a no-op replay.

**Who may revoke.** Front desk of the clinic, one person, as themselves. No second person and no step-up: a revoke only moves a record back towards "a person decides" (the safe direction), so it needs less ceremony than a confirm. A nurse or doctor who suspects a record can raise the existing review path; they do not write the log. (Open question for the clinical safety lead.)

**What the matcher does afterwards.** The record is unverified again: a full match on it is a review, never an automatic link. Nothing already decided is undone: referrals that were linked to the record while it was confirmed stay linked; a revoke does not unlink, unmerge or erase. Because of that the revoke should also raise ONE nurse review task, `identity_confirmation_revoked`, with ids and counts only (the patient id and the number of triage proposals created for that patient since the confirmation, from `agents.triage_proposals.patient_id`), so a person re-checks what was linked in the window. That task kind, its priority and its role are clinician-owned placeholders (`needs clinician sign-off`).

**Tests that would prove it.** Offline: the matcher's verified rule over (confirm, revoke, confirm) sequences and over front-desk-created records; the closed reason list; no free text accepted. Live, as the restricted role: only front desk of the clinic can write the log and only as themselves; a revoke makes the same record's next full match a review; a second confirm after a revoke restores matching; the log is append-only for the owner too; the role-matrix cases for the new route (`POST /v1/staff/patients/{id}/revoke-identity`, idempotency key, fixed refusals) in the same change as the route; the OpenAPI snapshot. rls-security-reviewer on the migration.

**What it changes.** One migration (the log replaces `unique (patient_id)`; fix-forward, never an edit of 0023), one route, the matcher's `_find` (the latest event per patient instead of existence), the pre-launch checklist.

**Not decided here.** Whether a revoke needs a second person after all; the reason list; the task's priority and role; whether front-desk-created records may be revoked.
