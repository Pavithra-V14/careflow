-- 0037_front_desk_scheduling_checkpoints: the Scheduling graph's runs become durable (Phase 3 follow-up, owner decision of 2026-11-06 (a); BUILD-SPEC.md 7.2; CLAUDE.md rules 5, 6, 8, 10).
--
-- The three checkpoint tables already exist (0021, our own tables, forced RLS, append-only history). Their policies allow the queue worker, nurse and doctor only, but the Scheduling graph is driven by
-- the signed-in FRONT-DESK person under their own session (no worker identity in Phase 3), so a durable run needs front desk to write and read ITS OWN graph's rows. This migration adds, and only adds:
--
--   * for front desk of the row's clinic: INSERT and SELECT on the three tables, and UPDATE of the special (negative idx) pending writes, ONLY where the thread id begins `scheduling:` (the Scheduling
--     graph's thread id is `scheduling:<slot id>`). Front desk therefore still cannot read or write a Front Door run (`front_door:<document id>`) or any other graph's checkpoint.
--
-- Nothing is removed or widened for anyone else; the append-only triggers, the column grants and the clinic scoping of 0021 are untouched. Policies are permissive and OR together, so these are new
-- ways in, each narrower than the existing ones.
--
-- WHAT IS IN A ROW. The Scheduling graph keeps only ids, fixed-word statuses and counts in its state (careflow/scheduling_graph.py; a test greps the stored bytes), never message text: the patient reply is
-- a stored message that the graph names by id. A graph whose state held patient text must pass an encrypting serializer before it runs.
--
-- RESIDUAL RISKS, stated. (1) Any front-desk person of the clinic can write ANY `scheduling:` thread of that clinic through SQL; the routes only ever write the run of the slot they act on, and the thread
-- id is not tied to a slot by a foreign key. The graph effects are guarded by their own rules (the hold policy, the booking policy), not by the checkpoint. (2) Rows accumulate: deletion is blocked
-- (0021) and retention is a later, audited package.

create policy graph_checkpoints_insert_front_desk_scheduling on agents.graph_checkpoints for insert to careflow_app
    with check (left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']));
create policy graph_checkpoints_select_front_desk_scheduling on agents.graph_checkpoints for select to careflow_app
    using (left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']));
create policy graph_checkpoint_blobs_insert_front_desk_scheduling on agents.graph_checkpoint_blobs for insert to careflow_app
    with check (left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']));
create policy graph_checkpoint_blobs_select_front_desk_scheduling on agents.graph_checkpoint_blobs for select to careflow_app
    using (left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']));
create policy graph_checkpoint_writes_insert_front_desk_scheduling on agents.graph_checkpoint_writes for insert to careflow_app
    with check (left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']));
create policy graph_checkpoint_writes_select_front_desk_scheduling on agents.graph_checkpoint_writes for select to careflow_app
    using (left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']));
create policy graph_checkpoint_writes_update_front_desk_scheduling on agents.graph_checkpoint_writes for update to careflow_app
    using (idx < 0 and left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (idx < 0 and left(thread_id, 11) = 'scheduling:' and iam.has_clinic_role(clinic_id, array['front_desk']));

do $$
declare n int;
begin
    select count(*) into n from pg_policies
     where schemaname = 'agents' and tablename in ('graph_checkpoints', 'graph_checkpoint_blobs', 'graph_checkpoint_writes') and policyname like '%front_desk_scheduling';
    if n <> 7 then
        raise exception '0037: expected 7 front-desk scheduling policies, found %', n;
    end if;
    if exists (select 1 from pg_policies where schemaname = 'agents' and tablename in ('graph_checkpoints', 'graph_checkpoint_blobs', 'graph_checkpoint_writes')
                and policyname like '%front_desk_scheduling' and (coalesce(qual, '') || coalesce(with_check, '')) not like '%scheduling:%') then
        raise exception '0037: a front-desk checkpoint policy lost its thread-prefix condition';
    end if;
end $$;
