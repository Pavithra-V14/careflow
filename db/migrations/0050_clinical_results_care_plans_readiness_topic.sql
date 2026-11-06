-- 0050_clinical_results_care_plans_readiness_topic (owner decisions of 2026-11-06 items A and C; BUILD-SPEC.md 9.5, 22.5, 17.5, Phase 6; CLAUDE.md rules 3, 5, 6, 8, 10).
--
-- (1) The `readiness` messaging topic (spec 22.5, promised by 0011): the three topic check constraints (threads, messages, screenings) gain the word. The existing policies already give nurse and doctor every topic
--     and front desk only an allowlist (appointments, safety), so a readiness thread is nurse/doctor only until a migration says otherwise.
-- (2) clinical.results: a BI-TEMPORAL result store (spec 9.5): valid_from / valid_to say when the result held in the world, recorded_at says when the system learned it, and a correction is a NEW row that
--     names the one it supersedes (never an update; a result is superseded at most once). Append-only for everyone. `verified` is explicit with no default: an unverified result satisfies no rule (rule 3).
--     `measures` holds only numbers or nulls by field name; the application validates the shape and a database check keeps it an object of bounded size. Nurse and doctor of the clinic read and write, as
--     themselves, with a verified second factor re-checked by a restrictive policy on iam.aal2_session(). Medications and notes are NOT built (they need coded vocabularies, which are clinician content).
-- (3) Care plans for the Care Journey agent (Phase 6): ops.care_plans (the doctor's plan text ENCRYPTED, a status the graph moves), ops.care_plan_steps (the closed plan schema once a doctor confirms it) and
--     ops.care_plan_waits (one per result step; the first result to claim a wait holds it). Doctor creates a plan; nurse and doctor read; the graph's ports write as the signed-in person.
--
-- The plan text is clinical-record free text about a person, so it is encrypted like a message body (ciphertext + key version) and never leaves the application unencrypted. No agent keeps its own memory of a
-- patient here (rule 6): these are clinical record and operational state, written by a person's request and changed only by the closed transitions below.

-- (1)
alter table msg.threads drop constraint threads_topic_check;
alter table msg.threads add constraint threads_topic_check check (topic in ('care', 'appointments', 'safety', 'readiness'));
alter table msg.messages drop constraint messages_topic_check;
alter table msg.messages add constraint messages_topic_check check (topic in ('care', 'appointments', 'safety', 'readiness'));
alter table msg.screenings drop constraint screenings_topic_check;
alter table msg.screenings add constraint screenings_topic_check check (topic in ('care', 'appointments', 'safety', 'readiness'));

-- (2)
create table clinical.results (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    patient_id      uuid not null,
    code            text not null check (code ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$'),
    measures        jsonb not null check (jsonb_typeof(measures) = 'object' and octet_length(measures::text) <= 4096),
    verified        boolean not null,
    valid_from      timestamptz not null,
    valid_to        timestamptz,
    recorded_at     timestamptz not null default clock_timestamp(),
    recorded_by     uuid not null references iam.accounts (id),
    supersedes_id   uuid,
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint results_key_per_clinic unique (clinic_id, idempotency_key),
    constraint results_id_clinic unique (id, clinic_id),
    constraint results_id_clinic_patient unique (id, clinic_id, patient_id),
    constraint results_superseded_once unique (supersedes_id),
    constraint results_valid_range check (valid_to is null or valid_to > valid_from),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    foreign key (supersedes_id, clinic_id, patient_id) references clinical.results (id, clinic_id, patient_id)  -- a correction names a result of the SAME patient
);
create index results_patient_idx on clinical.results (clinic_id, patient_id, valid_from desc);

create function clinical.results_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'clinical.results is append-only: % is not permitted (a correction is a new row that supersedes the old one)', tg_op using errcode = 'restrict_violation';
end
$$;
revoke all on function clinical.results_guard() from public;
create trigger results_no_update_delete before update or delete on clinical.results for each row execute function clinical.results_guard();
create trigger results_no_truncate before truncate on clinical.results for each statement execute function clinical.results_guard();
create trigger results_not_erased before insert on clinical.results for each row execute function ops.care_barriers_not_erased();

alter table clinical.results enable row level security;
alter table clinical.results force row level security;
create policy results_select on clinical.results for select to careflow_app using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy results_insert on clinical.results for insert to careflow_app
    with check (recorded_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy results_aal2 on clinical.results as restrictive for insert to careflow_app with check (iam.aal2_session());
revoke all on clinical.results from public;
grant usage on schema clinical to careflow_app;
grant select on clinical.results to careflow_app;
grant insert (clinic_id, origin, patient_id, code, measures, verified, valid_from, valid_to, recorded_by, supersedes_id, idempotency_key) on clinical.results to careflow_app;

-- (3)
create table ops.care_plans (
    id               uuid primary key default gen_random_uuid(),
    clinic_id        uuid not null references iam.clinics (id),
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    patient_id       uuid not null,
    plan_ct          bytea not null check (octet_length(plan_ct) between 1 and 40000),
    plan_key_version integer not null check (plan_key_version >= 1),
    status           text not null default 'screening' check (status in ('screening', 'awaiting_confirmation', 'active', 'completed', 'rejected', 'held')),
    created_by       uuid not null references iam.accounts (id),
    created_at       timestamptz not null default clock_timestamp(),
    updated_at       timestamptz not null default clock_timestamp(),
    idempotency_key  text not null check (length(idempotency_key) between 1 and 300),
    constraint care_plans_key_per_clinic unique (clinic_id, idempotency_key),
    constraint care_plans_id_clinic unique (id, clinic_id),
    constraint care_plans_id_clinic_patient unique (id, clinic_id, patient_id),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id)
);

create table ops.care_plan_steps (
    id                 uuid primary key default gen_random_uuid(),
    clinic_id          uuid not null,
    plan_id            uuid not null,
    step_key           text not null check (step_key ~ '^[a-z][a-z0-9_]{0,39}$'),
    position           integer not null check (position >= 0),
    kind               text not null check (kind in ('task', 'wait_result')),
    label              text not null check (length(label) between 1 and 120),
    result_prefix      text check (result_prefix ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$'),
    cond_field         text check (cond_field ~ '^[a-z][a-z0-9_]{0,39}$'),
    cond_op            text check (cond_op in ('lt', 'le', 'gt', 'ge', 'eq')),
    cond_threshold     double precision check (cond_threshold = cond_threshold and abs(cond_threshold) < 'Infinity'::float8),  -- no NaN, no infinity: they would satisfy a rule without real data
    on_met             text check (on_met in ('propose_referral')),
    status             text not null default 'open' check (status in ('open', 'met', 'not_met', 'needs_doctor', 'closed')),
    evidence_result_id uuid,
    idempotency_key    text not null check (length(idempotency_key) between 1 and 300),
    constraint care_plan_steps_one_key_per_plan unique (plan_id, step_key),
    constraint care_plan_steps_one_position_per_plan unique (plan_id, position),
    constraint care_plan_steps_key_per_clinic unique (clinic_id, idempotency_key),
    constraint care_plan_steps_task_has_no_wait_fields check (kind = 'wait_result' or (result_prefix is null and cond_field is null and cond_op is null and cond_threshold is null and on_met is null)),
    constraint care_plan_steps_wait_has_prefix check (kind = 'task' or result_prefix is not null),
    constraint care_plan_steps_condition_whole check ((cond_field is null) = (cond_op is null) and (cond_op is null) = (cond_threshold is null)),
    constraint care_plan_steps_on_met_needs_condition check (on_met is null or cond_field is not null),
    constraint care_plan_steps_closed_cites_a_result check (status not in ('met', 'not_met') or evidence_result_id is not null),
    foreign key (plan_id, clinic_id) references ops.care_plans (id, clinic_id),
    foreign key (evidence_result_id, clinic_id) references clinical.results (id, clinic_id)
);

create table ops.care_plan_waits (
    id                 uuid primary key default gen_random_uuid(),
    clinic_id          uuid not null,
    plan_id            uuid not null,
    step_key           text not null,
    wait_key           text not null check (length(wait_key) between 1 and 200),
    patient_id         uuid not null,
    prefix             text not null check (prefix ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$'),
    open               boolean not null default true,
    claimed_result_id  uuid,
    idempotency_key    text not null check (length(idempotency_key) between 1 and 300),
    constraint care_plan_waits_wait_key unique (clinic_id, wait_key),
    constraint care_plan_waits_key_per_clinic unique (clinic_id, idempotency_key),
    foreign key (plan_id, step_key) references ops.care_plan_steps (plan_id, step_key),
    foreign key (plan_id, clinic_id, patient_id) references ops.care_plans (id, clinic_id, patient_id),  -- a wait belongs to the plan's own patient
    foreign key (claimed_result_id, clinic_id, patient_id) references clinical.results (id, clinic_id, patient_id)  -- and can be claimed only by a result of that patient
);
create index care_plan_waits_open_idx on ops.care_plan_waits (clinic_id, patient_id) where open;

-- transitions: a finished plan is final; a plan's identity never changes; a step closes once; a wait is claimed once and the claim is never replaced
create function ops.care_plans_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plans rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.status in ('completed', 'rejected') then
        raise exception 'a finished care plan is final' using errcode = 'restrict_violation';
    end if;
    if new.id <> old.id or new.clinic_id <> old.clinic_id or new.origin <> old.origin or new.patient_id <> old.patient_id or new.plan_ct <> old.plan_ct
       or new.plan_key_version <> old.plan_key_version or new.created_by <> old.created_by or new.created_at <> old.created_at or new.idempotency_key <> old.idempotency_key then
        raise exception 'only the status of a care plan can change' using errcode = 'restrict_violation';
    end if;
    if new.status <> old.status then
        -- the closed transitions: screening -> awaiting_confirmation | held | rejected; awaiting_confirmation -> active | rejected | held; held -> awaiting_confirmation | active | rejected; active -> completed | held
        if not ((old.status, new.status) in (('screening', 'awaiting_confirmation'), ('screening', 'held'), ('screening', 'rejected'), ('awaiting_confirmation', 'active'), ('awaiting_confirmation', 'rejected'),
                                             ('awaiting_confirmation', 'held'), ('held', 'awaiting_confirmation'), ('held', 'active'), ('held', 'rejected'), ('active', 'completed'), ('active', 'held'))) then
            raise exception 'that care plan status change is not allowed' using errcode = 'restrict_violation';
        end if;
        -- only a DOCTOR confirms (activates) or rejects a plan, and a plan with no steps cannot be activated
        if new.status in ('active', 'rejected') and not iam.has_clinic_role(new.clinic_id, array['doctor']) then
            raise exception 'only a doctor can confirm or reject a care plan' using errcode = 'insufficient_privilege';
        end if;
        if new.status = 'active' and not exists (select 1 from ops.care_plan_steps s where s.plan_id = new.id and s.clinic_id = new.clinic_id) then
            raise exception 'a care plan with no steps cannot be activated' using errcode = 'restrict_violation';
        end if;
    end if;
    new.updated_at := clock_timestamp();
    return new;
end
$$;
create function ops.care_plan_steps_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plan_steps rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.status <> 'open' and old.status <> 'needs_doctor' then
        raise exception 'a closed care plan step is final' using errcode = 'restrict_violation';
    end if;
    if (to_jsonb(new) - 'status' - 'evidence_result_id') <> (to_jsonb(old) - 'status' - 'evidence_result_id') then
        raise exception 'only the status and the evidence of a step can change' using errcode = 'restrict_violation';
    end if;
    if exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status in ('completed', 'rejected')) then
        raise exception 'the care plan is finished' using errcode = 'restrict_violation';
    end if;
    if new.status in ('met', 'not_met') then
        -- a step closes only on a result of the plan's OWN patient, VERIFIED, not superseded, whose code starts with the step's prefix (an unverified result satisfies no rule)
        if new.kind <> 'wait_result' or not exists (
            select 1 from clinical.results r join ops.care_plans p on p.id = new.plan_id and p.clinic_id = new.clinic_id
             where r.id = new.evidence_result_id and r.clinic_id = new.clinic_id and r.patient_id = p.patient_id and r.verified
               and left(lower(r.code), length(new.result_prefix)) = lower(new.result_prefix)
               and not exists (select 1 from clinical.results s where s.supersedes_id = r.id)
        ) then
            raise exception 'a step can close only on a verified result of this patient that matches the step' using errcode = 'restrict_violation';
        end if;
    end if;
    return new;
end
$$;
create function ops.care_plan_waits_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plan_waits rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if (to_jsonb(new) - 'open' - 'claimed_result_id') <> (to_jsonb(old) - 'open' - 'claimed_result_id') then
        raise exception 'only the claim of a wait can change' using errcode = 'restrict_violation';
    end if;
    if old.claimed_result_id is not null and new.claimed_result_id is distinct from old.claimed_result_id then
        raise exception 'the first result to claim a wait keeps it' using errcode = 'restrict_violation';
    end if;
    if old.open is false and new.open is true then
        raise exception 'a closed wait stays closed' using errcode = 'restrict_violation';
    end if;
    if exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status in ('completed', 'rejected')) then
        raise exception 'the care plan is finished' using errcode = 'restrict_violation';
    end if;
    if new.claimed_result_id is not null and old.claimed_result_id is null and not exists (
        select 1 from clinical.results r where r.id = new.claimed_result_id and r.clinic_id = new.clinic_id and r.patient_id = new.patient_id
           and left(lower(r.code), length(new.prefix)) = lower(new.prefix)
    ) then
        raise exception 'a wait can be claimed only by a result of this patient that matches its prefix' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;
revoke all on function ops.care_plans_guard(), ops.care_plan_steps_guard(), ops.care_plan_waits_guard() from public;
create trigger care_plans_guard before update or delete on ops.care_plans for each row execute function ops.care_plans_guard();
create trigger care_plans_no_truncate before truncate on ops.care_plans for each statement execute function ops.care_plans_guard();
create trigger care_plan_steps_guard before update or delete on ops.care_plan_steps for each row execute function ops.care_plan_steps_guard();
create trigger care_plan_steps_no_truncate before truncate on ops.care_plan_steps for each statement execute function ops.care_plan_steps_guard();
create trigger care_plan_waits_guard before update or delete on ops.care_plan_waits for each row execute function ops.care_plan_waits_guard();
create trigger care_plan_waits_no_truncate before truncate on ops.care_plan_waits for each statement execute function ops.care_plan_waits_guard();
create trigger care_plans_not_erased before insert on ops.care_plans for each row execute function ops.care_barriers_not_erased();

-- steps may be added only while the plan is being proposed (status screening); waits only once a doctor has activated it
create function ops.care_plan_children_insert_guard() returns trigger
    language plpgsql set search_path = ''
as $$
declare
    v_status text;
begin
    select status into v_status from ops.care_plans where id = new.plan_id and clinic_id = new.clinic_id;
    if tg_table_name = 'care_plan_steps' and v_status is distinct from 'screening' then
        raise exception 'steps can be added only while the plan is being proposed' using errcode = 'restrict_violation';
    end if;
    if tg_table_name = 'care_plan_waits' and v_status is distinct from 'active' then
        raise exception 'waits can be added only to an active plan' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;
revoke all on function ops.care_plan_children_insert_guard() from public;
create trigger care_plan_steps_insert_guard before insert on ops.care_plan_steps for each row execute function ops.care_plan_children_insert_guard();
create trigger care_plan_waits_insert_guard before insert on ops.care_plan_waits for each row execute function ops.care_plan_children_insert_guard();

alter table ops.care_plans enable row level security;
alter table ops.care_plans force row level security;
alter table ops.care_plan_steps enable row level security;
alter table ops.care_plan_steps force row level security;
alter table ops.care_plan_waits enable row level security;
alter table ops.care_plan_waits force row level security;

create policy care_plans_select on ops.care_plans for select to careflow_app using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_plans_insert on ops.care_plans for insert to careflow_app
    with check (created_by = auth.uid() and status = 'screening' and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['doctor']));
create policy care_plans_update on ops.care_plans for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])) with check (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_plans_aal2_insert on ops.care_plans as restrictive for insert to careflow_app with check (iam.aal2_session());
create policy care_plans_aal2_update on ops.care_plans as restrictive for update to careflow_app using (iam.aal2_session()) with check (iam.aal2_session());

create policy care_plan_steps_select on ops.care_plan_steps for select to careflow_app using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_plan_steps_insert on ops.care_plan_steps for insert to careflow_app with check (iam.has_clinic_role(clinic_id, array['doctor']));
create policy care_plan_steps_update on ops.care_plan_steps for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])) with check (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_plan_steps_aal2_insert on ops.care_plan_steps as restrictive for insert to careflow_app with check (iam.aal2_session());
create policy care_plan_steps_aal2_update on ops.care_plan_steps as restrictive for update to careflow_app using (iam.aal2_session()) with check (iam.aal2_session());

create policy care_plan_waits_select on ops.care_plan_waits for select to careflow_app using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_plan_waits_insert on ops.care_plan_waits for insert to careflow_app with check (iam.has_clinic_role(clinic_id, array['doctor']));
create policy care_plan_waits_update on ops.care_plan_waits for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])) with check (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_plan_waits_aal2_insert on ops.care_plan_waits as restrictive for insert to careflow_app with check (iam.aal2_session());
create policy care_plan_waits_aal2_update on ops.care_plan_waits as restrictive for update to careflow_app using (iam.aal2_session()) with check (iam.aal2_session());

revoke all on ops.care_plans, ops.care_plan_steps, ops.care_plan_waits from public;
grant select on ops.care_plans, ops.care_plan_steps, ops.care_plan_waits to careflow_app;
grant insert (clinic_id, origin, patient_id, plan_ct, plan_key_version, created_by, idempotency_key) on ops.care_plans to careflow_app;
grant update (status) on ops.care_plans to careflow_app;
grant insert (clinic_id, plan_id, step_key, position, kind, label, result_prefix, cond_field, cond_op, cond_threshold, on_met, idempotency_key) on ops.care_plan_steps to careflow_app;
grant update (status, evidence_result_id) on ops.care_plan_steps to careflow_app;
grant insert (clinic_id, plan_id, step_key, wait_key, patient_id, prefix, idempotency_key) on ops.care_plan_waits to careflow_app;
grant update (open, claimed_result_id) on ops.care_plan_waits to careflow_app;

do $$
declare
    r text;
    t text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on clinical.results, ops.care_plans, ops.care_plan_steps, ops.care_plan_waits from %I', r);
            execute format('revoke all on function clinical.results_guard() from %I', r);
            execute format('revoke all on function ops.care_plans_guard() from %I', r);
            execute format('revoke all on function ops.care_plan_steps_guard() from %I', r);
            execute format('revoke all on function ops.care_plan_waits_guard() from %I', r);
            execute format('revoke all on function ops.care_plan_children_insert_guard() from %I', r);
        end if;
    end loop;
    foreach t in array array['clinical.results_guard()', 'ops.care_plans_guard()', 'ops.care_plan_steps_guard()', 'ops.care_plan_waits_guard()', 'ops.care_plan_children_insert_guard()', 'ops.care_barriers_not_erased()'] loop
        if has_function_privilege('public', t, 'execute') or has_function_privilege('careflow_app', t, 'execute') then
            raise exception '0050: % must not be executable by PUBLIC or the application role', t;
        end if;
    end loop;
    foreach t in array array['clinical.results', 'ops.care_plans', 'ops.care_plan_steps', 'ops.care_plan_waits'] loop
        if has_table_privilege('careflow_app', t, 'delete') or has_table_privilege('careflow_app', t, 'truncate') then
            raise exception '0050: the application role must not delete from %', t;
        end if;
        if not (select c.relrowsecurity and c.relforcerowsecurity from pg_class c where c.oid = t::regclass) then
            raise exception '0050: % must have forced row-level security', t;
        end if;
    end loop;
    if has_any_column_privilege('careflow_app', 'clinical.results', 'update') then
        raise exception '0050: clinical.results is append-only for the application role';
    end if;
end $$;
