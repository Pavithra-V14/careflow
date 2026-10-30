-- 0026: bind the front-desk insert of an `unmerge_review` review task to a real unmerge (fix-forward of 0013; owner decision 2026-10-30; found by the rls-security-reviewer pass on 0025).
--
-- THE HOLE. 0013 let front desk and admin insert ANY task of kind `unmerge_review` with any idempotency key. Other writers use `on conflict (idempotency_key) do nothing` with predictable keys
-- (`redflag:...`, `dead-letter:...`, the triage and hold keys). A front-desk user who guessed such a key could pre-insert an `unmerge_review` task under it; the real writer's insert would
-- then do nothing and its code would treat its own escalation as already raised. A front-desk user could also create fake tasks for the nurses.
--
-- THE FIX. The `unmerge_review` clause now needs a real unmerge written by the same person, exactly as the 0025 revoke clause does: the task must be open and unacknowledged, assigned to a
-- nurse, and its idempotency key and source id must be `unmerge-review:<merge id>` and `merge:<merge id>` (the shapes careflow/identity.py writes) for a merge of the same clinic and origin
-- that is `unmerged` with `unmerged_by = auth.uid()`. Merge ids are random uuids. The unmerge updates the merge row BEFORE it inserts the task, in the same transaction, so the check sees it.
--   * ADMIN LOSES THE INSERT, not the read. 0013 listed admin beside front desk, but only front desk can unmerge (merges_update_front_desk), so an admin could never satisfy the new binding;
--     the clause is written for front desk alone. Admin still SEES unmerge reviews (review_tasks_select, unchanged).
--   * Nurse, doctor and queue_worker keep their unrestricted insert (unchanged), and the 0025 revoke clause is reproduced exactly.
--   * review_tasks_select and review_tasks_update are untouched. No grant, no role.
--
-- The link must have been ACTIVE (`activated_at` is set only on activation; the code raises a task only for a link that had been active, none for a cancelled proposal), so a cancelled proposal
-- cannot be used to mint a nurse task.
--
-- RESIDUAL. (1) The task's `provenance` JSON is written by the inserting role and is not constrained by row-level security (the real writer builds it; front desk could put any JSON in its own
-- task). (2) The nurse/doctor/worker clause is still any kind with any key (those roles are trusted to write review tasks); this migration narrows front desk only.

drop policy review_tasks_insert on agents.review_tasks;
create policy review_tasks_insert on agents.review_tasks for insert to careflow_app
    with check (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])
        or (
            iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'unmerge_review' and assigned_role = 'nurse'
            and status = 'open' and acknowledged_by is null
            and exists (
                select 1 from mpi.merges m
                where m.status = 'unmerged' and m.activated_at is not null and m.clinic_id = review_tasks.clinic_id and m.origin = review_tasks.origin and m.unmerged_by = auth.uid()
                  and review_tasks.idempotency_key = 'unmerge-review:' || m.id::text
                  and review_tasks.source_message_id = 'merge:' || m.id::text
            )
        )
        or (
            iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'identity_confirmation_revoked' and assigned_role in ('nurse', 'doctor')
            and status = 'open' and acknowledged_by is null
            and exists (
                select 1 from mpi.identity_confirmations e
                where e.kind = 'revoke' and e.clinic_id = review_tasks.clinic_id and e.origin = review_tasks.origin and e.confirmed_by = auth.uid()
                  and review_tasks.idempotency_key = 'identity-revoke-review:' || e.id::text
                  and review_tasks.source_message_id = 'identity-revoke:' || e.id::text
            )
        )
    );
