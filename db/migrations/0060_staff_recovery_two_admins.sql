-- 0060_staff_recovery_two_admins (owner request 2026-11-08; BUILD-SPEC.md 18.1: "Staff account recovery goes through an admin plus a second admin — no self-service bypass of MFA"; CLAUDE.md rules 5, 8, 10).
--
-- A staff member who lost their second factor cannot recover it themselves. One admin of the clinic REQUESTS recovery for that account (iam.request_staff_recovery); a SECOND admin, a different person who is
-- neither the requester nor the account being recovered, APPROVES it (iam.approve_staff_recovery); the application then asks the Auth service to reset the account's factors through the Auth admin PORT
-- (careflow/auth_admin.py; no real adapter is wired by default, so the complete route answers 503) and records the result with iam.complete_staff_recovery. Either admin may cancel before completion. Every step
-- is an audit row written by the function. The request table is written only by these functions; the application role can only read it (admins of the clinic, second factor required).
--
-- WHAT THIS DOES NOT DO: it does not reset a factor in the database (the factor lives in Supabase Auth). Until the Auth admin call is verified and wired, an approved request waits.

create table iam.recovery_requests (
    id                uuid primary key default gen_random_uuid(),
    clinic_id         uuid not null references iam.clinics (id),
    origin            text not null check (origin in ('live', 'test', 'eval', 'canary')),
    target_account_id uuid not null references iam.accounts (id),
    requested_by      uuid not null references iam.accounts (id),
    approved_by       uuid references iam.accounts (id),
    status            text not null default 'requested' check (status in ('requested', 'approved', 'completed', 'cancelled')),
    created_at        timestamptz not null default clock_timestamp(),
    decided_at        timestamptz,
    idempotency_key   text not null check (length(idempotency_key) between 1 and 300),
    constraint recovery_key_per_clinic unique (clinic_id, idempotency_key),
    constraint recovery_not_self check (requested_by <> target_account_id),
    constraint recovery_second_admin check (approved_by is null or (approved_by <> requested_by and approved_by <> target_account_id)),
    constraint recovery_state_shape check (
        (status = 'requested' and approved_by is null and decided_at is null)
        or (status = 'approved' and approved_by is not null and decided_at is not null)
        or (status = 'completed' and approved_by is not null and decided_at is not null)
        or (status = 'cancelled' and decided_at is not null)
    )
);
-- at most ONE open recovery per account
create unique index recovery_one_open_idx on iam.recovery_requests (target_account_id) where status in ('requested', 'approved');

create function iam.recovery_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'iam.recovery_requests rows are never deleted' using errcode = 'restrict_violation';
    end if;
    -- forward only: requested -> approved | cancelled; approved -> completed | cancelled; nothing else about the row changes except the approver and the decision time
    if (to_jsonb(new) - 'status' - 'approved_by' - 'decided_at') <> (to_jsonb(old) - 'status' - 'approved_by' - 'decided_at') then
        raise exception 'only the status of a recovery request can change' using errcode = 'restrict_violation';
    end if;
    if not ((old.status, new.status) in (('requested', 'approved'), ('requested', 'cancelled'), ('approved', 'completed'), ('approved', 'cancelled'))) then
        raise exception 'that recovery status change is not allowed' using errcode = 'restrict_violation';
    end if;
    if new.status <> 'approved' and new.approved_by is distinct from old.approved_by then
        raise exception 'only the approval sets the approver' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;
revoke all on function iam.recovery_guard() from public;
create trigger recovery_guard before update or delete on iam.recovery_requests for each row execute function iam.recovery_guard();
create trigger recovery_no_truncate before truncate on iam.recovery_requests for each statement execute function iam.recovery_guard();

alter table iam.recovery_requests enable row level security;
alter table iam.recovery_requests force row level security;
revoke all on iam.recovery_requests from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.recovery_requests from %I', r);
            execute format('revoke all on function iam.recovery_guard() from %I', r);
        end if;
    end loop;
end $$;
grant select on iam.recovery_requests to careflow_app;
create policy recovery_select on iam.recovery_requests for select to careflow_app using (iam.has_clinic_role(clinic_id, array['admin']) and iam.aal2_session());

-- ---- request (an admin) ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
create function iam.request_staff_recovery(p_clinic_id uuid, p_target_account_id uuid, p_idempotency_key text) returns table (request_id uuid, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_prior  record;
    v_id     uuid;
begin
    if v_actor is null or p_clinic_id is null or p_target_account_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 300
       or p_target_account_id = v_actor or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'recovery refused' using errcode = '42501';
    end if;
    select r.id, r.requested_by, r.target_account_id into v_prior from iam.recovery_requests r where r.clinic_id = p_clinic_id and r.idempotency_key = p_idempotency_key;
    if found then
        if v_prior.requested_by <> v_actor or v_prior.target_account_id <> p_target_account_id then
            raise exception 'recovery refused' using errcode = '42501';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    -- the account must be a STAFF account with a live role in this clinic, and in NO other clinic: its second factor belongs to the whole account, so one clinic's admins must not be able to weaken
    -- another clinic's staff (an account with roles in several clinics is recovered by the operator, outside this path: an owner decision named in the gap audit)
    if not exists (select 1 from iam.accounts a join iam.staff_roles sr on sr.account_id = a.id
                    where a.id = p_target_account_id and a.account_type = 'staff' and a.status = 'active' and sr.clinic_id = p_clinic_id and sr.revoked_at is null)
       or exists (select 1 from iam.staff_roles o where o.account_id = p_target_account_id and o.clinic_id <> p_clinic_id and o.revoked_at is null) then
        raise exception 'recovery refused' using errcode = '42501';
    end if;
    select c.origin into v_origin from iam.clinics c where c.id = p_clinic_id;
    insert into iam.recovery_requests (clinic_id, origin, target_account_id, requested_by, idempotency_key) values (p_clinic_id, v_origin, p_target_account_id, v_actor, p_idempotency_key)
    on conflict do nothing returning id into v_id;
    if v_id is null then
        -- a concurrent call with the same key won (its replay is returned), or another recovery is already open for this account (refused)
        select r.id, r.requested_by, r.target_account_id into v_prior from iam.recovery_requests r where r.clinic_id = p_clinic_id and r.idempotency_key = p_idempotency_key;
        if found and v_prior.requested_by = v_actor and v_prior.target_account_id = p_target_account_id then
            return query select v_prior.id, false;
            return;
        end if;
        raise exception 'recovery refused' using errcode = '42501';
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.recovery_request', 'account', p_target_account_id::text, jsonb_build_object('request_id', v_id::text), 'sys:staff-recovery-request:' || v_id::text);
    return query select v_id, true;
end
$$;

-- ---- approve (a second admin) / complete / cancel --------------------------------------------------------------------------------------------------------------------------------------------------
create function iam.resolve_staff_recovery(p_request_id uuid, p_action text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_r     record;
begin
    if v_actor is null or p_request_id is null or p_action is null or p_action not in ('approve', 'complete', 'cancel') or not iam.aal2_session() then
        raise exception 'recovery refused' using errcode = '42501';
    end if;
    select r.id, r.clinic_id, r.origin, r.target_account_id, r.requested_by, r.approved_by, r.status into v_r from iam.recovery_requests r where r.id = p_request_id for update;
    if not found or not iam.has_clinic_role(v_r.clinic_id, array['admin']) then
        raise exception 'recovery refused' using errcode = '42501';
    end if;
    if p_action in ('approve', 'complete') and v_r.status in ('requested', 'approved')
       and not exists (select 1 from iam.accounts a join iam.staff_roles sr on sr.account_id = a.id
                        where a.id = v_r.target_account_id and a.account_type = 'staff' and a.status = 'active' and sr.clinic_id = v_r.clinic_id and sr.revoked_at is null)
       or exists (select 1 from iam.staff_roles o where o.account_id = v_r.target_account_id and o.clinic_id <> v_r.clinic_id and o.revoked_at is null) then  -- the account-wide factor: a role gained elsewhere since the request stops it too
        return 'target_gone';  -- the account was suspended or lost its role since the request
    end if;
    if p_action = 'approve' then
        if v_r.status = 'approved' and v_r.approved_by = v_actor then return 'approved'; end if;  -- a replay by the approver
        if v_r.status <> 'requested' then return 'not_requested'; end if;
        if v_actor = v_r.requested_by or v_actor = v_r.target_account_id then return 'same_person'; end if;  -- a SECOND admin: neither the requester nor the account being recovered
        update iam.recovery_requests set status = 'approved', approved_by = v_actor, decided_at = clock_timestamp() where id = p_request_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_r.origin, v_r.clinic_id, v_actor, 'admin', 'staff.recovery_approve', 'account', v_r.target_account_id::text, jsonb_build_object('request_id', p_request_id::text), 'sys:staff-recovery-approve:' || p_request_id::text);
        return 'approved';
    elsif p_action = 'complete' then
        if v_r.status = 'completed' then return 'completed'; end if;
        if v_r.status <> 'approved' then return 'not_approved'; end if;
        if v_actor = v_r.target_account_id then return 'same_person'; end if;
        update iam.recovery_requests set status = 'completed' where id = p_request_id;
        -- the caller resets the factors through the Auth admin port ONLY on this answer, in this same transaction: if the port fails the request is rolled back to approved
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_r.origin, v_r.clinic_id, v_actor, 'admin', 'staff.recovery_complete', 'account', v_r.target_account_id::text, jsonb_build_object('request_id', p_request_id::text), 'sys:staff-recovery-complete:' || p_request_id::text);
        return 'completed_now';
    else
        if v_r.status = 'cancelled' then return 'cancelled'; end if;
        if v_r.status not in ('requested', 'approved') then return 'not_open'; end if;
        if v_actor = v_r.target_account_id then return 'same_person'; end if;
        update iam.recovery_requests set status = 'cancelled', decided_at = clock_timestamp() where id = p_request_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_r.origin, v_r.clinic_id, v_actor, 'admin', 'staff.recovery_cancel', 'account', v_r.target_account_id::text, jsonb_build_object('request_id', p_request_id::text), 'sys:staff-recovery-cancel:' || p_request_id::text);
        return 'cancelled';
    end if;
end
$$;

revoke all on function iam.request_staff_recovery(uuid, uuid, text), iam.resolve_staff_recovery(uuid, text) from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.request_staff_recovery(uuid, uuid, text), iam.resolve_staff_recovery(uuid, text) from %I', r);
        end if;
    end loop;
end $$;
grant execute on function iam.request_staff_recovery(uuid, uuid, text), iam.resolve_staff_recovery(uuid, text) to careflow_app;

do $$
declare r text; f text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0060: the migrating role must bypass row-level security';
    end if;
    if has_any_column_privilege('careflow_app', 'iam.recovery_requests', 'insert') or has_any_column_privilege('careflow_app', 'iam.recovery_requests', 'update') or has_table_privilege('careflow_app', 'iam.recovery_requests', 'delete') then
        raise exception '0060: recovery requests are written only by their functions';
    end if;
    foreach f in array array['iam.request_staff_recovery(uuid,uuid,text)', 'iam.resolve_staff_recovery(uuid,text)'] loop
        if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0060: % must be executable by the application login and not by PUBLIC', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0060: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
    if has_function_privilege('public', 'iam.recovery_guard()', 'execute') then
        raise exception '0060: the guard function must not be executable by PUBLIC';
    end if;
end $$;
