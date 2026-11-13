-- 0073_front_desk_supervisor_and_revoke_alert (owner instruction 2026-11-13; pre-launch item 23; docs/design/identity-confirmation-revoke.md; CLAUDE.md rules 1, 3, 5, 8, 10). Fix forward: no merged migration is edited.
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off  (WHO re-checks after a revoke, and WHICH reasons raise an admin alert, are clinician items 23 and 24: this migration builds the role and the doors, and decides nothing)
--
-- 1. A new staff role `front_desk_supervisor`. It ADDS to front desk and grants nothing by itself: it can be granted only to a person who already holds front desk at that clinic (the grant function refuses
--    otherwise), it goes when front desk is revoked, and it cannot be given at invitation. It lets its holder see, take and close ONE kind of task: `identity_confirmation_revoked` tasks assigned to the
--    supervisor role. It sees no other task, no message, no result, no note. A task is assigned to the supervisor only if the clinic's required setting says so (CAREFLOW_REVOKE_TASK_ROLE); unset = no task is raised, as before.
-- 2. A new alert class `identity_revoked_suspect`, for an ADMIN, raised by the front-desk person who made a revoke, bound to that real revoke event (the same pattern as the canary approval alert, 0039): the alert
--    carries the event id and a count of one, nothing else. WHICH reasons raise it is the required setting CAREFLOW_REVOKE_ALERT_REASONS (unset = never).
-- 3. Pre-launch item 16 (part): the nurse, doctor and worker insert clause of review tasks now pins `status = 'open'` and `acknowledged_by is null`, as the front-desk clauses already did. (The key-squatting half, binding
--    those roles' keys to their source, is NOT done: those roles are trusted to write any kind.)
-- NEW GRANTS: none. Policies are widened below; the 0039 column grants already cover the alert insert.

alter table iam.staff_roles drop constraint staff_roles_role_check;
alter table iam.staff_roles add constraint staff_roles_role_check
    check (role in ('front_desk', 'front_desk_supervisor', 'nurse', 'doctor', 'admin', 'business_owner', 'auditor', 'queue_worker'));

alter table agents.review_tasks drop constraint review_tasks_assigned_role_check;
alter table agents.review_tasks add constraint review_tasks_assigned_role_check
    check (assigned_role in ('nurse', 'doctor', 'front_desk', 'front_desk_supervisor'));

alter table ops.alerts drop constraint alerts_class_check;
alter table ops.alerts add constraint alerts_class_check
    check (class in ('red_flag_overdue', 'task_overdue', 'task_escalated', 'dead_letters', 'queue_age', 'canary_approved', 'spend_runaway', 'delivery_failed', 'fallback_spike', 'tracing_cap', 'identity_revoked_suspect'));
alter table ops.alerts add constraint alerts_revoked_suspect_is_for_admin
    check (class <> 'identity_revoked_suspect' or (audience_role = 'admin' and ref is not null));

-- the task: who may read, take and close it
drop policy review_tasks_select on agents.review_tasks;
create policy review_tasks_select on agents.review_tasks for select to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker'])
        or (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']) and kind = 'unmerge_review')
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'referral_hold' and assigned_role = 'front_desk')
        or (iam.has_clinic_role(clinic_id, array['front_desk_supervisor']) and kind = 'identity_confirmation_revoked' and assigned_role = 'front_desk_supervisor')
    );

drop policy review_tasks_update on agents.review_tasks;
create policy review_tasks_update on agents.review_tasks for update to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
        or (iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'referral_hold' and assigned_role = 'front_desk')
        or (iam.has_clinic_role(clinic_id, array['front_desk_supervisor']) and kind = 'identity_confirmation_revoked' and assigned_role = 'front_desk_supervisor')
    )
    with check (
        (
            iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
            or (iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'referral_hold' and assigned_role = 'front_desk')
            or (iam.has_clinic_role(clinic_id, array['front_desk_supervisor']) and kind = 'identity_confirmation_revoked' and assigned_role = 'front_desk_supervisor')
        )
        and (acknowledged_by is null or acknowledged_by = auth.uid())
    );

drop policy review_tasks_insert on agents.review_tasks;
create policy review_tasks_insert on agents.review_tasks for insert to careflow_app
    with check (
        (iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'queue_worker']) and status = 'open' and acknowledged_by is null)  -- pre-launch item 16: a task is born open and untaken, whoever writes it
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
            iam.has_clinic_role(clinic_id, array['front_desk']) and kind = 'identity_confirmation_revoked' and assigned_role in ('nurse', 'doctor', 'front_desk_supervisor')
            and status = 'open' and acknowledged_by is null
            and exists (
                select 1 from mpi.identity_confirmations e
                where e.kind = 'revoke' and e.clinic_id = review_tasks.clinic_id and e.origin = review_tasks.origin and e.confirmed_by = auth.uid()
                  and review_tasks.idempotency_key = 'identity-revoke-review:' || e.id::text
                  and review_tasks.source_message_id = 'identity-revoke:' || e.id::text
            )
        )
    );

-- the alert: raised by the person who made the revoke, bound to that event
create policy alerts_insert_revoke_alert on ops.alerts for insert to careflow_app
    with check (
        class = 'identity_revoked_suspect' and audience_role = 'admin' and alert_count = 1 and idempotency_key = 'identity-revoked-alert:' || ref
        and iam.has_clinic_role(clinic_id, array['front_desk'])
        and exists (
            select 1 from mpi.identity_confirmations e
            where e.id::text = alerts.ref and e.kind = 'revoke' and e.clinic_id = alerts.clinic_id and e.origin = alerts.origin and e.confirmed_by = auth.uid()
        )
    );

-- the grant and revoke functions (0034) with the new role in their closed list
create or replace function iam.grant_staff_role(p_account_id uuid, p_clinic_id uuid, p_role text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_origin text;
    v_added integer;
begin
    if v_actor is null or not iam.aal2_session() or p_account_id is null or p_account_id = v_actor or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'front_desk_supervisor', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
       or not iam.has_clinic_role(p_clinic_id, array['admin'])
       -- the supervisor role ADDS to front desk: it is granted only to someone who already holds front desk at this clinic (it grants nothing else by itself)
       or (p_role = 'front_desk_supervisor' and not exists (select 1 from iam.staff_roles f where f.account_id = p_account_id and f.clinic_id = p_clinic_id and f.role = 'front_desk' and f.revoked_at is null))
       or not exists (select 1 from iam.accounts a where a.id = p_account_id and a.account_type = 'staff' and a.status in ('invited', 'active', 'suspended')) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    if p_role = 'front_desk_supervisor' then
        perform pg_advisory_xact_lock(hashtextextended('staff-admins:' || p_clinic_id::text, 0));  -- the same lock as the revoke: a grant cannot race a front-desk revoke
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    insert into iam.staff_roles (account_id, clinic_id, role, granted_by, origin) values (p_account_id, p_clinic_id, p_role, v_actor, v_origin)
    on conflict (account_id, clinic_id, role) where revoked_at is null do nothing;
    get diagnostics v_added = row_count;
    if v_added = 0 then
        return false;  -- already held: a replay or a no-op writes nothing more
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.grant_role', 'account', p_account_id::text, jsonb_build_object('role', p_role, 'role_scope', iam.role_scope()), 'sys:staff-grant:' || gen_random_uuid()::text);
    return true;
end
$$;

create or replace function iam.revoke_staff_role(p_account_id uuid, p_clinic_id uuid, p_role text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_origin text;
    v_closed integer;
begin
    if v_actor is null or not iam.aal2_session() or p_account_id is null or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'front_desk_supervisor', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
       or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('staff-admins:' || p_clinic_id::text, 0));  -- serialise changes to a clinic's admin set
    if p_role = 'admin' and exists (select 1 from iam.staff_roles where account_id = p_account_id and clinic_id = p_clinic_id and role = 'admin' and revoked_at is null)
       and not exists (
        select 1 from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id
         where sr.clinic_id = p_clinic_id and sr.role = 'admin' and sr.revoked_at is null and a.status = 'active' and a.account_type = 'staff' and sr.account_id <> p_account_id) then
        raise exception 'a clinic must keep an active administrator' using errcode = 'check_violation';
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    update iam.staff_roles set revoked_at = clock_timestamp()
     where account_id = p_account_id and clinic_id = p_clinic_id and role = p_role and revoked_at is null;
    get diagnostics v_closed = row_count;
    if v_closed > 0 and p_role = 'front_desk' then  -- the supervisor role goes with front desk (it only adds to it)
        update iam.staff_roles set revoked_at = clock_timestamp()
         where account_id = p_account_id and clinic_id = p_clinic_id and role = 'front_desk_supervisor' and revoked_at is null;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.revoke_role', 'account', p_account_id::text,
                jsonb_build_object('role', 'front_desk_supervisor', 'with', 'front_desk', 'role_scope', iam.role_scope()), 'sys:staff-revoke:' || gen_random_uuid()::text);
    end if;
    if v_closed = 0 then
        return false;  -- nothing live to revoke: a replay or a no-op writes nothing more
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.revoke_role', 'account', p_account_id::text, jsonb_build_object('role', p_role, 'role_scope', iam.role_scope()), 'sys:staff-revoke:' || gen_random_uuid()::text);
    return true;
end
$$;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r)
           and (has_function_privilege(r, 'iam.grant_staff_role(uuid,uuid,text,text)', 'execute') or has_function_privilege(r, 'iam.revoke_staff_role(uuid,uuid,text,text)', 'execute')) then
            raise exception '0073: the staff role functions must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'iam.grant_staff_role(uuid,uuid,text,text)', 'execute') or has_function_privilege('public', 'iam.revoke_staff_role(uuid,uuid,text,text)', 'execute') then
        raise exception '0073: the staff role functions must not be executable by PUBLIC';
    end if;
    if pg_get_functiondef('iam.grant_staff_role(uuid,uuid,text,text)'::regprocedure) not like '%iam.aal2_session()%'
       or pg_get_functiondef('iam.revoke_staff_role(uuid,uuid,text,text)'::regprocedure) not like '%iam.aal2_session()%' then
        raise exception '0073: the staff role functions must keep the second-factor check';
    end if;
    if (select count(*) from pg_policies where schemaname = 'agents' and tablename = 'review_tasks') <> 3 then
        raise exception '0073: agents.review_tasks must have exactly three policies';
    end if;
    if (select count(*) from pg_policies where schemaname = 'ops' and tablename = 'alerts') <> 5 then
        raise exception '0073: ops.alerts must have exactly five policies';
    end if;
end $$;
