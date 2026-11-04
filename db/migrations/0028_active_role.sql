-- 0028: the ACTIVE ROLE (docs/design/active-role.md; owner decision 2026-10-31). A request may carry `careflow_active = {"clinic_id", "role"}` inside the SAME claims JSON that already
-- carries the caller's identity (`request.jwt.claims`, set per transaction by the application). When the KEY is present, only that one grant counts for the two RLS helpers; absent, nothing changes.
--
-- It can only REMOVE access: a grant counts when it is live AND (no active claim OR it equals the claim). It never adds a grant, never changes `auth.uid()`, and never reads anything the caller does not hold.
-- FAIL CLOSED: the presence of the key turns narrowing on even when its value is null, a string, an array or an object of the wrong shape; a malformed value matches no grant at all. The application drops any
-- `careflow_active` it finds in a token and writes its own after checking the grant (careflow/active_role.py).
--
-- WHAT THIS MIGRATION DOES
--   * iam.narrowing_on()                 true when the claims JSON has the key `careflow_active`, whatever its value (invoker; reads one setting).
--   * iam.grant_is_active(clinic, role)  true when narrowing is off, or the claim names exactly this clinic and role (invoker).
--   * iam.has_clinic_role / iam.has_any_role   REPLACED (same signatures, same SECURITY DEFINER, same pinned search_path, privileges kept): one added condition, iam.grant_is_active(sr.clinic_id, sr.role).
--   * iam.effective_roles(clinic)        the caller's live roles at the clinic that count under the active claim, for the application to name the role it acts as (definer, auth.uid()).
--   * iam.active_grant_ok(text)          true when the caller holds, LIVE, the grant the claim names. The text argument is ignored: the application passes `set_config(...)` as the argument so that the claims are set
--                                         and checked in ONE statement (one round trip). A plpgsql function is never inlined, so the argument is always evaluated first.
--   * iam.role_scope()                   'active' when narrowing is on, else 'all_grants' (for audit details). SECURITY DEFINER with a pinned search_path, because it calls narrowing_on(), which the app role cannot execute.
--   GRANTS (the only new ones): EXECUTE on iam.effective_roles(uuid), iam.active_grant_ok(text) and iam.role_scope() to careflow_app, revoked from public first. Nothing else is granted.
--   NOT CHANGED: iam.admin_of_account (it calls has_clinic_role, so an admin acting under a non-admin active role loses admin reads for that request: intended, design section 9), the 0016 approval trigger
--   (it checks OTHER accounts, not the caller), the 0024 exclusivity trigger. Background workers and the queue send no claims, so no key, so no narrowing.

create function iam.narrowing_on() returns boolean
    language sql stable set search_path = ''
as $$
    select coalesce(nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::pg_catalog.jsonb ? 'careflow_active', false)
$$;

create function iam.grant_is_active(p_clinic_id uuid, p_role text) returns boolean
    language sql stable set search_path = ''
as $$
    select case
        when coalesce(not (c ? 'careflow_active'), true) then true   -- no claims, or no key: no narrowing (the same test as iam.narrowing_on, parsed once)
        else coalesce(
            (pg_catalog.jsonb_typeof(c -> 'careflow_active') = 'object')
            and (c -> 'careflow_active' ->> 'clinic_id') = p_clinic_id::pg_catalog.text
            and (c -> 'careflow_active' ->> 'role') = p_role,
            false)
    end
    from (select nullif(pg_catalog.current_setting('request.jwt.claims', true), '')::pg_catalog.jsonb as c) s
$$;

create or replace function iam.has_clinic_role(p_clinic_id uuid, p_roles text[]) returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1
        from iam.staff_roles sr
        join iam.accounts a on a.id = sr.account_id
        where sr.account_id = auth.uid()
          and a.status = 'active'
          and a.account_type = 'staff'
          and sr.revoked_at is null
          and sr.clinic_id = p_clinic_id
          and sr.role = any (p_roles)
          and iam.grant_is_active(sr.clinic_id, sr.role)
    )
$$;

create or replace function iam.has_any_role(p_roles text[]) returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1
        from iam.staff_roles sr
        join iam.accounts a on a.id = sr.account_id
        where sr.account_id = auth.uid()
          and a.status = 'active'
          and a.account_type = 'staff'
          and sr.revoked_at is null
          and sr.role = any (p_roles)
          and iam.grant_is_active(sr.clinic_id, sr.role)
    )
$$;

create function iam.effective_roles(p_clinic_id uuid) returns text[]
    language sql stable security definer set search_path = ''
as $$
    select coalesce(pg_catalog.array_agg(sr.role order by sr.role), '{}'::pg_catalog.text[])
    from iam.staff_roles sr
    join iam.accounts a on a.id = sr.account_id
    where sr.account_id = auth.uid()
      and a.status = 'active'
      and a.account_type = 'staff'
      and sr.revoked_at is null
      and sr.clinic_id = p_clinic_id
      and iam.grant_is_active(sr.clinic_id, sr.role)
$$;

create function iam.active_grant_ok(p_claims_were_set text) returns boolean
    language plpgsql volatile security definer set search_path = ''
as $$
begin
    -- p_claims_were_set is deliberately unused: it exists so that the caller's set_config(...) is evaluated BEFORE this body runs.
    return iam.narrowing_on() and exists (
        select 1
        from iam.staff_roles sr
        join iam.accounts a on a.id = sr.account_id
        where sr.account_id = auth.uid()
          and a.status = 'active'
          and a.account_type = 'staff'
          and sr.revoked_at is null
          and iam.grant_is_active(sr.clinic_id, sr.role)
    );
end
$$;

create function iam.role_scope() returns text
    language sql stable security definer set search_path = ''   -- DEFINER: it calls iam.narrowing_on(), which careflow_app may not execute (reviewer finding F1)
as $$
    select case when iam.narrowing_on() then 'active' else 'all_grants' end
$$;

revoke all on function iam.narrowing_on() from public;
revoke all on function iam.grant_is_active(uuid, text) from public;
revoke all on function iam.effective_roles(uuid) from public;
revoke all on function iam.active_grant_ok(text) from public;
revoke all on function iam.role_scope() from public;
grant execute on function iam.effective_roles(uuid) to careflow_app;
grant execute on function iam.active_grant_ok(text) to careflow_app;
grant execute on function iam.role_scope() to careflow_app;

-- Verify the semantics inside the migration's own transaction (transaction-local settings, reset at the end): absent key = no narrowing; key present with ANY value = narrowing on and
-- a malformed value matches nothing; a well-formed value matches only itself.
do $$
declare
    c1 constant uuid := '00000000-0000-0000-0000-0000000000a1';
begin
    perform set_config('request.jwt.claims', '{"sub": "x"}', true);
    if iam.narrowing_on() or not iam.grant_is_active(c1, 'nurse') or iam.role_scope() <> 'all_grants' then
        raise exception '0028: with no careflow_active key nothing may be narrowed';
    end if;
    perform set_config('request.jwt.claims', '{"sub": "x", "careflow_active": null}', true);
    if not iam.narrowing_on() or iam.grant_is_active(c1, 'nurse') or iam.role_scope() <> 'active' then
        raise exception '0028: a null careflow_active must turn narrowing on and match nothing';
    end if;
    perform set_config('request.jwt.claims', '{"sub": "x", "careflow_active": "nurse"}', true);
    if iam.grant_is_active(c1, 'nurse') then
        raise exception '0028: a string careflow_active must match nothing';
    end if;
    perform set_config('request.jwt.claims', '{"sub": "x", "careflow_active": ["nurse"]}', true);
    if iam.grant_is_active(c1, 'nurse') then
        raise exception '0028: an array careflow_active must match nothing';
    end if;
    perform set_config('request.jwt.claims', '{"sub": "x", "careflow_active": {"clinic_id": 5, "role": "nurse"}}', true);
    if iam.grant_is_active(c1, 'nurse') then
        raise exception '0028: a wrong-typed clinic_id must match nothing';
    end if;
    perform set_config('request.jwt.claims', '{"sub": "x", "careflow_active": {"clinic_id": "00000000-0000-0000-0000-0000000000a1", "role": "nurse"}}', true);
    if not iam.grant_is_active(c1, 'nurse') or iam.grant_is_active(c1, 'doctor') or iam.grant_is_active('00000000-0000-0000-0000-0000000000a2', 'nurse') then
        raise exception '0028: a well-formed careflow_active must match exactly its own clinic and role';
    end if;
    perform set_config('request.jwt.claims', '', true);
end
$$;
