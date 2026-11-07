-- 0061_result_corrections_and_clinical_notes (owner request 2026-11-08; BUILD-SPEC.md 9.5, 9.6; CLAUDE.md rules 5, 6, 8, 10).
--
-- (1) CORRECTION FAN-OUT (spec 9.5). A result that supersedes another is either an AMENDMENT (a new measurement; the old value was true then; nothing happens) or a CORRECTION (the old value was wrong).
--     The writer declares which (`change_kind`); a path that cannot ask (the column is left empty) is inferred by code: same test code AND same effective date => correction, otherwise amendment (spec 9.5).
--     On a correction, an AFTER INSERT trigger raises a REVIEW TASK for a doctor for every care-plan step that cites the old result as its evidence ('met' / 'not_met': a decision already acted on) and for every
--     step whose open wait the old result claimed (a decision not yet acted on). The task names ids and a fixed word only (the old and new result, the plan, the step, the decision), never a value.
--     A DECLARED amendment cannot silence this: when the old result has the same test code and the same effective instant the correction review is raised anyway (a declaration may only add review).
--     RESIDUALS, named: (a) spec 9.5 limits the review to decisions acted on within the last 30 days; plan steps carry no decision time, so EVERY acted-on decision that cites the old result is reviewed
--     (more review, never less); (b) an in-flight graph run is not stopped: the task is what blocks a human from relying on it; (c) only care-plan steps consume results in this build, so only they fan out.
-- (2) CLINICAL NOTES (spec 9.6): append-only notes, a correction is an ADDENDUM (a new note naming the one it adds to, same patient). The body is stored ENCRYPTED (bound to the clinic and the note id), erased
--     with the patient by the audited erasure and re-encrypted by key rotation. Nurses and doctors of the clinic, with a second factor, write and read. NOT built: the structure-aware splitting and the
--     content-addressed embedding cache of 9.6 (they need an embedding model); patients do not see notes in the download of their data (a policy question named in the gap audit).

alter table clinical.results add column change_kind text check (change_kind in ('amendment', 'correction'));
alter table clinical.results add constraint results_kind_needs_supersession check (change_kind is null or supersedes_id is not null);
grant insert (change_kind) on clinical.results to careflow_app;

create function clinical.results_infer_kind() returns trigger
    language plpgsql set search_path = ''
as $$
declare
    v_old record;
begin
    if new.supersedes_id is null then
        if new.change_kind is not null then
            raise exception 'a change kind needs a result to supersede' using errcode = 'check_violation';
        end if;
        return new;
    end if;
    if new.change_kind is null then
        select r.code, r.valid_from into v_old from clinical.results r where r.id = new.supersedes_id and r.clinic_id = new.clinic_id;
        -- unknown is not a value and the safe direction is more review: an old result that cannot be read is inferred as a CORRECTION.
        -- CLINICAL-PLACEHOLDER: needs clinician sign-off: "same effective date" is implemented as the exact same instant (valid_from); whether it means the same clinic-local calendar day is a clinical call.
        new.change_kind := case when not found or (lower(v_old.code) = lower(new.code) and v_old.valid_from = new.valid_from) then 'correction' else 'amendment' end;
    end if;
    return new;
end
$$;
revoke all on function clinical.results_infer_kind() from public;
create trigger results_infer_kind before insert on clinical.results for each row execute function clinical.results_infer_kind();

create function clinical.results_fan_out() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    r record;
begin
    -- a DECLARED amendment cannot silence the safety net: when the old result has the same test code and the same effective date, it is reviewed as a correction anyway (a declaration may only add review)
    if new.supersedes_id is null then
        return new;
    end if;
    if new.change_kind is distinct from 'correction' and not exists (
        select 1 from clinical.results o where o.id = new.supersedes_id and o.clinic_id = new.clinic_id and lower(o.code) = lower(new.code) and o.valid_from = new.valid_from) then
        return new;
    end if;
    for r in
        select s.plan_id, s.step_key, s.status as step_status, 'decided'::text as phase
          from ops.care_plan_steps s join ops.care_plans p on p.id = s.plan_id and p.clinic_id = s.clinic_id
         where s.clinic_id = new.clinic_id and s.evidence_result_id = new.supersedes_id and s.status in ('met', 'not_met') and p.patient_id = new.patient_id
        union all
        select w.plan_id, w.step_key, s.status, 'pending'
          from ops.care_plan_waits w join ops.care_plan_steps s on s.plan_id = w.plan_id and s.step_key = w.step_key and s.clinic_id = w.clinic_id
         where w.clinic_id = new.clinic_id and w.claimed_result_id = new.supersedes_id and w.patient_id = new.patient_id and s.status in ('open', 'needs_doctor')
    loop
        insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key)
        values (new.clinic_id, new.origin, 'result_correction', 'standard', 'doctor', 'result:' || new.id::text,
                jsonb_build_object('old_result_id', new.supersedes_id::text, 'new_result_id', new.id::text, 'plan_id', r.plan_id::text, 'step_key', r.step_key, 'decision', case when r.phase = 'decided' then r.step_status else 'pending' end, 'declared', new.change_kind),
                'sys:result-correction:' || new.id::text || ':' || r.plan_id::text || ':' || r.step_key)
        on conflict (idempotency_key) do nothing;
    end loop;
    return new;
end
$$;
revoke all on function clinical.results_fan_out() from public;
create trigger results_fan_out after insert on clinical.results for each row execute function clinical.results_fan_out();
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function clinical.results_infer_kind(), clinical.results_fan_out() from %I', r);
        end if;
    end loop;
end $$;

-- ---- clinical notes ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
create table clinical.notes (
    id               uuid primary key,
    clinic_id        uuid not null references iam.clinics (id),
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    patient_id       uuid not null,
    author_id        uuid not null references iam.accounts (id),
    parent_id        uuid,
    body_ct          bytea check (body_ct is null or octet_length(body_ct) between 29 and 40100),
    body_key_version smallint check (body_key_version is null or body_key_version > 0),
    erased_at        timestamptz,
    created_at       timestamptz not null default clock_timestamp(),
    idempotency_key  text not null check (length(idempotency_key) between 1 and 300),
    constraint notes_key_per_clinic unique (clinic_id, idempotency_key),
    constraint notes_id_clinic_patient unique (id, clinic_id, patient_id),
    constraint notes_text_or_erased check ((erased_at is null and body_ct is not null and body_key_version is not null) or (erased_at is not null and body_ct is null and body_key_version is null)),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    foreign key (parent_id, clinic_id, patient_id) references clinical.notes (id, clinic_id, patient_id)  -- an addendum names a note of the SAME patient
);
create index notes_patient_idx on clinical.notes (clinic_id, patient_id, created_at);

create function clinical.notes_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'clinical.notes is append-only: % is not permitted (a correction is an addendum)', tg_op using errcode = 'restrict_violation';
    end if;
    if old.erased_at is null then
        if new.erased_at is not null and new.body_ct is null and new.body_key_version is null
           and (to_jsonb(new) - 'body_ct' - 'body_key_version' - 'erased_at') = (to_jsonb(old) - 'body_ct' - 'body_key_version' - 'erased_at') then
            return new;
        end if;
        if new.erased_at is null and new.body_ct is not null and new.body_key_version is distinct from old.body_key_version and new.body_ct is distinct from old.body_ct
           and (to_jsonb(new) - 'body_ct' - 'body_key_version') = (to_jsonb(old) - 'body_ct' - 'body_key_version') then
            return new;
        end if;
    end if;
    raise exception 'a note can only be erased or have its text re-encrypted' using errcode = 'restrict_violation';
end
$$;
revoke all on function clinical.notes_guard() from public;
create trigger notes_guard before update or delete on clinical.notes for each row execute function clinical.notes_guard();
create trigger notes_no_truncate before truncate on clinical.notes for each statement execute function clinical.notes_guard();
create trigger notes_not_erased before insert on clinical.notes for each row execute function ops.care_barriers_not_erased();

create table clinical.note_rotation_batches (
    id               uuid primary key default gen_random_uuid(),
    clinic_id        uuid not null references iam.clinics (id),
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    from_version     smallint not null check (from_version > 0),
    to_version       smallint not null check (to_version > 0 and to_version <> from_version),
    texts_rotated    integer not null check (texts_rotated > 0),
    first_id         uuid not null,
    last_id          uuid not null,
    created_at       timestamptz not null default clock_timestamp(),
    idempotency_key  text not null unique check (length(idempotency_key) > 0)
);

alter table clinical.notes enable row level security;
alter table clinical.notes force row level security;
alter table clinical.note_rotation_batches enable row level security;
alter table clinical.note_rotation_batches force row level security;
revoke all on clinical.notes, clinical.note_rotation_batches from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on clinical.notes, clinical.note_rotation_batches from %I', r);
            execute format('revoke all on function clinical.notes_guard() from %I', r);
        end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'careflow_app') then
        revoke all on clinical.note_rotation_batches from careflow_app;
    end if;
end $$;
grant select on clinical.notes to careflow_app;
grant insert (id, clinic_id, origin, patient_id, author_id, parent_id, body_ct, body_key_version, idempotency_key) on clinical.notes to careflow_app;
create policy notes_select on clinical.notes for select to careflow_app using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy notes_insert on clinical.notes for insert to careflow_app with check (author_id = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy notes_aal2_select on clinical.notes as restrictive for select to careflow_app using (iam.aal2_session());
create policy notes_aal2_insert on clinical.notes as restrictive for insert to careflow_app with check (iam.aal2_session());

create or replace function mpi.approve_patient_erasure(p_request_id uuid, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor   uuid := auth.uid();
    v_req     record;
    v_plans   integer;
    v_results integer;
    v_messages integer;
    v_voice   integer;
    v_griev   integer;
    v_notes   integer;
begin
    if v_actor is null or p_request_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    select * into v_req from mpi.erasure_requests where id = p_request_id for update;
    if not found or not iam.has_clinic_role(v_req.clinic_id, array['front_desk']) then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    if v_req.status = 'erased' then
        return false;  -- a replay: already carried out
    end if;
    -- two DIFFERENT people (BUILD-SPEC 12.5): the requester cannot approve their own request
    if v_req.status <> 'requested' or v_req.requested_by = v_actor then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    perform 1 from mpi.patients where id = v_req.patient_id for update;  -- the same row lock the merge engine takes on both patients, so a merge cannot be proposed between the check and the erase
    if exists (select 1 from mpi.merges m
               where m.status in ('awaiting_second_approval', 'active') and v_req.patient_id in (m.master_patient_id, m.subordinate_patient_id)) then
        raise exception 'a patient in a live merge link cannot be erased' using errcode = 'check_violation';
    end if;
    update mpi.patients
       set name_ct = null, name_key_version = null, dob_ct = null, dob_key_version = null, phone_ct = null, phone_key_version = null, erased_at = clock_timestamp()
     where id = v_req.patient_id and erased_at is null;
    -- the care record of the same patient (0056): plan text and step labels, then results' measures. Counts, not content, go to the audit row.
    -- the plans first (erased AND closed), then their waits (cancelled) and steps (label erased, open ones closed), so the guards see an erased plan
    update ops.care_plans set plan_ct = null, plan_key_version = null, erased_at = clock_timestamp(), status = 'erased'
     where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id and erased_at is null;
    get diagnostics v_plans = row_count;
    update ops.care_plan_waits w set open = false
      from ops.care_plans p where w.plan_id = p.id and w.clinic_id = p.clinic_id and p.patient_id = v_req.patient_id and p.clinic_id = v_req.clinic_id and w.open;
    update ops.care_plan_steps s set label = 'erased', status = case when s.status in ('open', 'needs_doctor') then 'closed' else s.status end
      from ops.care_plans p where s.plan_id = p.id and s.clinic_id = p.clinic_id and p.patient_id = v_req.patient_id and p.clinic_id = v_req.clinic_id and (s.label <> 'erased' or s.status in ('open', 'needs_doctor'));
    update clinical.results set measures = '{}'::jsonb, erased_at = clock_timestamp()
     where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id and erased_at is null;
    get diagnostics v_results = row_count;
    -- the messages of the patient's threads (the bodies; ids, topics, times stay) and the stored voice transcript chunks of the bound account (deleted)
    update msg.messages m set body_ct = null, body_key_version = null, erased_at = clock_timestamp()
      from msg.threads t where m.thread_id = t.id and m.clinic_id = t.clinic_id and t.patient_id = v_req.patient_id and t.clinic_id = v_req.clinic_id and m.erased_at is null;
    get diagnostics v_messages = row_count;
    update msg.voice_sessions vs set status = 'ended'
     where vs.status = 'open' and vs.clinic_id = v_req.clinic_id
       and exists (select 1 from mpi.patients pt where pt.id = v_req.patient_id and pt.account_id = vs.account_id and pt.clinic_id = vs.clinic_id);
    delete from msg.voice_transcript_chunks c
     where c.clinic_id = v_req.clinic_id
       and exists (select 1 from msg.voice_sessions s join mpi.patients pt on pt.account_id = s.account_id and pt.clinic_id = s.clinic_id
                    where s.id = c.session_id and s.clinic_id = c.clinic_id and pt.id = v_req.patient_id);
    get diagnostics v_voice = row_count;
    -- the grievance texts of the account bound to the patient (0059): the text goes, the category, status trail and times stay
    update iam.grievances g set body_ct = null, body_key_version = null, erased_at = clock_timestamp()
     where g.clinic_id = v_req.clinic_id and g.erased_at is null
       and exists (select 1 from mpi.patients pt where pt.id = v_req.patient_id and pt.account_id = g.account_id and pt.clinic_id = g.clinic_id);
    get diagnostics v_griev = row_count;
    -- the clinical notes of the patient (0061): the bodies go, the chain of ids, authors and times stay
    update clinical.notes n set body_ct = null, body_key_version = null, erased_at = clock_timestamp()
     where n.patient_id = v_req.patient_id and n.clinic_id = v_req.clinic_id and n.erased_at is null;
    get diagnostics v_notes = row_count;
    update mpi.erasure_requests set status = 'erased', approved_by = v_actor, decided_at = clock_timestamp() where id = p_request_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'patient.erase', 'patient', v_req.patient_id::text,
            jsonb_build_object('reason', v_req.reason, 'role_scope', iam.role_scope()), 'sys:erase:' || p_request_id::text);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'care_record.erased', 'patient', v_req.patient_id::text,
            jsonb_build_object('care_plans', v_plans, 'results', v_results, 'messages', v_messages, 'voice_chunks', v_voice, 'grievances', v_griev, 'notes', v_notes), 'sys:erase-care-record:' || p_request_id::text);
    return true;
end
$$;

do $$
declare r text; f text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where current_user = rolname) then
        raise exception '0061: the migrating role must bypass row-level security';
    end if;
    if has_any_column_privilege('careflow_app', 'clinical.notes', 'update') or has_table_privilege('careflow_app', 'clinical.notes', 'delete') or has_table_privilege('careflow_app', 'clinical.note_rotation_batches', 'select')
       or has_column_privilege('careflow_app', 'clinical.notes', 'erased_at', 'insert') or has_column_privilege('careflow_app', 'clinical.results', 'change_kind', 'update') then
        raise exception '0061: the application role may insert notes (not erase or change them) and nothing else here';
    end if;
    foreach f in array array['mpi.approve_patient_erasure(uuid,text)'] loop
        if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0061: % must be executable by the application login and not by PUBLIC', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0061: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
    foreach f in array array['clinical.notes_guard()', 'clinical.results_infer_kind()', 'clinical.results_fan_out()'] loop
        if has_function_privilege('public', f, 'execute') then
            raise exception '0061: % must not be executable by PUBLIC', f;
        end if;
    end loop;
end $$;
