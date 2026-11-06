-- 0044_care_gaps: the data behind the Care Gaps panel (Phase 9 follow-up, owner decision 2026-11-06 item 2; BUILD-SPEC.md 9.3, 12.6, 22.4, 24; CLAUDE.md rules 5, 6, 8, 10).
--
-- FIVE NEW TABLES in schema ops, every one with forced row-level security from this migration, an origin tag with no default, and a key unique per clinic (rule 5):
--
--   ops.care_recurrence_rules   a recurrence rule (9.3): an interval in days OR an annual month, and the grace in days after the due date. CLINICIAN-AUTHORED DATA: no rule is created by this migration.
--   ops.care_episodes           a care episode of a patient (a pregnancy, a chronic condition, a post-op follow-up): a code word, a start date, an optional close date. The episode's content is clinician data.
--   ops.care_episode_rules      which rule governs which episode, and when it was last done (NULL = unknown, which the panel treats as unknown, never as overdue and never as fine).
--   ops.care_barriers           a no-show barrier (22.4) with a CLOSED set of types and a status open -> resolved (final).
--   ops.care_review_samples     the after-the-fact sample (12.6): a reference to an auto-approved decision awaiting a person's re-check. The SAMPLING STEP (a producer) is not part of this migration.
--
-- WHO. Nurse, doctor and admin of the clinic may READ all five (the panel). They may INSERT a barrier and RESOLVE one (the only writes the application role has here). Rules, episodes, episode-rules and samples
-- are SELECT-only for the application role: their producers are separate, reviewed packages (clinician data entry; the sampler), and the owner (migrator) seeds them for tests and demos. Front desk, auditor,
-- business owner and the queue worker have no access. No table holds a name, a diagnosis or free text: codes are shape-checked words, dates, ids and closed words.
--
-- A BARRIER is changed once: open -> resolved, by the caller (resolved_by = themselves), with the time taken from the database clock by a trigger; a resolved barrier is final; nothing is ever deleted
-- (no DELETE grant, a trigger blocks it for every role, as for ops.alerts). The patient must be of the same clinic: a composite foreign key (patient_id, clinic_id) on mpi.patients.
--
-- RESIDUALS. (0) A barrier can be recorded for an erased patient, and a sample's decision is not tied to the sample's clinic by a key (only the owner/sampler can write samples; the sampler package must set both). (1) A nurse, doctor or admin of the clinic can record a barrier for ANY patient of that clinic, and resolve any open barrier of the clinic (no assignment model exists); both are audited by the
-- routes. (2) The panel's ages come from the database clock but the owner can edit rows. (3) Rows accumulate; retention is a later, audited package.

create table ops.care_recurrence_rules (
    id                 uuid primary key default gen_random_uuid(),
    clinic_id          uuid not null references iam.clinics (id),
    origin             text not null check (origin in ('live', 'test', 'eval', 'canary')),
    rule_code          text not null check (rule_code ~ '^[a-z][a-z0-9_]{0,59}$'),
    interval_days      integer check (interval_days >= 1),
    annual_month       smallint check (annual_month between 1 and 12),
    overdue_after_days integer not null check (overdue_after_days >= 0),
    created_at         timestamptz not null default clock_timestamp(),
    idempotency_key    text not null check (length(idempotency_key) between 1 and 300),
    constraint care_rules_one_cycle check ((interval_days is null) <> (annual_month is null)),
    constraint care_rules_key_per_clinic unique (clinic_id, idempotency_key),
    constraint care_rules_id_clinic unique (id, clinic_id)
);

create table ops.care_episodes (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    patient_id      uuid not null,
    episode_code    text not null check (episode_code ~ '^[a-z][a-z0-9_]{0,59}$'),
    started_on      date not null,
    closed_on       date,
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint care_episodes_key_per_clinic unique (clinic_id, idempotency_key),
    constraint care_episodes_id_clinic unique (id, clinic_id),
    constraint care_episodes_closed_after_start check (closed_on is null or closed_on >= started_on),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id)
);

create table ops.care_episode_rules (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    episode_id      uuid not null,
    rule_id         uuid not null,
    last_done_on    date,
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint care_episode_rules_key_per_clinic unique (clinic_id, idempotency_key),
    constraint care_episode_rules_one_rule_per_episode unique (episode_id, rule_id),
    foreign key (episode_id, clinic_id) references ops.care_episodes (id, clinic_id),
    foreign key (rule_id, clinic_id) references ops.care_recurrence_rules (id, clinic_id)
);

create table ops.care_barriers (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    patient_id      uuid not null,
    barrier         text not null check (barrier in ('transport', 'cost', 'childcare', 'forgot', 'scheduling_conflict', 'other')),
    status          text not null default 'open' check (status in ('open', 'resolved')),
    opened_at       timestamptz not null default clock_timestamp(),
    resolved_by     uuid references iam.accounts (id),
    resolved_at     timestamptz,
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint care_barriers_key_per_clinic unique (clinic_id, idempotency_key),
    constraint care_barriers_resolution_pair check ((status = 'open') = (resolved_by is null and resolved_at is null)),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id)
);

create table ops.care_review_samples (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    decision_id     uuid not null references agents.review_decisions (id),
    sampled_at      timestamptz not null default clock_timestamp(),
    rechecked_by    uuid references iam.accounts (id),
    rechecked_at    timestamptz,
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint care_samples_key_per_clinic unique (clinic_id, idempotency_key),
    constraint care_samples_one_per_decision unique (decision_id),
    constraint care_samples_recheck_pair check ((rechecked_by is null) = (rechecked_at is null))
);

create index care_barriers_open_idx on ops.care_barriers (clinic_id, opened_at) where status = 'open';
create index care_samples_open_idx on ops.care_review_samples (clinic_id, sampled_at) where rechecked_at is null;
create index care_episode_rules_clinic_idx on ops.care_episode_rules (clinic_id);

create function ops.care_barriers_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_barriers rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.status <> 'open' then
        raise exception 'a resolved barrier is final' using errcode = 'restrict_violation';
    end if;
    if new.status <> 'resolved' or new.clinic_id <> old.clinic_id or new.patient_id <> old.patient_id or new.barrier <> old.barrier or new.opened_at <> old.opened_at
       or new.idempotency_key <> old.idempotency_key or new.origin <> old.origin or new.id <> old.id then
        raise exception 'a barrier can only be resolved' using errcode = 'restrict_violation';
    end if;
    new.resolved_at := clock_timestamp();  -- the time is the database's, never the caller's
    return new;
end
$$;

create trigger care_barriers_guard before update or delete on ops.care_barriers for each row execute function ops.care_barriers_guard();
create trigger care_barriers_no_truncate before truncate on ops.care_barriers for each statement execute function ops.care_barriers_guard();

alter table ops.care_recurrence_rules enable row level security;
alter table ops.care_recurrence_rules force row level security;
alter table ops.care_episodes enable row level security;
alter table ops.care_episodes force row level security;
alter table ops.care_episode_rules enable row level security;
alter table ops.care_episode_rules force row level security;
alter table ops.care_barriers enable row level security;
alter table ops.care_barriers force row level security;
alter table ops.care_review_samples enable row level security;
alter table ops.care_review_samples force row level security;

create policy care_rules_select on ops.care_recurrence_rules for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));
create policy care_episodes_select on ops.care_episodes for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));
create policy care_episode_rules_select on ops.care_episode_rules for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));
create policy care_samples_select on ops.care_review_samples for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));
create policy care_barriers_select on ops.care_barriers for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));
create policy care_barriers_insert on ops.care_barriers for insert to careflow_app
    with check (status = 'open' and resolved_by is null and resolved_at is null and iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));
create policy care_barriers_resolve on ops.care_barriers for update to careflow_app
    using (status = 'open' and iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']))
    with check (status = 'resolved' and resolved_by = auth.uid() and iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));

revoke all on ops.care_recurrence_rules, ops.care_episodes, ops.care_episode_rules, ops.care_barriers, ops.care_review_samples from public;
grant select on ops.care_recurrence_rules, ops.care_episodes, ops.care_episode_rules, ops.care_barriers, ops.care_review_samples to careflow_app;
grant insert (clinic_id, origin, patient_id, barrier, idempotency_key) on ops.care_barriers to careflow_app;
grant update (status, resolved_by) on ops.care_barriers to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.care_recurrence_rules, ops.care_episodes, ops.care_episode_rules, ops.care_barriers, ops.care_review_samples from %I', r);
        end if;
    end loop;
end $$;

do $$
declare t text;
begin
    foreach t in array array['care_recurrence_rules', 'care_episodes', 'care_episode_rules', 'care_barriers', 'care_review_samples'] loop
        if not (select c.relrowsecurity and c.relforcerowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname = 'ops' and c.relname = t) then
            raise exception '0044: ops.% must have forced row-level security', t;
        end if;
        if has_table_privilege('careflow_app', 'ops.' || t, 'delete') or has_table_privilege('careflow_app', 'ops.' || t, 'truncate') then
            raise exception '0044: the application role must not delete from ops.%', t;
        end if;
        if exists (select 1 from pg_roles r where r.rolname in ('anon', 'authenticated', 'service_role') and has_table_privilege(r.rolname, 'ops.' || t, 'select')) then
            raise exception '0044: anon, authenticated and service_role must have no access to ops.%', t;
        end if;
    end loop;
    foreach t in array array['care_recurrence_rules', 'care_episodes', 'care_episode_rules', 'care_review_samples'] loop
        if has_table_privilege('careflow_app', 'ops.' || t, 'insert') or has_table_privilege('careflow_app', 'ops.' || t, 'update') then
            raise exception '0044: ops.% must be read-only for the application role', t;
        end if;
    end loop;
    if has_any_column_privilege('careflow_app', 'ops.care_barriers', 'insert') is not true or has_table_privilege('careflow_app', 'ops.care_barriers', 'update') then
        raise exception '0044: barrier privileges are not as designed (column-level insert and update only)';
    end if;
    if (select count(*) from pg_policies where schemaname = 'ops' and tablename like 'care\_%') <> 7 then
        raise exception '0044: expected 7 care-gap policies';
    end if;
end $$;
