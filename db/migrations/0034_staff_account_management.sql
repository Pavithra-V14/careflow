-- 0034_staff_account_management: invite, grant, revoke, suspend and activate staff accounts (pre-approved by the owner 2026-11-05; docs/design/staff-account-management.md).
-- Reviewer pass 2026-11-05 applied: replay is decided by STATE (never by reading audit.log, whose keys a caller could plant), audit keys are server-made with the reserved `sys:` prefix (0035), the
-- last-administrator check is serialised per clinic, nobody grants a role to themselves, and the functions are revoked from anon, authenticated and service_role.
--
-- Until now accounts and role grants were created by a script run by the owner (scripts/seed_dev_staff.py); the application role could not write to iam at all. Rather than hand the application role INSERT
-- and UPDATE on iam.accounts and iam.staff_roles (which would let any bug or any admin session do anything to any account), every change goes through ONE of five SECURITY DEFINER functions (pinned
-- search_path), each of which does exactly one thing, checks who the caller is INSIDE, and writes its own audit row in the same transaction. Nothing else about iam changes: the tables stay SELECT-only
-- for the application role.
--
-- WHO MAY DO WHAT (checked in the function, through iam.has_clinic_role, so the active-role header narrows it):
--   * invite_staff_account, grant_staff_role, revoke_staff_role: an ACTIVE ADMIN of the clinic concerned. The roles that can be granted are front_desk, nurse, doctor, admin, auditor and business_owner;
--     NEVER queue_worker (worker accounts stay operator-created: a worker identity must not be creatable from a web session). Nobody grants a role to THEMSELVES.
--   * set_staff_account_status (suspend and reinstate): an admin of EVERY clinic where the target holds a live grant (so one clinic's admin cannot suspend someone who also works for another clinic), never
--     on oneself, and only active <-> suspended. An admin can NOT activate an invited account: activation is the person's own act.
--   * activate_own_account: the person themselves, once their token shows a verified second factor (aal2): invited -> active. So nobody becomes active without having enrolled a second factor.
--   * A clinic can never be left without an active administrator by a revoke or a suspension (check_violation), also under concurrency: the clinic's admin set is locked (advisory lock) while checking.
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
begin
    if v_actor is null or p_account_id is null or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
       or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    -- a replay: the account this caller already invited, still invited, with that role granted by this caller
    if exists (select 1 from iam.accounts a join iam.staff_roles sr on sr.account_id = a.id
                where a.id = p_account_id and a.status = 'invited' and sr.clinic_id = p_clinic_id and sr.role = p_role and sr.granted_by = v_actor and sr.revoked_at is null) then
        return false;
    end if;
    if exists (select 1 from iam.accounts where id = p_account_id) then
        raise exception 'staff change refused' using errcode = '42501';  -- an existing account is changed with grant_staff_role, not re-invited
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    insert into iam.accounts (id, account_type, status, origin) values (p_account_id, 'staff', 'invited', v_origin);
    insert into iam.staff_roles (account_id, clinic_id, role, granted_by, origin) values (p_account_id, p_clinic_id, p_role, v_actor, v_origin);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.invite', 'account', p_account_id::text, jsonb_build_object('role', p_role, 'role_scope', iam.role_scope()), 'sys:staff-invite:' || gen_random_uuid()::text);
    return true;
end
$$;

create function iam.grant_staff_role(p_account_id uuid, p_clinic_id uuid, p_role text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_origin text;
    v_added integer;
begin
    if v_actor is null or p_account_id is null or p_account_id = v_actor or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
       or not iam.has_clinic_role(p_clinic_id, array['admin'])
       or not exists (select 1 from iam.accounts a where a.id = p_account_id and a.account_type = 'staff' and a.status in ('invited', 'active', 'suspended')) then
        raise exception 'staff change refused' using errcode = '42501';
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

create function iam.revoke_staff_role(p_account_id uuid, p_clinic_id uuid, p_role text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_origin text;
    v_closed integer;
begin
    if v_actor is null or p_account_id is null or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_role is null or p_role <> all (array['front_desk', 'nurse', 'doctor', 'admin', 'auditor', 'business_owner'])
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
    if v_closed = 0 then
        return false;  -- nothing live to revoke: a replay or a no-op writes nothing more
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'staff.revoke_role', 'account', p_account_id::text, jsonb_build_object('role', p_role, 'role_scope', iam.role_scope()), 'sys:staff-revoke:' || gen_random_uuid()::text);
    return true;
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
begin
    if v_actor is null or p_account_id is null or p_account_id = v_actor or p_status is null or p_status <> all (array['suspended', 'active'])
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    -- serialise with every other change to the admin sets of the clinics concerned (sorted, so two callers cannot deadlock)
    for v_clinic in select distinct clinic_id from iam.staff_roles where account_id = p_account_id and revoked_at is null order by clinic_id loop
        perform pg_advisory_xact_lock(hashtextextended('staff-admins:' || v_clinic::text, 0));
    end loop;
    select status into v_from from iam.accounts where id = p_account_id and account_type = 'staff';
    -- the target must exist, be moving between active and suspended only, and the caller must be an admin of EVERY clinic where the target holds a live grant
    if v_from is null
       or v_from not in ('active', 'suspended')
       or not exists (select 1 from iam.staff_roles where account_id = p_account_id and revoked_at is null)
       or exists (select 1 from iam.staff_roles sr where sr.account_id = p_account_id and sr.revoked_at is null and not iam.has_clinic_role(sr.clinic_id, array['admin'])) then
        raise exception 'staff change refused' using errcode = '42501';
    end if;
    if v_from = p_status then
        return false;  -- already in that state: a replay writes nothing more
    end if;
    if p_status = 'suspended' then
        -- a suspension must not leave a clinic without an active administrator
        for v_clinic in select distinct clinic_id from iam.staff_roles where account_id = p_account_id and role = 'admin' and revoked_at is null loop
            if not exists (select 1 from iam.staff_roles sr join iam.accounts a on a.id = sr.account_id
                            where sr.clinic_id = v_clinic and sr.role = 'admin' and sr.revoked_at is null and a.status = 'active' and a.account_type = 'staff' and sr.account_id <> p_account_id) then
                raise exception 'a clinic must keep an active administrator' using errcode = 'check_violation';
            end if;
        end loop;
    end if;
    update iam.accounts set status = p_status where id = p_account_id;
    for v_clinic in select distinct clinic_id from iam.staff_roles where account_id = p_account_id and revoked_at is null loop
        select origin into v_origin from iam.clinics where id = v_clinic;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_origin, v_clinic, v_actor, 'admin', case when p_status = 'suspended' then 'staff.suspend' else 'staff.reinstate' end, 'account', p_account_id::text,
                jsonb_build_object('role_scope', iam.role_scope()), 'sys:staff-status:' || gen_random_uuid()::text);
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
                jsonb_build_object('role_scope', 'all_grants'), 'sys:staff-activate:' || gen_random_uuid()::text);
    end loop;
    return true;
end
$$;

do $$
declare
    f text;
    r text;
begin
    foreach f in array array['iam.invite_staff_account(uuid, uuid, text, text)', 'iam.grant_staff_role(uuid, uuid, text, text)', 'iam.revoke_staff_role(uuid, uuid, text, text)',
                              'iam.set_staff_account_status(uuid, text, text)', 'iam.activate_own_account()'] loop
        execute format('revoke all on function %s from public', f);
        execute format('grant execute on function %s to careflow_app', f);
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
    end loop;
end $$;

-- Verification: the application role can execute exactly these five functions, nobody else can, and the application role still cannot write to iam directly.
do $$
declare
    f text;
    r text;
begin
    foreach f in array array['iam.invite_staff_account(uuid, uuid, text, text)', 'iam.grant_staff_role(uuid, uuid, text, text)', 'iam.revoke_staff_role(uuid, uuid, text, text)',
                              'iam.set_staff_account_status(uuid, text, text)', 'iam.activate_own_account()'] loop
        if not has_function_privilege('careflow_app', f, 'execute') or has_function_privilege('public', f, 'execute') then
            raise exception '0034: % must be executable by the application role only', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0034: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
    if has_table_privilege('careflow_app', 'iam.accounts', 'insert') or has_table_privilege('careflow_app', 'iam.accounts', 'update')
       or has_table_privilege('careflow_app', 'iam.staff_roles', 'insert') or has_table_privilege('careflow_app', 'iam.staff_roles', 'update') then
        raise exception '0034: the application role must still not write to iam directly';
    end if;
end $$;
