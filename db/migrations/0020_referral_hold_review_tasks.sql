-- 0020_referral_hold_review_tasks: a review task for a referral the Front Door could not take further, visible to and resolvable by front desk when it is theirs
-- (BUILD-SPEC.md §16.2, §17.2, §12.1; CLAUDE.md rules 3, 8, 10). Owner decision of 2026-10-24: a run must never end silently.
--
-- The graph writes a task of kind `referral_hold` for three outcomes:
--   * the letter has no usable date of birth or an unverified name (§17.2: it "routes to the front desk, who supply the missing detail") -> assigned to front_desk;
--   * the extraction was unavailable (no approved route, a model outage) -> assigned to a nurse;
--   * the red-flag screening was UNASSESSABLE (no usable rules, a rule failed to compile): unknown is not clear -> assigned to a nurse.
-- The task holds a short fixed-word reason list and the document id (as source_message_id = 'document:<id>'); NEVER the letter, a name, a date or a phone.
-- The task's priority is a CLINICAL-PLACEHOLDER argument of the writer (careflow/triage_store.py), never a default.
--
-- WHAT CHANGES, and nothing else. 0013 limited what front desk and admin can see of review_tasks to kind `unmerge_review`, and 0007 let only nurse and doctor
-- acknowledge or resolve. A front-desk task would be invisible and unresolvable, so the two policies are REPLACED (dropped and recreated, as 0013 did) with
-- exactly the old rules plus ONE new clause each, for kind = 'referral_hold' AND assigned_role = 'front_desk':
--   select: front desk of the clinic may also read such a task;
--   update (acknowledge/resolve): front desk of the clinic may also act on such a task, only as themselves (acknowledged_by = auth.uid()).
-- Insert is unchanged (queue worker, nurse, doctor may insert; front desk may still insert only unmerge_review). Red-flag tasks, triage-proposal tasks,
-- screening tasks and every other kind stay hidden from front desk and admin. A nurse-assigned hold stays invisible to front desk.
--
-- RESIDUAL RISKS: (1) a front-desk user can read the reasons of front-desk holds only: fixed words and a document id. (2) Nothing here makes the task appear
-- anywhere: the review console is a later package; until it exists a person must query the table. (3) No audit.log row is written. (4) Like the nurse/doctor rule it extends (0007), the update policy lets the actor set a resolved hold back to open or take over a hold another front-desk user acknowledged: there is no status-transition trigger and no audit row. (5) An identity key conflict (identity_key_conflict) and an empty letter (empty_letter) are held for a NURSE; data-entry refusals from the identity module (identity_refused) go to front desk.

drop policy review_tasks_select on agents.review_tasks;
drop policy review_tasks_update on agents.review_tasks;

create policy review_tasks_select on agents.review_tasks for select to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])
        or (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']) and kind = 'unmerge_review')
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'referral_hold' and assigned_role = 'front_desk')
    );

create policy review_tasks_update on agents.review_tasks for update to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'referral_hold' and assigned_role = 'front_desk')
    )
    with check (
        (
            iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
            or (iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'referral_hold' and assigned_role = 'front_desk')
        )
        and (acknowledged_by is null or acknowledged_by = auth.uid())
    );
