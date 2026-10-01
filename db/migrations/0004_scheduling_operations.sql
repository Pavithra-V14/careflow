-- 0004_scheduling_operations: holds, bookings, waitlist and clinician-authored priority levels
-- (BUILD-SPEC.md §17.1, §17.5, §12.1; CLAUDE.md rules 4, 5, 7, 8).
--
-- * sched.priority_levels is where a clinic's waitlist priority tiers are DEFINED. This migration creates
--   the table EMPTY on purpose: which tiers exist, what they are called and how they rank is clinical
--   content, so it is authored and approved by a clinician and entered as data, never written here
--   (CLAUDE.md rule 1).
--   # CLINICAL-PLACEHOLDER: needs clinician sign-off  (the tier set and its ranks)
-- * Holds expire by clock, not by a job: a hold whose expires_at has passed is treated as released by
--   every reader (soft filter) and is tidied up by an explicit sweep; correctness never depends on the
--   sweep having run. See careflow/scheduling.py.
-- * Every table: origin tag with no default, an idempotency key, forced RLS, no DELETE.
-- * RLS roles: front desk writes holds, bookings and the waitlist; nurse and doctor read them; a doctor
--   authors priority levels. Patients get no direct access yet (the portal adds its own policies with
--   its routes). Admin, auditor and business owner see no bookings or waitlist rows.

-- Child tables must prove they share the slot's clinic.
alter table sched.slots add constraint slots_id_clinic_key unique (id, clinic_id);

-- ---- priority levels (clinician-authored, empty at creation) ------------------------------------

create table sched.priority_levels (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    code            text not null check (code ~ '^[a-z][a-z0-9_]*$'),
    label           text not null check (length(btrim(label)) > 0),
    -- Strict ranking: 1 is served first. No two levels in a clinic share a rank, so there are no ties.
    rank            integer not null check (rank > 0),
    -- A level cannot exist without the named clinician who approved it (rule 1).
    approved_by     uuid not null references iam.accounts (id),
    approved_at     timestamptz not null default now(),
    active          boolean not null default true,
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default now(),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    unique (clinic_id, code),
    unique (clinic_id, rank)
);

-- ---- holds --------------------------------------------------------------------------------------

create table sched.holds (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null,
    slot_id         uuid not null,
    -- Optional: the patient the slot is being held for. A hold for one patient cannot be booked for another.
    patient_id      uuid,
    status          text not null default 'active' check (status in ('active', 'released', 'expired', 'converted')),
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null,
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    -- Expiry is after creation. (A CHECK cannot compare to now(): it is not immutable and would be
    -- re-evaluated on every update and on restore. The insert trigger below enforces "in the future".)
    check (expires_at > created_at),
    foreign key (slot_id, clinic_id) references sched.slots (id, clinic_id),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id)
);

-- At most one ACTIVE hold per slot: a duplicate hold is refused by the engine, not just by code.
create unique index holds_one_active_per_slot on sched.holds (slot_id) where status = 'active';
create index holds_expiry_idx on sched.holds (expires_at) where status = 'active';

create function sched.require_future_expiry() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    -- clock_timestamp(), not now(): now() is the transaction's start, which a long transaction would let drift.
    if new.expires_at <= clock_timestamp() then
        raise exception 'hold expires_at must be in the future' using errcode = 'check_violation';
    end if;
    return new;
end
$$;

create trigger holds_expiry_in_future before insert on sched.holds
    for each row execute function sched.require_future_expiry();

-- ---- bookings -----------------------------------------------------------------------------------

create table sched.bookings (
    id                      uuid primary key default gen_random_uuid(),
    clinic_id               uuid not null,
    slot_id                 uuid not null,
    patient_id              uuid not null,
    hold_id                 uuid references sched.holds (id),
    status                  text not null default 'confirmed' check (status in ('confirmed', 'cancelled')),
    created_at              timestamptz not null default now(),
    cancelled_at            timestamptz,
    origin                  text not null check (origin in ('live', 'test', 'eval', 'canary')),
    -- Rule 5: every booking carries a client-supplied key, unique across the table.
    idempotency_key         text not null unique check (length(idempotency_key) > 0),
    cancel_idempotency_key  text unique check (cancel_idempotency_key is null or length(cancel_idempotency_key) > 0),
    check ((status = 'cancelled') = (cancelled_at is not null)),
    check ((status = 'cancelled') = (cancel_idempotency_key is not null)),
    foreign key (slot_id, clinic_id) references sched.slots (id, clinic_id),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id)
);

-- One live booking per slot: a second confirmed booking is refused by the engine.
create unique index bookings_one_confirmed_per_slot on sched.bookings (slot_id) where status = 'confirmed';
create index bookings_patient_idx on sched.bookings (patient_id);

-- ---- waitlist -----------------------------------------------------------------------------------

create table sched.waitlist (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null,
    patient_id      uuid not null,
    visit_type_id   uuid not null,
    priority_code   text not null,
    joined_at       timestamptz not null default now(),
    status          text not null default 'waiting' check (status in ('waiting', 'fulfilled', 'removed')),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default now(),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    foreign key (visit_type_id, clinic_id) references sched.visit_types (id, clinic_id),
    -- A priority must be one the clinic's clinicians have defined; it cannot be free text.
    foreign key (clinic_id, priority_code) references sched.priority_levels (clinic_id, code)
);

create unique index waitlist_one_waiting_entry on sched.waitlist (clinic_id, patient_id, visit_type_id) where status = 'waiting';
create index waitlist_queue_idx on sched.waitlist (clinic_id, visit_type_id, joined_at) where status = 'waiting';

-- ---- Row-level security -------------------------------------------------------------------------

alter table sched.priority_levels enable row level security;
alter table sched.priority_levels force row level security;
alter table sched.holds enable row level security;
alter table sched.holds force row level security;
alter table sched.bookings enable row level security;
alter table sched.bookings force row level security;
alter table sched.waitlist enable row level security;
alter table sched.waitlist force row level security;

create policy priority_levels_select on sched.priority_levels for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']));
create policy priority_levels_insert_doctor on sched.priority_levels for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['doctor']));
create policy priority_levels_update_doctor on sched.priority_levels for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['doctor']))
    with check (iam.has_clinic_role(clinic_id, array['doctor']));

create policy holds_select on sched.holds for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']));
create policy holds_insert_front_desk on sched.holds for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy holds_update_front_desk on sched.holds for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

create policy bookings_select on sched.bookings for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor']));
create policy bookings_insert_front_desk on sched.bookings for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy bookings_update_front_desk on sched.bookings for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

create policy waitlist_select on sched.waitlist for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor']));
create policy waitlist_insert_front_desk on sched.waitlist for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy waitlist_update_front_desk on sched.waitlist for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

-- ---- Privileges ---------------------------------------------------------------------------------

revoke all on sched.priority_levels, sched.holds, sched.bookings, sched.waitlist from public;
grant select, insert, update on sched.priority_levels, sched.holds, sched.bookings, sched.waitlist to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on sched.priority_levels, sched.holds, sched.bookings, sched.waitlist from %I', r);
        end if;
    end loop;
end $$;
