-- 0016_spend_ledger_and_registry_approval (BUILD-SPEC.md §5.1, §6.1, §15.2, §17.1 `ops` and `ai`; CLAUDE.md rules 5, 7, 8, 10, 13).
-- Two owner decisions of 2026-10-24, one migration.
--
-- PART A. ops.spend_ledger: the day's model spend, persisted, so a restart cannot reset a per-clinic daily ceiling (§6.1, §15.2).
--   * One row per model call that cost something or was recorded as free (an actual cost of 0 with its shadow cost, §5.1), written by the
--     gateway after the call. Append-only for EVERYONE, the table owner included (trigger), like audit.log: spend can only be added,
--     never edited away.
--   * The day is taken from the DATABASE clock in India time (Asia/Kolkata, UTC+05:30, no daylight saving), not from the caller, and is
--     excluded from the application role's INSERT privilege, so a caller cannot file spend under a different day.
--   * Only a queue_worker of the clinic may INSERT (the gateway runs in the worker; the API enqueues work, it does not call models).
--     admin, business_owner, auditor and queue_worker of the clinic may SELECT. Nothing else sees it. The idempotency key is unique PER
--     CLINIC, so one clinic's worker can neither collide with nor probe another clinic's keys.
--   * Every row counts toward the day's total, the SAFETY class included: a ceiling limits ORDINARY calls, but safety spend is real spend
--     and is shown honestly. Heavy safety use can therefore make ordinary calls hit the ceiling sooner; it can never be refused itself (§6.1).
--   * RESIDUAL RISKS, stated: (1) the ledger is append-only and amounts cannot be negative, so a mistaken entry (a mis-priced registry row, a
--     worker bug) cannot be reversed in place and the only repair is the migrator; a reversal / adjustment mechanism is a later change.
--     (2) A worker can over-record, which starves its own clinic's ORDINARY calls for the rest of the day, or under-record by writing 0 or
--     nothing. The worker is a trusted process identity; the database bounds who may write and what shape a row has, not whether the amount
--     is honest. (3) Totals are meant to be read per origin: the gateway's adapter filters by origin, the table does not.
--   * No patient data: a clinic, a role name, a provider, a pinned model id, a call class, two amounts and an origin.
--
-- PART B. ai.model_registry: a phi_approved row now needs a REAL approver, enforced in the database (0015 only required a name and a date).
--   * New column created_by: the account that added the row (the migrator has no identity of its own, so the operator names themselves;
--     if a signed-in caller ever inserts, the trigger forces created_by = auth.uid()).
--   * For a phi_approved row the trigger requires: created_by is set and is an active staff account; approved_by is an ACTIVE STAFF account
--     holding a live admin or doctor grant in some clinic; approved_by is NOT created_by (two different people); approved_at is not in the
--     future (one minute of clock skew allowed). Any failure is a check_violation with a fixed message and no id in it.
--   * The identity guard of 0015 is replaced (fix forward, CREATE OR REPLACE) so created_by is immutable too.
--   * The approval is checked at INSERT: a later change to the approver's account does not rewrite history, and rows are never edited.
--   RESIDUAL RISK, stated: the database proves that the two accounts named are eligible (active staff, an admin or doctor grant) and
--   DIFFERENT; it does NOT prove that the approver consented or that the creator is the person at the keyboard. When the migrator inserts,
--   created_by is self-attested, and one operator can name any eligible pair. The migrator can also disable triggers, as for audit.log
--   (BUILD-SPEC.md 18.1). Real attestation needs a signed-in approver action, which is a later feature. approved_at is bounded above only.
--   The creator may hold any live staff grant (a service account counts).

-- ---- PART A ---------------------------------------------------------------------------------------------------------------------------

create table ops.spend_ledger (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    -- The clinic's day, from the database clock, India time. Not writable by the application role (column privileges below).
    day             date not null default ((clock_timestamp() at time zone 'Asia/Kolkata')::date),
    role            text not null check (role in ('llm_large', 'llm_planner', 'llm_fast', 'vlm', 'embed', 'rerank', 'llm_judge',
                                                   'decision', 'voice_live')),
    provider        text not null check (length(btrim(provider)) > 0),
    model_id        text not null check (length(btrim(model_id)) > 0),
    call_class      text not null check (call_class in ('ordinary', 'safety')),
    usd_actual      numeric(14, 6) not null check (usd_actual >= 0),
    usd_shadow      numeric(14, 6) not null check (usd_shadow >= 0),
    -- Origin tag (§0): no default, so a test row can never be silently tagged `live`.
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default clock_timestamp(),
    -- Rule 5: every write carries a key, unique PER CLINIC (not across clinics: one clinic's worker must not collide with another's).
    idempotency_key text not null check (length(idempotency_key) > 0),
    constraint spend_ledger_key_per_clinic unique (clinic_id, idempotency_key)
);

create index spend_ledger_clinic_day_idx on ops.spend_ledger (clinic_id, day);

create function ops.spend_ledger_immutable() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'ops.spend_ledger is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;

create trigger spend_ledger_no_update_delete before update or delete on ops.spend_ledger
    for each row execute function ops.spend_ledger_immutable();
create trigger spend_ledger_no_truncate before truncate on ops.spend_ledger
    for each statement execute function ops.spend_ledger_immutable();

alter table ops.spend_ledger enable row level security;
alter table ops.spend_ledger force row level security;

create policy spend_ledger_insert_worker on ops.spend_ledger for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy spend_ledger_select on ops.spend_ledger for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker', 'admin', 'business_owner', 'auditor']));

revoke all on ops.spend_ledger from public;
grant select on ops.spend_ledger to careflow_app;
-- Column-level INSERT: id, day and created_at always come from the database.
grant insert (clinic_id, role, provider, model_id, call_class, usd_actual, usd_shadow, origin, idempotency_key)
    on ops.spend_ledger to careflow_app;

-- ---- PART B ---------------------------------------------------------------------------------------------------------------------------

alter table ai.model_registry add column created_by uuid references iam.accounts (id);

create or replace function ai.model_registry_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        if (new.id, new.role, new.provider, new.model_id, new.data_policy, new.unit, new.approved_by, new.approved_at,
            new.created_by, new.origin, new.created_at, new.idempotency_key)
           is distinct from
           (old.id, old.role, old.provider, old.model_id, old.data_policy, old.unit, old.approved_by, old.approved_at,
            old.created_by, old.origin, old.created_at, old.idempotency_key) then
            raise exception 'a registry row''s identity and data policy are immutable: add a new row and disable this one'
                using errcode = 'restrict_violation';
        end if;
        return new;
    end if;
    raise exception 'ai.model_registry is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;

-- SECURITY DEFINER with a pinned search_path: it must read iam.staff_roles and iam.accounts whatever role inserts the row. Those tables
-- are forced-RLS with policies only `to careflow_app`, so the definer (the owner) reads them because the owner has BYPASSRLS, the same
-- dependency iam.has_clinic_role (0001) and msg.record_screening (0013) have.
create function ai.model_registry_check_approval() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    if new.data_policy <> 'phi_approved' then
        return new;
    end if;
    if new.created_by is null then
        raise exception 'a phi_approved model must name the account that added it (created_by)' using errcode = 'check_violation';
    end if;
    if auth.uid() is not null and new.created_by is distinct from auth.uid() then
        raise exception 'created_by must be the signed-in account that adds the row' using errcode = 'check_violation';
    end if;
    if new.approved_by = new.created_by then
        raise exception 'the approver of a phi_approved model cannot be the account that added it' using errcode = 'check_violation';
    end if;
    if not exists (
        select 1 from iam.accounts a
        where a.id = new.created_by and a.status = 'active' and a.account_type = 'staff'
          and exists (select 1 from iam.staff_roles sr where sr.account_id = a.id and sr.revoked_at is null)
    ) then
        raise exception 'created_by must be an active staff account' using errcode = 'check_violation';
    end if;
    if not exists (
        select 1 from iam.accounts a join iam.staff_roles sr on sr.account_id = a.id
        where a.id = new.approved_by and a.status = 'active' and a.account_type = 'staff'
          and sr.revoked_at is null and sr.role in ('admin', 'doctor')
    ) then
        raise exception 'the approver of a phi_approved model must be an active staff account holding an admin or doctor grant'
            using errcode = 'check_violation';
    end if;
    if new.approved_at > clock_timestamp() + interval '1 minute' then
        raise exception 'the approval of a phi_approved model cannot be dated in the future' using errcode = 'check_violation';
    end if;
    return new;
end
$$;

create trigger model_registry_check_approval before insert on ai.model_registry
    for each row execute function ai.model_registry_check_approval();

revoke all on function ai.model_registry_check_approval() from public;
revoke all on function ops.spend_ledger_immutable() from public;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.spend_ledger from %I', r);
            execute format('revoke all on function ai.model_registry_check_approval() from %I', r);
            execute format('revoke all on function ops.spend_ledger_immutable() from %I', r);
        end if;
    end loop;
end $$;

comment on table ops.spend_ledger is
    'Append-only model spend per clinic per India-time day (BUILD-SPEC 6.1, 15.2). The day comes from the database clock. No patient data.';
