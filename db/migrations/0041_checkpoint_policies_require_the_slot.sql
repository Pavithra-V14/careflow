-- 0041_checkpoint_policies_require_the_slot: closes the residual risk stated in 0037 (reviewer finding, 2026-11-06; owner decision 2026-11-06 item 6; BUILD-SPEC.md 7.2; CLAUDE.md rules 8, 10).
--
-- 0037 let front desk of a clinic write ANY checkpoint thread whose id begins `scheduling:` in its clinic, whether or not such a slot exists. This migration replaces the seven policies (0037 itself is
-- not edited) with the same rules PLUS: the text after `scheduling:` must be the id of a slot that exists IN THE SAME CLINIC and that the caller can see (the subquery runs under the caller's own
-- row-level security on sched.slots, which front desk of the clinic satisfies). A thread for a slot that does not exist, or lives in another clinic, can no longer be written or read by front desk.
--
-- No other role's policy is touched. RESIDUAL: front desk of the clinic can still write the thread of ANY slot of its own clinic (not only the slot it is acting on); the graph effects remain
-- guarded by their own rules (the hold and booking policies). The match is on the uuid itself (a guarded cast inside CASE, so a thread id that is not a canonical lower-case uuid matches nothing and cannot raise), which lets the slot primary key be used. The names are kept so a reader of 0037 finds the same policies.

drop policy graph_checkpoints_insert_front_desk_scheduling on agents.graph_checkpoints;
create policy graph_checkpoints_insert_front_desk_scheduling on agents.graph_checkpoints for insert to careflow_app
    with check (left(graph_checkpoints.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoints.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoints.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoints.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoints.clinic_id));
drop policy graph_checkpoints_select_front_desk_scheduling on agents.graph_checkpoints;
create policy graph_checkpoints_select_front_desk_scheduling on agents.graph_checkpoints for select to careflow_app
    using (left(graph_checkpoints.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoints.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoints.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoints.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoints.clinic_id));
drop policy graph_checkpoint_blobs_insert_front_desk_scheduling on agents.graph_checkpoint_blobs;
create policy graph_checkpoint_blobs_insert_front_desk_scheduling on agents.graph_checkpoint_blobs for insert to careflow_app
    with check (left(graph_checkpoint_blobs.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoint_blobs.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoint_blobs.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoint_blobs.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoint_blobs.clinic_id));
drop policy graph_checkpoint_blobs_select_front_desk_scheduling on agents.graph_checkpoint_blobs;
create policy graph_checkpoint_blobs_select_front_desk_scheduling on agents.graph_checkpoint_blobs for select to careflow_app
    using (left(graph_checkpoint_blobs.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoint_blobs.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoint_blobs.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoint_blobs.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoint_blobs.clinic_id));
drop policy graph_checkpoint_writes_insert_front_desk_scheduling on agents.graph_checkpoint_writes;
create policy graph_checkpoint_writes_insert_front_desk_scheduling on agents.graph_checkpoint_writes for insert to careflow_app
    with check (left(graph_checkpoint_writes.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoint_writes.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoint_writes.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoint_writes.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoint_writes.clinic_id));
drop policy graph_checkpoint_writes_select_front_desk_scheduling on agents.graph_checkpoint_writes;
create policy graph_checkpoint_writes_select_front_desk_scheduling on agents.graph_checkpoint_writes for select to careflow_app
    using (left(graph_checkpoint_writes.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoint_writes.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoint_writes.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoint_writes.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoint_writes.clinic_id));
drop policy graph_checkpoint_writes_update_front_desk_scheduling on agents.graph_checkpoint_writes;
create policy graph_checkpoint_writes_update_front_desk_scheduling on agents.graph_checkpoint_writes for update to careflow_app
    using (idx < 0 and left(graph_checkpoint_writes.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoint_writes.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoint_writes.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoint_writes.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoint_writes.clinic_id))
    with check (idx < 0 and left(graph_checkpoint_writes.thread_id, 11) = 'scheduling:' and iam.has_clinic_role(graph_checkpoint_writes.clinic_id, array['front_desk'])
       and exists (select 1 from sched.slots s where s.id = (case when substr(graph_checkpoint_writes.thread_id, 12) ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then substr(graph_checkpoint_writes.thread_id, 12)::uuid end) and s.clinic_id = graph_checkpoint_writes.clinic_id));

do $$
declare n int;
begin
    select count(*) into n from pg_policies
     where schemaname = 'agents' and tablename in ('graph_checkpoints', 'graph_checkpoint_blobs', 'graph_checkpoint_writes') and policyname like '%front_desk_scheduling';
    if n <> 7 then
        raise exception '0041: expected 7 front-desk scheduling policies, found %', n;
    end if;
    if exists (select 1 from pg_policies where schemaname = 'agents' and tablename in ('graph_checkpoints', 'graph_checkpoint_blobs', 'graph_checkpoint_writes')
                and policyname like '%front_desk_scheduling'
                and (coalesce(qual, '') || coalesce(with_check, '')) !~ 'scheduling:.*has_clinic_role.*front_desk.*slots.*clinic_id') then
        raise exception '0041: a front-desk checkpoint policy lost its prefix, role, slot or clinic condition';
    end if;
    if (select count(*) from pg_policies where schemaname = 'agents' and policyname like '%front_desk_scheduling' and cmd = 'UPDATE') <> 1
       or (select count(*) from pg_policies where schemaname = 'agents' and policyname like '%front_desk_scheduling' and cmd = 'INSERT') <> 3
       or (select count(*) from pg_policies where schemaname = 'agents' and policyname like '%front_desk_scheduling' and cmd = 'SELECT') <> 3 then
        raise exception '0041: the front-desk scheduling policies are not 3 insert, 3 select and 1 update';
    end if;
end $$;
