-- 0052_visit_readiness (owner decision of 2026-11-07, item 1; BUILD-SPEC.md 22; CLAUDE.md rules 1, 3, 5, 6, 8, 10): the storage behind the Visit Readiness agent, to mechanism level.
--
-- A VISIT is a booking (sched.bookings). Three append-only tables, all nurse and doctor of the clinic (readiness is a nurse topic), every write as themselves with a verified second factor re-checked by a
-- restrictive policy, every read needing one too (the queue worker is not involved: the agent runs in the request of the nurse or doctor who started it):
--   * ops.visit_facts            a fact the agent LEARNED from a reply (grounded by code before this is written): the visit, the fact name, the readiness message it came from. The VALUE stays in the message
--                                (encrypted there); this table holds no patient words.
--   * ops.visit_readiness_answers  the answer to a checklist item as a CLOSED word (confirmed / not_done / unclear) and the message it came from.
--   * ops.visit_briefs           the clinician's pre-visit brief: lines of {field, status} only (no value), one per visit.
-- Every row names the booking AND the patient AND the clinic through a composite key, and its source message through (message, clinic, origin, topic 'readiness'), so a fact cannot cite another clinic's or another
-- topic's message. QUESTIONS AND CHECKLISTS ARE NOT HERE: they are clinician content in a required application setting (CAREFLOW_VISIT_READINESS_CONTENT); nothing is seeded.

alter table sched.bookings add constraint bookings_id_clinic_patient unique (id, clinic_id, patient_id);
-- a cited message is pinned to its THREAD, and the thread to the PATIENT, so a fact or answer for one patient cannot cite another patient's reply (two visits of one patient share one readiness thread: the
-- visit itself is not pinned by the message key; the agent's grounding is the only guard for that)
alter table msg.threads add constraint threads_id_clinic_origin_topic_patient unique (id, clinic_id, origin, topic, patient_id);
alter table msg.messages add constraint messages_id_thread_clinic_origin_topic unique (id, thread_id, clinic_id, origin, topic);

create table ops.visit_facts (
    id                uuid primary key default gen_random_uuid(),
    clinic_id         uuid not null references iam.clinics (id),
    origin            text not null check (origin in ('live', 'test', 'eval', 'canary')),
    visit_id          uuid not null,
    patient_id        uuid not null,
    fact              text not null check (fact ~ '^[a-z][a-z0-9_]{0,59}$'),
    thread_id         uuid not null,
    source_message_id uuid not null,
    topic             text not null default 'readiness' check (topic = 'readiness'),
    recorded_by       uuid not null references iam.accounts (id),
    created_at        timestamptz not null default clock_timestamp(),
    idempotency_key   text not null check (length(idempotency_key) between 1 and 300),
    constraint visit_facts_one_per_visit unique (visit_id, fact),
    constraint visit_facts_key_per_clinic unique (clinic_id, idempotency_key),
    foreign key (visit_id, clinic_id, patient_id) references sched.bookings (id, clinic_id, patient_id),
    foreign key (thread_id, clinic_id, origin, topic, patient_id) references msg.threads (id, clinic_id, origin, topic, patient_id),
    foreign key (source_message_id, thread_id, clinic_id, origin, topic) references msg.messages (id, thread_id, clinic_id, origin, topic)
);

create table ops.visit_readiness_answers (
    id                uuid primary key default gen_random_uuid(),
    clinic_id         uuid not null references iam.clinics (id),
    origin            text not null check (origin in ('live', 'test', 'eval', 'canary')),
    visit_id          uuid not null,
    patient_id        uuid not null,
    item              text not null check (item ~ '^[a-z][a-z0-9_]{0,59}$'),
    answer            text not null check (answer in ('confirmed', 'not_done', 'unclear')),
    thread_id         uuid not null,
    source_message_id uuid not null,
    topic             text not null default 'readiness' check (topic = 'readiness'),
    recorded_by       uuid not null references iam.accounts (id),
    created_at        timestamptz not null default clock_timestamp(),
    idempotency_key   text not null check (length(idempotency_key) between 1 and 300),
    -- several rows per item are allowed (a re-ask after an unclear reply records a new answer under a new key); the NEWEST row is the answer
    constraint visit_answers_key_per_clinic unique (clinic_id, idempotency_key),
    foreign key (visit_id, clinic_id, patient_id) references sched.bookings (id, clinic_id, patient_id),
    foreign key (thread_id, clinic_id, origin, topic, patient_id) references msg.threads (id, clinic_id, origin, topic, patient_id),
    foreign key (source_message_id, thread_id, clinic_id, origin, topic) references msg.messages (id, thread_id, clinic_id, origin, topic)
);

create table ops.visit_briefs (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    visit_id        uuid not null,
    patient_id      uuid not null,
    lines           jsonb not null check (jsonb_typeof(lines) = 'array' and octet_length(lines::text) <= 20000),
    created_by      uuid not null references iam.accounts (id),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint visit_briefs_one_per_visit unique (visit_id),
    constraint visit_briefs_key_per_clinic unique (clinic_id, idempotency_key),
    foreign key (visit_id, clinic_id, patient_id) references sched.bookings (id, clinic_id, patient_id)
);

create function ops.visit_readiness_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception '% is append-only: % is not permitted', tg_table_name, tg_op using errcode = 'restrict_violation';
end
$$;
revoke all on function ops.visit_readiness_guard() from public;

do $$
declare t text;
begin
    foreach t in array array['visit_facts', 'visit_readiness_answers', 'visit_briefs'] loop
        execute format('create trigger %I before update or delete on ops.%I for each row execute function ops.visit_readiness_guard()', t || '_no_update_delete', t);
        execute format('create trigger %I before truncate on ops.%I for each statement execute function ops.visit_readiness_guard()', t || '_no_truncate', t);
        execute format('create trigger %I before insert on ops.%I for each row execute function ops.care_barriers_not_erased()', t || '_not_erased', t);
        execute format('alter table ops.%I enable row level security', t);
        execute format('alter table ops.%I force row level security', t);
        execute format('create policy %I on ops.%I for select to careflow_app using (iam.has_clinic_role(clinic_id, array[''nurse'', ''doctor'']))', t || '_select', t);
        execute format('create policy %I on ops.%I as restrictive for select to careflow_app using (iam.aal2_session())', t || '_aal2_select', t);
        execute format('create policy %I on ops.%I as restrictive for insert to careflow_app with check (iam.aal2_session())', t || '_aal2_insert', t);
        execute format('revoke all on ops.%I from public', t);
        execute format('grant select on ops.%I to careflow_app', t);
    end loop;
end $$;

create policy visit_facts_insert on ops.visit_facts for insert to careflow_app
    with check (recorded_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy visit_answers_insert on ops.visit_readiness_answers for insert to careflow_app
    with check (recorded_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy visit_briefs_insert on ops.visit_briefs for insert to careflow_app
    with check (created_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));

grant insert (clinic_id, origin, visit_id, patient_id, fact, thread_id, source_message_id, recorded_by, idempotency_key) on ops.visit_facts to careflow_app;
grant insert (clinic_id, origin, visit_id, patient_id, item, answer, thread_id, source_message_id, recorded_by, idempotency_key) on ops.visit_readiness_answers to careflow_app;
grant insert (clinic_id, origin, visit_id, patient_id, lines, created_by, idempotency_key) on ops.visit_briefs to careflow_app;

do $$
declare
    t text;
    r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.visit_facts, ops.visit_readiness_answers, ops.visit_briefs from %I', r);
            execute format('revoke all on function ops.visit_readiness_guard() from %I', r);
        end if;
    end loop;
    foreach t in array array['ops.visit_facts', 'ops.visit_readiness_answers', 'ops.visit_briefs'] loop
        if has_table_privilege('careflow_app', t, 'update') or has_table_privilege('careflow_app', t, 'delete') or has_table_privilege('careflow_app', t, 'truncate') then
            raise exception '0052: % must be append-only for the application role', t;
        end if;
        if not (select c.relrowsecurity and c.relforcerowsecurity from pg_class c where c.oid = t::regclass) then
            raise exception '0052: % must have forced row-level security', t;
        end if;
    end loop;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and (has_function_privilege(r, 'ops.visit_readiness_guard()', 'execute') or has_table_privilege(r, 'ops.visit_facts', 'select')) then
            raise exception '0052: % must have no access', r;
        end if;
    end loop;
    if has_function_privilege('careflow_app', 'ops.visit_readiness_guard()', 'execute') or has_function_privilege('public', 'ops.visit_readiness_guard()', 'execute') then
        raise exception '0052: the guard function must not be executable by PUBLIC or the application role';
    end if;
end $$;
