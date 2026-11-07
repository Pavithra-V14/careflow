-- 0059_patient_rights_grievances_and_export (owner request 2026-11-08; BUILD-SPEC.md 19.3, 20.1, 17.5; CLAUDE.md rules 5, 6, 8, 10).
--
-- Spec 20.1: access, correction, erasure and nominee requests go through the grievance channel with tracking; 19.3: "download my data". Consent withdrawal already exists (0049: a withdrawn event against the
-- current notice). This migration adds:
-- (1) GRIEVANCES. A patient account that is bound to a record of a clinic files a grievance through iam.file_grievance: a CATEGORY (a closed word; the allowed list is a required setting of the application)
--     and a TEXT that is stored ENCRYPTED (bound to the clinic and the grievance id, key version beside it), never in a log or an audit row. An admin of the clinic (second factor required) acknowledges and
--     resolves it through iam.update_grievance; the status trail is append-only (iam.grievance_updates). The patient reads their own grievances and trail; admins read their clinic's. The text is erased
--     with the patient by the existing audited erasure (below) and rotates with the other ciphertexts (careflow/rotation.py).
-- (2) EXPORT. iam.export_my_messages and iam.export_my_bookings return the caller's OWN messages (ciphertext; the application decrypts) and bookings, filtered by the account's stage (spec 17.5: appointments
--     and safety need an active, bound account; care needs an account the clinic has activated for full access, which has no step in this build, so care messages are never exported yet). Each call is an audit row.
-- No application role can insert into or update any table here directly.

create table iam.grievances (
    id               uuid primary key default gen_random_uuid(),
    clinic_id        uuid not null references iam.clinics (id),
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    account_id       uuid not null references iam.accounts (id),
    category         text not null check (category ~ '^[a-z][a-z0-9_]{0,39}$'),
    body_ct          bytea check (body_ct is null or octet_length(body_ct) between 29 and 20100),
    body_key_version smallint check (body_key_version is null or body_key_version > 0),
    erased_at        timestamptz,
    created_at       timestamptz not null default clock_timestamp(),
    idempotency_key  text not null check (length(idempotency_key) between 1 and 300),
    constraint grievances_key_per_account unique (account_id, idempotency_key),
    constraint grievances_id_clinic unique (id, clinic_id),
    constraint grievances_text_or_erased check ((erased_at is null and body_ct is not null and body_key_version is not null) or (erased_at is not null and body_ct is null and body_key_version is null))
);

create table iam.grievance_updates (
    id              uuid primary key default gen_random_uuid(),
    grievance_id    uuid not null,
    clinic_id       uuid not null,
    status          text not null check (status in ('acknowledged', 'resolved')),
    actor_id        uuid not null references iam.accounts (id),
    created_at      timestamptz not null default clock_timestamp(),
    constraint grievance_updates_once unique (grievance_id, status),
    foreign key (grievance_id, clinic_id) references iam.grievances (id, clinic_id)
);

create function iam.grievances_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception '% rows are never deleted', tg_table_name using errcode = 'restrict_violation';
    end if;
    if tg_table_name = 'grievance_updates' then
        raise exception 'the grievance trail is append-only' using errcode = 'restrict_violation';
    end if;
    -- iam.grievances: the text may be erased (text and key version null, erased_at set) or rotated (text and key version change, the version MUST change); nothing else changes
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
    raise exception 'a grievance can only be erased or have its text re-encrypted' using errcode = 'restrict_violation';
end
$$;
revoke all on function iam.grievances_guard() from public;
create trigger grievances_guard before update or delete on iam.grievances for each row execute function iam.grievances_guard();
create trigger grievances_no_truncate before truncate on iam.grievances for each statement execute function iam.grievances_guard();
create trigger grievance_updates_guard before update or delete on iam.grievance_updates for each row execute function iam.grievances_guard();
create trigger grievance_updates_no_truncate before truncate on iam.grievance_updates for each statement execute function iam.grievances_guard();

alter table iam.grievances enable row level security;
alter table iam.grievances force row level security;
alter table iam.grievance_updates enable row level security;
alter table iam.grievance_updates force row level security;
revoke all on iam.grievances, iam.grievance_updates from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.grievances, iam.grievance_updates from %I', r);
            execute format('revoke all on function iam.grievances_guard() from %I', r);
        end if;
    end loop;
end $$;
grant select on iam.grievances, iam.grievance_updates to careflow_app;
create policy grievances_select on iam.grievances for select to careflow_app
    using (account_id = auth.uid() or (iam.has_clinic_role(clinic_id, array['admin']) and iam.aal2_session()));
create policy grievance_updates_select on iam.grievance_updates for select to careflow_app
    using ((iam.has_clinic_role(clinic_id, array['admin']) and iam.aal2_session())
           or exists (select 1 from iam.grievances g where g.id = grievance_updates.grievance_id and g.clinic_id = grievance_updates.clinic_id and g.account_id = auth.uid()));

create table iam.grievance_rotation_batches (
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
alter table iam.grievance_rotation_batches enable row level security;
alter table iam.grievance_rotation_batches force row level security;
revoke all on iam.grievance_rotation_batches from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.grievance_rotation_batches from %I', r);
        end if;
    end loop;
end $$;

-- ---- file (a bound patient account) ----------------------------------------------------------------------------------------------------------------------------------------------------------------
create function iam.file_grievance(p_clinic_id uuid, p_category text, p_body_ct bytea, p_key_version smallint, p_grievance_id uuid, p_idempotency_key text)
    returns table (grievance_id uuid, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_prior  record;
begin
    if v_actor is null or p_clinic_id is null or p_category is null or p_category !~ '^[a-z][a-z0-9_]{0,39}$' or p_body_ct is null or octet_length(p_body_ct) not between 29 and 20100
       or p_key_version is null or p_key_version < 1 or p_grievance_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 300
       or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'grievance refused' using errcode = '42501';
    end if;
    select g.id, g.clinic_id, g.category into v_prior from iam.grievances g where g.account_id = v_actor and g.idempotency_key = p_idempotency_key;
    if found then
        if v_prior.clinic_id <> p_clinic_id or v_prior.category <> p_category then
            raise exception 'grievance refused' using errcode = '42501';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    -- the account must be bound to a live record of THAT clinic (a registered account that has not claimed a record has no clinic to write to)
    select pt.origin into v_origin from mpi.patients pt where pt.account_id = v_actor and pt.clinic_id = p_clinic_id and pt.erased_at is null;
    if not found then
        raise exception 'grievance refused' using errcode = '42501';
    end if;
    insert into iam.grievances (id, clinic_id, origin, account_id, category, body_ct, body_key_version, idempotency_key)
    values (p_grievance_id, p_clinic_id, v_origin, v_actor, p_category, p_body_ct, p_key_version, p_idempotency_key) on conflict (account_id, idempotency_key) do nothing;
    if not found then
        raise exception 'grievance refused' using errcode = '42501';  -- a concurrent call with the same key won; the retry gets the replay
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'patient', 'grievance.file', 'grievance', p_grievance_id::text, jsonb_build_object('category', p_category), 'sys:grievance-file:' || p_grievance_id::text);
    return query select p_grievance_id, true;
end
$$;

-- ---- acknowledge / resolve (an admin of the clinic) ----------------------------------------------------------------------------------------------------------------------------------------------------
create function iam.update_grievance(p_grievance_id uuid, p_status text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_g     record;
    v_acked boolean;
begin
    if v_actor is null or p_grievance_id is null or p_status is null or p_status not in ('acknowledged', 'resolved') or not iam.aal2_session() then
        raise exception 'grievance update refused' using errcode = '42501';
    end if;
    select g.id, g.clinic_id, g.origin into v_g from iam.grievances g where g.id = p_grievance_id;
    if not found or not iam.has_clinic_role(v_g.clinic_id, array['admin']) then
        raise exception 'grievance update refused' using errcode = '42501';
    end if;
    if exists (select 1 from iam.grievance_updates u where u.grievance_id = p_grievance_id and u.status = p_status) then
        return 'replay';
    end if;
    v_acked := exists (select 1 from iam.grievance_updates u where u.grievance_id = p_grievance_id and u.status = 'acknowledged');
    if exists (select 1 from iam.grievance_updates u where u.grievance_id = p_grievance_id and u.status = 'resolved') or (p_status = 'acknowledged' and v_acked) then
        return 'invalid_transition';  -- a resolved grievance is final
    end if;
    insert into iam.grievance_updates (grievance_id, clinic_id, status, actor_id) values (p_grievance_id, v_g.clinic_id, p_status, v_actor);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_g.origin, v_g.clinic_id, v_actor, 'admin', 'grievance.' || p_status, 'grievance', p_grievance_id::text, '{}'::jsonb, 'sys:grievance-' || p_status || ':' || p_grievance_id::text);
    return 'recorded';
end
$$;

-- ---- export (the caller's own data) ------------------------------------------------------------------------------------------------------------------------------------------------------------------
create function iam.export_my_messages()
    returns table (clinic_id uuid, message_id uuid, thread_id uuid, topic text, sender_kind text, created_at timestamptz, erased_at timestamptz, body_ct bytea, body_key_version smallint)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_n     integer;
begin
    if v_actor is null or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'export refused' using errcode = '42501';
    end if;
    -- appointments and safety topics only: the care topic needs full access, which no step of this build grants (spec 17.5)
    select count(*) into v_n from (
        select m.id from msg.messages m join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
         where pt.account_id = v_actor and pt.erased_at is null and m.topic in ('appointments', 'safety') limit 2000) c;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    select pt.origin, pt.clinic_id, v_actor, 'patient', 'portal.export', 'account', v_actor::text, jsonb_build_object('part', 'messages', 'messages', v_n), 'sys:portal-export:' || gen_random_uuid()::text
      from mpi.patients pt where pt.account_id = v_actor limit 1;
    return query
    select m.clinic_id, m.id, m.thread_id, m.topic, m.sender_kind, m.created_at, m.erased_at, m.body_ct, m.body_key_version
      from msg.messages m join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
     where pt.account_id = v_actor and pt.erased_at is null and m.topic in ('appointments', 'safety') order by m.created_at, m.id limit 2000;
end
$$;

create function iam.export_my_bookings()
    returns table (clinic_id uuid, booking_id uuid, status text, created_at timestamptz, cancelled_at timestamptz, starts_at timestamptz, ends_at timestamptz)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
begin
    if v_actor is null or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'export refused' using errcode = '42501';
    end if;
    return query
    select b.clinic_id, b.id, b.status, b.created_at, b.cancelled_at, s.starts_at, s.ends_at
      from sched.bookings b join sched.slots s on s.id = b.slot_id and s.clinic_id = b.clinic_id join mpi.patients pt on pt.id = b.patient_id and pt.clinic_id = b.clinic_id
     where pt.account_id = v_actor and pt.erased_at is null order by s.starts_at, b.id limit 2000;
end
$$;

revoke all on function iam.file_grievance(uuid, text, bytea, smallint, uuid, text), iam.update_grievance(uuid, text), iam.export_my_messages(), iam.export_my_bookings() from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.file_grievance(uuid, text, bytea, smallint, uuid, text), iam.update_grievance(uuid, text), iam.export_my_messages(), iam.export_my_bookings() from %I', r);
        end if;
    end loop;
end $$;
grant execute on function iam.file_grievance(uuid, text, bytea, smallint, uuid, text), iam.update_grievance(uuid, text), iam.export_my_messages(), iam.export_my_bookings() to careflow_app;

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
    update mpi.erasure_requests set status = 'erased', approved_by = v_actor, decided_at = clock_timestamp() where id = p_request_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'patient.erase', 'patient', v_req.patient_id::text,
            jsonb_build_object('reason', v_req.reason, 'role_scope', iam.role_scope()), 'sys:erase:' || p_request_id::text);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'care_record.erased', 'patient', v_req.patient_id::text,
            jsonb_build_object('care_plans', v_plans, 'results', v_results, 'messages', v_messages, 'voice_chunks', v_voice, 'grievances', v_griev), 'sys:erase-care-record:' || p_request_id::text);
    return true;
end
$$;

do $$
declare r text; f text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where current_user = rolname) then
        raise exception '0059: the migrating role must bypass row-level security';
    end if;
    if has_any_column_privilege('careflow_app', 'iam.grievances', 'insert') or has_any_column_privilege('careflow_app', 'iam.grievances', 'update') or has_table_privilege('careflow_app', 'iam.grievances', 'delete')
       or has_any_column_privilege('careflow_app', 'iam.grievance_updates', 'insert') or has_any_column_privilege('careflow_app', 'iam.grievance_updates', 'update') or has_table_privilege('careflow_app', 'iam.grievance_updates', 'delete') then
        raise exception '0059: grievances are written only by their functions';
    end if;
    foreach f in array array['iam.file_grievance(uuid,text,bytea,smallint,uuid,text)', 'iam.update_grievance(uuid,text)', 'iam.export_my_messages()', 'iam.export_my_bookings()', 'mpi.approve_patient_erasure(uuid,text)'] loop
        if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0059: % must be executable by the application login and not by PUBLIC', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0059: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
    if has_function_privilege('public', 'iam.grievances_guard()', 'execute') then
        raise exception '0059: the guard function must not be executable by PUBLIC';
    end if;
end $$;
