-- 0001_initial_schema: Phase 0 foundations (BUILD-SPEC.md §17.1, §18.1, §18.4, Appendix D).
--
-- * Creates all 11 schema namespaces. Only `iam` and `audit` get tables here; the other nine are
--   deliberately empty and gain tables in the phase that uses them.
-- * iam: the identity tables RLS needs (clinics, accounts, staff_roles). consent_records,
--   activation_requests and grievances are listed in §17.1 but are NOT created here; their shape is
--   not specified yet and is not guessed.
-- * audit: one append-only log, immutable by trigger and by privilege.
-- * RLS: every policy keys off auth.uid() (§18.1). The app role has SELECT only on iam (writes are
--   added with the route that needs them) and INSERT + policy-limited SELECT on audit.
-- * Deny by default: no policy => no rows. An unknown or missing identity matches nothing.
--
-- The role careflow_app is created by scripts/create_app_role.py, not here (its password must never
-- be committed). This migration only grants to it.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'careflow_app') then
        raise exception 'role careflow_app must exist before migration 0001 (run scripts/create_app_role.py)';
    end if;
end $$;

-- ---- Schema namespaces (§17.1) ----------------------------------------------------------------

create schema if not exists iam;
create schema if not exists mpi;
create schema if not exists sched;
create schema if not exists clinical;
create schema if not exists docs;
create schema if not exists msg;
create schema if not exists kb;
create schema if not exists agents;
create schema if not exists ai;
create schema if not exists ops;
create schema if not exists audit;

-- ---- iam --------------------------------------------------------------------------------------

create table iam.clinics (
    id         uuid primary key default gen_random_uuid(),
    name       text not null check (length(btrim(name)) > 0),
    -- Origin tag (§0): no default on purpose, so test data can never be silently tagged `live`.
    origin     text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at timestamptz not null default now()
);

-- One row per person who can sign in. `id` is the Supabase Auth user id (auth.users.id); there is no
-- foreign key to it because schema auth is owned by Supabase, not by this project. Contact details
-- stay in Supabase Auth and are not duplicated here.
create table iam.accounts (
    id           uuid primary key,
    account_type text not null check (account_type in ('patient', 'guardian', 'staff')),
    -- New accounts start `invited`, never `active`: activation is an explicit act.
    status       text not null default 'invited' check (status in ('invited', 'active', 'suspended', 'closed')),
    origin       text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at   timestamptz not null default now()
);

-- Staff role grants, per clinic (§18.4). Revocation closes the row; rows are never deleted.
create table iam.staff_roles (
    id         uuid primary key default gen_random_uuid(),
    account_id uuid not null references iam.accounts (id),
    clinic_id  uuid not null references iam.clinics (id),
    role       text not null check (role in ('front_desk', 'nurse', 'doctor', 'admin', 'business_owner', 'auditor')),
    granted_at timestamptz not null default now(),
    granted_by uuid references iam.accounts (id),
    revoked_at timestamptz,
    origin     text not null check (origin in ('live', 'test', 'eval', 'canary')),
    check (revoked_at is null or revoked_at >= granted_at)
);

create unique index staff_roles_one_active_grant
    on iam.staff_roles (account_id, clinic_id, role) where revoked_at is null;
create index staff_roles_clinic_idx on iam.staff_roles (clinic_id);

-- RLS helpers. SECURITY DEFINER so the policies on staff_roles can ask "is the caller staff here?"
-- without recursing into staff_roles' own policies; search_path is pinned so nothing can be shadowed.
-- Both return false (never true) when auth.uid() is null, the account is not an active staff account,
-- or the grant is revoked.
create function iam.has_clinic_role(p_clinic_id uuid, p_roles text[]) returns boolean
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
    )
$$;

-- True when the caller is an active clinic admin of any clinic where p_account_id holds a live grant.
create function iam.admin_of_account(p_account_id uuid) returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1
        from iam.staff_roles target
        where target.account_id = p_account_id
          and target.revoked_at is null
          and iam.has_clinic_role(target.clinic_id, array['admin'])
    )
$$;

revoke all on function iam.has_clinic_role(uuid, text[]) from public;
revoke all on function iam.admin_of_account(uuid) from public;

alter table iam.clinics enable row level security;
alter table iam.clinics force row level security;
alter table iam.accounts enable row level security;
alter table iam.accounts force row level security;
alter table iam.staff_roles enable row level security;
alter table iam.staff_roles force row level security;

-- A clinic is visible to its own active staff (any role). Patients/guardians see no clinic rows here.
create policy clinics_select_own_staff on iam.clinics for select to careflow_app
    using (iam.has_clinic_role(id, array['front_desk', 'nurse', 'doctor', 'admin', 'business_owner', 'auditor']));

-- An account sees itself; a clinic admin also sees the staff accounts of their clinic.
create policy accounts_select_self on iam.accounts for select to careflow_app
    using (id = auth.uid());
create policy accounts_select_clinic_admin on iam.accounts for select to careflow_app
    using (iam.admin_of_account(id));

-- Staff see their own grants; a clinic admin sees every grant at their clinic.
create policy staff_roles_select_self on iam.staff_roles for select to careflow_app
    using (account_id = auth.uid());
create policy staff_roles_select_clinic_admin on iam.staff_roles for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['admin']));

grant usage on schema iam to careflow_app;
grant select on iam.clinics, iam.accounts, iam.staff_roles to careflow_app;
grant execute on function iam.has_clinic_role(uuid, text[]) to careflow_app;
grant execute on function iam.admin_of_account(uuid) to careflow_app;

-- ---- audit ------------------------------------------------------------------------------------

-- Append-only (§17.1, §21). `actor_id` is the account uuid, not a name or contact detail, so removing
-- a person's identifiable fields elsewhere leaves the audit row intact with only an opaque id (§9.4).
-- Never put patient text in `details`.
create table audit.log (
    id              bigint generated always as identity primary key,
    occurred_at     timestamptz not null default now(),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    clinic_id       uuid,
    actor_id        uuid,          -- null = system action with no signed-in caller
    actor_role      text,
    action          text not null check (length(btrim(action)) > 0),
    resource_type   text,
    resource_id     text,
    reason          text,          -- required by policy for break-glass access (§18.4), enforced by the writer
    details         jsonb not null default '{}'::jsonb check (jsonb_typeof(details) = 'object'),
    -- Every write carries an idempotency key (CLAUDE.md rule 5): a retried write cannot double-log.
    idempotency_key text not null unique check (length(idempotency_key) > 0)
);

create index audit_log_clinic_time_idx on audit.log (clinic_id, occurred_at desc);

create function audit.reject_mutation() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'audit.log is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;

-- Fires for every role including the table owner.
create trigger log_no_update_delete before update or delete on audit.log
    for each row execute function audit.reject_mutation();
create trigger log_no_truncate before truncate on audit.log
    for each statement execute function audit.reject_mutation();

revoke all on audit.log from public;
revoke update, delete, truncate on audit.log from careflow_app;

alter table audit.log enable row level security;
alter table audit.log force row level security;

-- A signed-in caller can only write rows attributed to themselves; a call with no identity can only
-- write system rows (actor_id null). Nobody can write a row as someone else.
create policy log_insert_as_self on audit.log for insert to careflow_app
    with check (actor_id is not distinct from auth.uid());
-- Read access is the auditor role only, per clinic. System rows (clinic_id null) are not readable
-- through the app role.
create policy log_select_auditor on audit.log for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['auditor']));

grant usage on schema audit to careflow_app;
grant insert, select on audit.log to careflow_app;

-- ---- Supabase's own API roles get nothing here ------------------------------------------------

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on all tables in schema iam from %I', r);
            execute format('revoke all on all tables in schema audit from %I', r);
            execute format('revoke all on all functions in schema iam from %I', r);
            execute format('revoke all on all functions in schema audit from %I', r);
            execute format('revoke all on schema iam from %I', r);
            execute format('revoke all on schema audit from %I', r);
        end if;
    end loop;
end $$;
