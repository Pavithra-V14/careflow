-- 0002_mpi_and_scheduling: Phase 1 data layout for `mpi` and `sched` (BUILD-SPEC.md §17.1, §17.2,
-- Appendix D). Tables only; no key store, no matching logic, no booking/hold/waitlist tables yet.
--
-- * mpi.patients holds demographics as CIPHERTEXT ONLY (name, date of birth, phone). Encryption and
--   key handling happen in application code; the database never sees a plaintext demographic and has
--   no plaintext index on one. Every ciphertext column has its own key_version next to it (§17.2).
-- * mpi.identity_tokens holds keyed-HMAC blocking tokens, each with its key_version, so lookups never
--   need plaintext.
-- * sched.slots carries a GiST exclusion constraint: two live slots for one provider cannot overlap,
--   enforced by the engine regardless of which code path writes them.
-- * Every table has forced RLS keyed on auth.uid() through iam.has_clinic_role (migration 0001).
--   Deny by default: a role not named in a policy sees and writes nothing.
-- * The app role gets SELECT, INSERT, UPDATE only. No DELETE anywhere: rows are closed, not removed.

-- ---- mpi --------------------------------------------------------------------------------------

create table mpi.patients (
    id               uuid primary key default gen_random_uuid(),
    clinic_id        uuid not null references iam.clinics (id),
    -- Set when the patient signs up and claims the record (§17.2); null for a referral-created record.
    account_id       uuid references iam.accounts (id),

    -- Ciphertext, nonce/tag packed in by the application. Nullable ONLY for an erased record (below);
    -- each ciphertext and its key_version are present together or absent together.
    name_ct          bytea,
    name_key_version smallint,
    dob_ct           bytea,
    dob_key_version  smallint,
    phone_ct         bytea,         -- optional: a referral may arrive without a phone number
    phone_key_version smallint,

    -- Erasure removes identifiable fields but keeps the row (and the audit trail around it, §9.4).
    erased_at        timestamptz,

    -- Origin tag (§0): no default on purpose, so every row must state its context explicitly.
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at       timestamptz not null default now(),
    -- Every write carries an idempotency key (CLAUDE.md rule 5): a retried create cannot duplicate.
    idempotency_key  text not null unique check (length(idempotency_key) > 0),

    -- Needed so child tables can prove they share the patient's clinic.
    unique (id, clinic_id),

    check ((name_ct  is null) = (name_key_version  is null) and (name_key_version  is null or name_key_version  > 0)),
    check ((dob_ct   is null) = (dob_key_version   is null) and (dob_key_version   is null or dob_key_version   > 0)),
    check ((phone_ct is null) = (phone_key_version is null) and (phone_key_version is null or phone_key_version > 0)),
    -- A live record must carry a name and a date of birth (§17.2: a date of birth is required);
    -- an erased record must carry none of the three.
    check (
        (erased_at is null and name_ct is not null and dob_ct is not null)
        or (erased_at is not null and name_ct is null and dob_ct is null and phone_ct is null)
    )
);

-- At most one record per signed-in account.
create unique index patients_one_per_account on mpi.patients (account_id) where account_id is not null;
create index patients_clinic_idx on mpi.patients (clinic_id);

-- Keyed-HMAC blocking tokens (§17.1, §17.2). `kind` names which blocking key a token is (the set of
-- kinds belongs to the matching code, not this schema). During a key rotation two key_versions of
-- the same (patient, kind) coexist until the old one is retired.
create table mpi.identity_tokens (
    id          uuid primary key default gen_random_uuid(),
    patient_id  uuid not null,
    clinic_id   uuid not null,
    kind        text not null check (kind ~ '^[a-z][a-z0-9_]*$'),
    token       bytea not null check (octet_length(token) > 0),
    key_version smallint not null check (key_version > 0),
    origin      text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at  timestamptz not null default now(),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    -- The natural key doubles as the idempotency key for token writes.
    unique (patient_id, kind, key_version)
);

-- The lookup path: find candidates by token without ever touching plaintext.
create index identity_tokens_lookup_idx on mpi.identity_tokens (clinic_id, kind, key_version, token);

-- ---- sched ------------------------------------------------------------------------------------

-- btree_gist lets a GiST exclusion constraint compare uuids with `=` alongside a range overlap.
create extension if not exists btree_gist with schema extensions;

create table sched.locations (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    name            text not null check (length(btrim(name)) > 0),
    active          boolean not null default true,
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default now(),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    unique (id, clinic_id)
);

create table sched.providers (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    -- The provider's own sign-in, when they have one.
    account_id      uuid references iam.accounts (id),
    display_name    text not null check (length(btrim(display_name)) > 0),
    active          boolean not null default true,
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default now(),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    unique (id, clinic_id)
);

create table sched.visit_types (
    id               uuid primary key default gen_random_uuid(),
    clinic_id        uuid not null references iam.clinics (id),
    name             text not null check (length(btrim(name)) > 0),
    -- Operational scheduling length, entered per clinic. No default: nothing here guesses a duration.
    duration_minutes integer not null check (duration_minutes > 0),
    active           boolean not null default true,
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at       timestamptz not null default now(),
    idempotency_key  text not null unique check (length(idempotency_key) > 0),
    unique (id, clinic_id)
);

create table sched.slots (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null,
    provider_id     uuid not null,
    location_id     uuid not null,
    visit_type_id   uuid not null,
    starts_at       timestamptz not null,
    ends_at         timestamptz not null,
    -- Half-open, so back-to-back slots (10:00-10:30, 10:30-11:00) do not count as overlapping.
    during          tstzrange generated always as (tstzrange(starts_at, ends_at, '[)')) stored,
    status          text not null default 'open' check (status in ('open', 'held', 'booked', 'cancelled')),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default now(),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    check (ends_at > starts_at),
    -- Provider, location and visit type must belong to the slot's own clinic.
    foreign key (provider_id, clinic_id) references sched.providers (id, clinic_id),
    foreign key (location_id, clinic_id) references sched.locations (id, clinic_id),
    foreign key (visit_type_id, clinic_id) references sched.visit_types (id, clinic_id),
    -- No double-booking at the database level: one provider cannot have two live slots that overlap.
    -- Cancelled slots are released and may be re-created over the same time. Two concurrent inserts
    -- race inside the engine and exactly one wins; the other fails with exclusion_violation (23P01).
    constraint slots_no_provider_overlap exclude using gist (
        provider_id extensions.gist_uuid_ops with =,
        during with &&
    ) where (status <> 'cancelled')
);

create index slots_clinic_start_idx on sched.slots (clinic_id, starts_at);

-- ---- Row-level security -----------------------------------------------------------------------

alter table mpi.patients enable row level security;
alter table mpi.patients force row level security;
alter table mpi.identity_tokens enable row level security;
alter table mpi.identity_tokens force row level security;
alter table sched.locations enable row level security;
alter table sched.locations force row level security;
alter table sched.providers enable row level security;
alter table sched.providers force row level security;
alter table sched.visit_types enable row level security;
alter table sched.visit_types force row level security;
alter table sched.slots enable row level security;
alter table sched.slots force row level security;

-- Patient demographics: front desk, nurses and doctors of the patient's clinic, and the patient's own
-- account. NOT admin, business_owner (aggregates only) or auditor: nothing in §18.4 grants them
-- case-level access, so they get none. Only front desk creates or corrects a record.
create policy patients_select_own on mpi.patients for select to careflow_app
    using (account_id = auth.uid());
create policy patients_select_clinic_staff on mpi.patients for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor']));
create policy patients_insert_front_desk on mpi.patients for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy patients_update_front_desk on mpi.patients for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

-- Tokens are matching machinery, not something a patient reads: staff only, same split as above.
create policy tokens_select_clinic_staff on mpi.identity_tokens for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor']));
create policy tokens_insert_front_desk on mpi.identity_tokens for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy tokens_update_front_desk on mpi.identity_tokens for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

-- Scheduling: clinical and front-desk staff read; front desk and clinic admin manage. Patients get no
-- direct access yet: the patient portal adds its own scoped policy together with its route.
create policy locations_select_clinic_staff on sched.locations for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']));
create policy locations_insert_managers on sched.locations for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));
create policy locations_update_managers on sched.locations for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));

create policy providers_select_clinic_staff on sched.providers for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']));
create policy providers_insert_managers on sched.providers for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));
create policy providers_update_managers on sched.providers for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));

create policy visit_types_select_clinic_staff on sched.visit_types for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']));
create policy visit_types_insert_managers on sched.visit_types for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));
create policy visit_types_update_managers on sched.visit_types for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));

create policy slots_select_clinic_staff on sched.slots for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']));
create policy slots_insert_managers on sched.slots for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));
create policy slots_update_managers on sched.slots for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'admin']));

-- ---- Privileges -------------------------------------------------------------------------------

revoke all on mpi.patients, mpi.identity_tokens from public;
revoke all on sched.locations, sched.providers, sched.visit_types, sched.slots from public;

grant usage on schema mpi to careflow_app;
grant usage on schema sched to careflow_app;
grant select, insert, update on mpi.patients, mpi.identity_tokens to careflow_app;
grant select, insert, update on sched.locations, sched.providers, sched.visit_types, sched.slots to careflow_app;

-- Supabase's own API roles get nothing here.
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on all tables in schema mpi from %I', r);
            execute format('revoke all on all tables in schema sched from %I', r);
            execute format('revoke all on schema mpi from %I', r);
            execute format('revoke all on schema sched from %I', r);
        end if;
    end loop;
end $$;
