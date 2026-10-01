-- 0005_audit_doctor_signoff_security_fix: make the named approver on a priority level genuine
-- (fixes the finding from the /security-review of 0004; CLAUDE.md rule 1).
--
-- In 0004 a doctor could insert or edit a priority level with `approved_by` set to ANY account, so a
-- clinical sign-off could be attributed to someone else. This migration closes both holes:
--   * INSERT: the approver must be the signed-in doctor themselves (approved_by = auth.uid()).
--   * UPDATE: the app role may change only `label` and `active`. approved_by, approved_at, rank, code and
--     the rest are immutable to it, enforced by column privileges (which, unlike an RLS WITH CHECK, can
--     see "this column is being changed"). A different rank is a different clinical decision: it is a
--     NEW level that its own clinician approves, with the old one deactivated, never an in-place edit.
-- The migrator (table owner) keeps full rights for administration; it is not an application path.

drop policy priority_levels_insert_doctor on sched.priority_levels;
drop policy priority_levels_update_doctor on sched.priority_levels;

create policy priority_levels_insert_doctor on sched.priority_levels for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['doctor']) and approved_by = auth.uid());

create policy priority_levels_update_doctor on sched.priority_levels for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['doctor']))
    with check (iam.has_clinic_role(clinic_id, array['doctor']));

revoke update on sched.priority_levels from careflow_app;
grant update (label, active) on sched.priority_levels to careflow_app;
