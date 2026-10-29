-- 0025: revoking a mistaken identity confirmation (docs/design/identity-confirmation-revoke.md; owner instruction 2026-10-29). Fix-forward of 0023: nothing in 0023 is edited.
--
-- WHAT THIS DOES. mpi.identity_confirmations becomes an append-only EVENT LOG per patient: a row is a `confirm` or a `revoke`. The matcher's rule becomes "the patient's LATEST event is a
-- confirm, or (no event at all and the record was created by front desk)". A revoke moves a record back towards "a person decides"; nothing already decided is undone (no unlink, no unmerge,
-- no erase).
--   * `unique (patient_id)` is dropped (a record can be confirmed, revoked and confirmed again). Idempotency stays with the unique idempotency_key.
--   * `kind` ('confirm' | 'revoke', default 'confirm' so a writer that does not know the column still writes a confirmation) and `reason` (a CLOSED list of data-quality words; required for a
--     revoke and forbidden on a confirm). The reason list is an operational vocabulary, not clinical content; it is listed in the clinician sheet for confirmation. No free text.
--   * `seq`: an identity column that orders the events of a patient (the application serialises the events of one patient with an advisory lock, so seq order is event order).
--   * `confirmed_by` is the ACTOR of either kind (the column keeps its 0023 name; renaming it would rewrite the append-only table's contract).
--
-- NO NEW GRANT. The 0023 table-level `grant insert, select ... to careflow_app` covers the new columns, and the 0023 policies already decide who writes: front desk of the clinic, as themselves
-- (confirmed_by = auth.uid()), origin pinned to the patient's by the 0023 trigger. Nobody can update or delete (0023 triggers), including the owner.
--
-- ONE POLICY CHANGE (agents.review_tasks_insert, from 0013): a revoke raises ONE review task for a nurse or doctor (kind `identity_confirmation_revoked`) so a person re-checks what was linked
-- while the record was confirmed. Front desk wrote the revoke, so the insert is made in front desk's session, and the policy gains exactly one clause for that kind, assigned to nurse or doctor.
-- Front desk still CANNOT read that task (review_tasks_select is untouched), so the writer inserts without RETURNING or ON CONFLICT (both need the SELECT policy). The task's priority and role are
-- CLINICAL-PLACEHOLDER: needs clinician sign-off; the application takes them from required settings with no default and says so visibly when they are unset.
--
-- RESIDUALS. (1) A revoke does not unlink referrals already linked: the task lists the patient and the window and the nurse looks them up (front desk cannot read agents.triage_proposals, so the
-- count is deliberately not computed in front desk's session: an invisible zero would be a wrong zero). (2) The owner can still disable the append-only triggers (as in 0012, 0023).

alter table mpi.identity_confirmations drop constraint identity_confirmations_patient_id_key;

alter table mpi.identity_confirmations
    add column kind text not null default 'confirm' check (kind in ('confirm', 'revoke')),
    add column reason text check (reason in ('wrong_person', 'planted_or_suspect', 'mistaken_click')),
    add column seq bigint generated always as identity,
    add constraint identity_confirmations_reason_matches_kind check ((kind = 'revoke') = (reason is not null));

create unique index identity_confirmations_seq_idx on mpi.identity_confirmations (seq);
create index identity_confirmations_latest_idx on mpi.identity_confirmations (patient_id, seq desc);

drop policy review_tasks_insert on agents.review_tasks;
create policy review_tasks_insert on agents.review_tasks for insert to careflow_app
    with check (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])
        or (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']) and kind = 'unmerge_review')
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'identity_confirmation_revoked' and assigned_role in ('nurse', 'doctor'))
    );
