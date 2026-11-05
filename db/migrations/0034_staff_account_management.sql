-- 0034_staff_account_management: invite, grant, revoke, suspend and activate staff accounts (pre-approved by the owner 2026-11-05; docs/design/staff-account-management.md).
--
-- Until now accounts and role grants were created by a script run by the owner (scripts/seed_dev_staff.py); the application role could not write to iam at all. Rather than hand the application role INSERT
-- and UPDATE on iam.accounts and iam.staff_roles (which would let any bug or any admin session do anything to any account), every change goes through ONE of five SECURITY DEFINER functions (pinned
-- search_path), each of which does exactly one thing, checks who the caller is INSIDE, and writes its own audit row in the same transaction. Nothing else about iam changes: the tables stay SELECT-only
-- for the application role.
--
-- WHO MAY DO WHAT (checked in the function, through iam.has_clinic_role, so the active-role header narrows it):
--   * invite_staff_account, grant_staff_role, revoke_staff_role: an ACTIVE ADMIN of the clinic concerned. The roles that can be granted are front_desk, nurse, doctor, admin, auditor and business_owner;
--     NEVER queue_worker (worker accounts stay operator-created: a worker identity must not be creatable from a web session).
--   * set_staff_account_status (suspend and reinstate): an admin of EVERY clinic where the target holds a live grant (so one clinic's admin cannot suspend someone who also works for another clinic), never
--     on oneself, and only active <-> suspended. An admin can NOT activate an invited account: activation is the person's own act.
--   * activate_own_account: the person themselves, once their token shows a verified second factor (aal2): invited -> active. So nobody becomes active without having enrolled a second factor.
--   * A clinic can never be left without an active administrator by a revoke (check_violation).
-- The auth user (the sign-in) is created OUTSIDE the database by the Auth admin API behind a port; these functions only record the account and its grant. No e-mail or name is ever stored here or in the
-- audit row (details hold the closed role word and the role scope only).
--
-- NEW GRANTS (grants ledger): EXECUTE on the five functions below to careflow_app. Everything else is unchanged. Refusals that could reveal whether an account exists raise ONE generic error.

create function iam.invite_staff_account(p_account_id uuid, p_clinic_id uuid, p_role text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_origin text;
    v_key text := 'audit:staff-invite:' || coalesce(p_account_id::text, '') || ':' || coalesce(p_clinic_id::text, '') || ':' || coalesce(p_idempotency_key, '');
begin
    if v_actor is null or p_account_id is null or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
       or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    if exists (select 1 from audit.log where idempotency_key = v_key) then
        return false;  -- a replay: nothing more is written
    end if;
    if exists (select 1 from iam.accounts where id = p_account_id) then
        raise exception 'staff change refused' using errcode = '42501';  -- an existing account is changed with grant_staff_role, not re-invited
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    insert into iam.accounts (id, account_type, status, origin) values (p_account_id, 'staff', 'invited', v_origin);
    insert into iam.staff_roles (account_id, clinic_id, role, granted_by, origin) values (p_account_id, p_clinic_id, p_role, v_actor, v_origin);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.invite', 'account', p_account_id::text, jsonb_build_object('role', p_role, 'role_scope', iam.role_scope()), v_key);
    return true;
end
$$;

create function iam.grant_staff_role(p_account_id uuid, p_clinic_id uuid, p_role text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_origin text;
    v_key text := 'audit:staff-grant:' || coalesce(p_account_id::text, '') || ':' || coalesce(p_clinic_id::text, '') || ':' || coalesce(p_role, '') || ':' || coalesce(p_idempotency_key, '');
    v_added integer;
begin
    if v_actor is null or p_account_id is null or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
       or not iam.has_clinic_role(p_clinic_id, array['admin'])
       or not exists (select 1 from iam.accounts a where a.id = p_account_id and a.account_type = 'staff' and a.status in ('invited', 'active', 'suspended')) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    if exists (select 1 from audit.log where idempotency_key = v_key) then
        return false;
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    insert into iam.staff_roles (account_id, clinic_id, role, granted_by, origin) values (p_account_id, p_clinic_id, p_role, v_actor, v_origin)
    on conflict (account_id, clinic_id, role) where revoked_at is null do nothing;
    get diagnostics v_added = row_count;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.grant_role', 'account', p_account_id::text, jsonb_build_object('role', p_role, 'role_scope', iam.role_scope()), v_key);
    return v_added = 1;
end
$$;

create function iam.revoke_staff_role(p_account_id uuid, p_clinic_id uuid, p_role text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_origin text;
    v_key text := 'audit:staff-revoke:' || coalesce(p_account_id::text, '') || ':' || coalesce(p_clinic_id::text, '') || ':' || coalesce(p_role, '') || ':' || coalesce(p_idempotency_key, '');
    v_closed integer;
begin
    if v_actor is null or p_account_id is null or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
       or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    if exists (select 1 from audit.log where idempotency_key = v_key) then
        return false;
    end if;
    if p_role = 'admin' and not exists (
        select 1 from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id
         where sr.clinic_id = p_clinic_id and sr.role = 'admin' and sr.revoked_at is null and a.status = 'active' and a.account_type = 'staff' and sr.account_id <> p_account_id) then
        raise exception 'a clinic must keep an active administrator' using errcode = 'check_violation';
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    update iam.staff_roles set revoked_at = clock_timestamp()
     where account_id = p_account_id and clinic_id = p_clinic_id and role = p_role and revoked_at is null;
    get diagnostics v_closed = row_count;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.revoke_role', 'account', p_account_id::text, jsonb_build_object('role', p_role, 'role_scope', iam.role_scope()), v_key);
    return v_closed = 1;
end
$$;

create function iam.set_staff_account_status(p_account_id uuid, p_status text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_from text;
    v_clinic uuid;
    v_origin text;
    v_key text := 'audit:staff-status:' || coalesce(p_account_id::text, '') || ':' || coalesce(p_status, '') || ':' || coalesce(p_idempotency_key, '');
begin
    if v_actor is null or p_account_id is null or p_account_id = v_actor or p_status is null or p_status <> all (array['suspended', 'active'])
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    select status into v_from from iam.accounts where id = p_account_id and account_type = 'staff';
    -- the target must exist, be changing between active and suspended only, and the caller must be an admin of EVERY clinic where the target holds a live grant
    if v_from is null
       or not ((v_from = 'active' and p_status = 'suspended') or (v_from = 'suspended' and p_status = 'active') or (v_from = p_status and v_from in ('active', 'suspended')))
       or not exists (select 1 from iam.staff_roles where account_id = p_account_id and revoked_at is null)
       or exists (select 1 from iam.staff_roles sr where sr.account_id = p_account_id and sr.revoked_at is null and not iam.has_clinic_role(sr.clinic_id, array['admin'])) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    if v_from = p_status then
        return false;  -- already in that state: a replay writes nothing more
    end if;
    update iam.accounts set status = p_status where id = p_account_id;
    for v_clinic in select distinct clinic_id from iam.staff_roles where account_id = p_account_id and revoked_at is null loop
        select origin into v_origin from iam.clinics where id = v_clinic;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_origin, v_clinic, v_actor, 'admin', case when p_status = 'suspended' then 'staff.suspend' else 'staff.reinstate' end, 'account', p_account_id::text,
                jsonb_build_object('role_scope', iam.role_scope()), v_key || ':' || v_clinic::text);
    end loop;
    return true;
end
$$;

create function iam.activate_own_account() returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_claims jsonb := coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb);
    v_status text;
    v_clinic uuid;
    v_origin text;
begin
    if v_actor is null or v_claims ->> 'aal' is distinct from 'aal2' then
        raise exception 'staff change refused' using errcode = '42501';  -- no second factor, no activation
    end if;
    select status into v_status from iam.accounts where id = v_actor and account_type = 'staff';
    if v_status = 'active' then
        return false;  -- already active: a replay
    end if;
    if v_status is distinct from 'invited' or not exists (select 1 from iam.staff_roles where account_id = v_actor and revoked_at is null) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    update iam.accounts set status = 'active' where id = v_actor;
    for v_clinic in select distinct clinic_id from iam.staff_roles where account_id = v_actor and revoked_at is null loop
        select origin into v_origin from iam.clinics where id = v_clinic;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_origin, v_clinic, v_actor, (select min(role) from iam.staff_roles where account_id = v_actor and clinic_id = v_clinic and revoked_at is null), 'staff.activate', 'account', v_actor::text,
                jsonb_build_object('role_scope', 'all_grants'), 'audit:staff-activate:' || v_actor::text || ':' || v_clinic::text);
    end loop;
    return true;
end
$$;

revoke all on function iam.invite_staff_account(uuid, uuid, text, text) from public;
revoke all on function iam.grant_staff_role(uuid, uuid, text, text) from public;
revoke all on function iam.revoke_staff_role(uuid, uuid, text, text) from public;
revoke all on function iam.set_staff_account_status(uuid, text, text) from public;
revoke all on function iam.activate_own_account() from public;
grant execute on function iam.invite_staff_account(uuid, uuid, text, text) to careflow_app;
grant execute on function iam.grant_staff_role(uuid, uuid, text, text) to careflow_app;
grant execute on function iam.revoke_staff_role(uuid, uuid, text, text) to careflow_app;
grant execute on function iam.set_staff_account_status(uuid, text, text) to careflow_app;
grant execute on function iam.activate_own_account() to careflow_app;

-- Verification: the application role can execute exactly these five functions and still cannot write to iam directly.
do $$
declare
    f text;
begin
    foreach f in array array['iam.invite_staff_account(uuid, uuid, text, text)', 'iam.grant_staff_role(uuid, uuid, text, text)', 'iam.revoke_staff_role(uuid, uuid, text, text)',
                              'iam.set_staff_account_status(uuid, text, text)', 'iam.activate_own_account()'] loop
        if not has_function_privilege('careflow_app', f, 'execute') or has_function_privilege('public', f, 'execute') then
            raise exception '0034: % must be executable by the application role only', f;
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'iam.accounts', 'insert') or has_table_privilege('careflow_app', 'iam.accounts', 'update')
       or has_table_privilege('careflow_app', 'iam.staff_roles', 'insert') or has_table_privilege('careflow_app', 'iam.staff_roles', 'update') then
        raise exception '0034: the application role must still not write to iam directly';
    end if;
end $$;
