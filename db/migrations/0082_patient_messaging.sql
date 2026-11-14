-- 0082_patient_messaging (owner decision 2026-11-14, frontend request 29; BUILD-SPEC.md 17.5, 5.2, 11.5; CLAUDE.md rules 1, 2, 3, 5, 8, 10).
--
-- A patient may open the thread for a topic and SEND a message, and read their own threads. MECHANISM ONLY: the route is off unless the owner turns it on, there is no model anywhere in this path, and no reply wording is written.
--   * Topics a patient may SEND on: `care` (only at stage `active`, spec 17.5) and `appointments` (any bound patient). `safety` is never patient-sent. The patient must be bound (mpi.patients.account_id) to a live record at the clinic.
--   * The patient has still NO table grant and NO policy on msg.*. Everything goes through definer functions below, each of which checks auth.uid() itself and gives ONE fixed refusal (42501) for every reason.
--   * The deterministic red-flag rules screen the message in the SAME transaction before it commits (the deferred trigger msg.require_screening from 0013 refuses to commit an unscreened patient message). The patient session cannot read
--     kb.red_flag_rules (0007), so it reads them through kb.patient_screening_rules, which returns the same rows the engine reads, only to a bound patient of that clinic. Without it every message would be `unassessable`.
--   * msg.record_screening (0013) authorised only staff roles. It is re-issued here with ONE added condition, msg.is_own_patient_message, through pg_get_functiondef and replace (the body is 200 lines; copying it would invite drift). The DO block
--     refuses to continue unless the replacement happened exactly once. A patient can screen only a message that is theirs, sender_kind 'patient', recorded by them.
--   * msg.guard_patient_live (0011) is re-issued with one added condition: the caller is the patient's own bound, active patient account. Staff behaviour is unchanged.
--
-- NEW GRANTS (ledger): EXECUTE on msg.patient_open_thread, msg.patient_post_message, msg.patient_threads, msg.patient_thread_messages, kb.patient_screening_rules to careflow_app. No table grant changes.
-- Residual risks (pre-launch checklist 87): (1) the same as 0013, the database cannot prove the rule engine ran on the text that was stored; and now the party who can call msg.record_screening for a patient message is the patient who wrote it
-- (only for a message written in the same transaction, and a `clear` still has to match the database's own rule count and hash, which the patient can read through kb.patient_screening_rules); (2) that function returns the clinic's rules of every status
-- (draft and retired too, as the engine reads them): clinician data, not patient data, but a determined patient could learn which phrases escalate.

-- ---- helper: is this message the caller's own patient message? ----------------------------------------------------------

create function msg.is_own_patient_message(p_message_id uuid) returns boolean
    language sql stable security definer set search_path = ''
as $$
    select exists (
        select 1
          from msg.messages m
          join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id
          join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
          join iam.accounts a on a.id = auth.uid() and a.account_type = 'patient' and a.status = 'active'
         where m.id = p_message_id and m.sender_kind = 'patient' and m.recorded_by = auth.uid() and m.created_at >= transaction_timestamp()  -- only a message written in THIS transaction
           and pt.account_id = auth.uid() and pt.erased_at is null
    )
$$;

-- ---- msg.guard_patient_live: also the patient's own bound account --------------------------------------------------------

create or replace function msg.guard_patient_live(p_patient uuid, p_clinic uuid, p_origin text, p_table text) returns void
    language plpgsql security definer set search_path = ''
as $$
declare
    v_erased timestamptz;
    v_found boolean;
begin
    if not (
        iam.has_clinic_role(p_clinic, array['front_desk', 'nurse', 'doctor'])
        or exists (select 1 from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
                    where pt.id = p_patient and pt.clinic_id = p_clinic and pt.account_id = auth.uid())
    ) then
        raise exception using errcode = '42501',
            message = format('new row violates row-level security policy for table "%s"', p_table);
    end if;
    select true, p.erased_at into v_found, v_erased
      from mpi.patients p
     where p.id = p_patient and p.clinic_id = p_clinic and p.origin = p_origin
       for share;
    if v_found is not true or v_erased is not null then
        raise exception using errcode = '42501',
            message = format('new row violates row-level security policy for table "%s"', p_table);
    end if;
end
$$;

-- ---- msg.record_screening: one added condition -----------------------------------------------------------------------------

do $$
declare
    v_def text;
    v_new text;
    v_old constant text := 'iam.has_clinic_role(v_msg.clinic_id, array[''nurse'', ''doctor'', ''queue_worker''])';
begin
    v_def := pg_get_functiondef('msg.record_screening(uuid, text, text, integer, text, jsonb, text)'::regprocedure);
    if (length(v_def) - length(replace(v_def, v_old, ''))) <> length(v_old) then
        raise exception '0082: the authorisation line of msg.record_screening was not found exactly once';
    end if;
    v_new := replace(v_def, v_old, '(' || v_old || ' or msg.is_own_patient_message(p_message_id))');
    execute v_new;
end $$;

-- ---- kb.patient_screening_rules: the rules the engine reads, for a bound patient of that clinic only -----------------------

create function kb.patient_screening_rules(p_clinic_id uuid, p_origin text)
    returns table (id uuid, clinic_id uuid, rule_code text, version_id integer, status text, phrases text[], patterns text[], token_sets jsonb)
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or p_origin is null
       or not exists (select 1 from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
                       where pt.account_id = auth.uid() and pt.clinic_id = p_clinic_id and pt.origin = p_origin and pt.erased_at is null) then
        raise exception 'screening rules refused' using errcode = '42501';
    end if;
    return query select r.id, r.clinic_id, r.rule_code, r.version_id, r.status, r.phrases, r.patterns, r.token_sets
                   from kb.red_flag_rules r where (r.clinic_id = p_clinic_id or r.clinic_id is null) and r.origin = p_origin;
end
$$;

-- ---- msg.patient_open_thread: get or create the caller's thread for a topic --------------------------------------------------

create function msg.patient_open_thread(p_clinic_id uuid, p_topic text, p_new_thread_id uuid, p_idempotency_key text)
    returns table (thread_id uuid, clinic_id uuid, origin text, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor   uuid := auth.uid();
    v_patient uuid;
    v_origin  text;
    v_id      uuid;
begin
    if v_actor is null or p_clinic_id is null or p_new_thread_id is null or p_topic is null or p_topic not in ('care', 'appointments')
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'thread refused' using errcode = '42501';
    end if;
    select pt.id, pt.origin into v_patient, v_origin
      from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
     where pt.account_id = v_actor and pt.clinic_id = p_clinic_id and pt.erased_at is null;
    if v_patient is null or (p_topic = 'care' and iam.patient_stage() <> 'active') then
        raise exception 'thread refused' using errcode = '42501';
    end if;
    select t.id into v_id from msg.threads t where t.patient_id = v_patient and t.topic = p_topic;
    if v_id is not null then
        return query select v_id, p_clinic_id, v_origin, false;
        return;
    end if;
    insert into msg.threads (id, clinic_id, patient_id, topic, created_by, origin, idempotency_key)
    values (p_new_thread_id, p_clinic_id, v_patient, p_topic, v_actor, v_origin, 'patient-thread:' || p_new_thread_id::text)
    on conflict do nothing
    returning id into v_id;
    if v_id is null then
        select t.id into v_id from msg.threads t where t.patient_id = v_patient and t.topic = p_topic;
        return query select v_id, p_clinic_id, v_origin, false;
        return;
    end if;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'msg.patient_thread.opened', 'msg.threads', v_id::text, jsonb_build_object('topic', p_topic),
            'sys:patient-thread:' || gen_random_uuid()::text);
    return query select v_id, p_clinic_id, v_origin, true;
end
$$;

-- ---- msg.patient_post_message: the ciphertext is bound to the thread and message ids, so the thread is opened first --------------

create function msg.patient_post_message(p_thread_id uuid, p_message_id uuid, p_body_ct bytea, p_key_version smallint, p_idempotency_key text)
    returns table (message_id uuid, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_t      record;
    v_key    text;
    v_prior  uuid;
begin
    if v_actor is null or p_thread_id is null or p_message_id is null or p_body_ct is null or p_key_version is null or p_key_version <= 0
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'message refused' using errcode = '42501';
    end if;
    select t.id, t.clinic_id, t.origin, t.topic, t.patient_id into v_t
      from msg.threads t join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
      join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
     where t.id = p_thread_id and pt.account_id = v_actor and pt.erased_at is null and t.topic in ('care', 'appointments');
    if not found or (v_t.topic = 'care' and iam.patient_stage() <> 'active') then
        raise exception 'message refused' using errcode = '42501';
    end if;
    v_key := 'patient-msg:' || v_actor::text || ':' || p_idempotency_key;
    select m.id into v_prior from msg.messages m where m.idempotency_key = v_key and m.recorded_by = v_actor;
    if v_prior is not null then
        return query select v_prior, false;
        return;
    end if;
    begin
        insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key)
        values (p_message_id, v_t.clinic_id, v_t.origin, v_t.topic, p_thread_id, 'patient', v_actor, p_body_ct, p_key_version, v_key);
    exception when unique_violation then
        -- a concurrent identical post answers as a replay; any other collision (a key or id that is not this caller's) is the one fixed refusal
        select m.id into v_prior from msg.messages m where m.idempotency_key = v_key and m.recorded_by = v_actor;
        if v_prior is null then
            raise exception 'message refused' using errcode = '42501';
        end if;
        return query select v_prior, false;
        return;
    end;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (v_t.origin, v_t.clinic_id, v_actor, 'msg.patient_message.sent', 'msg.messages', p_message_id::text, jsonb_build_object('topic', v_t.topic),
            'sys:patient-msg:' || gen_random_uuid()::text);
    return query select p_message_id, true;
end
$$;

-- ---- reads: the caller's own threads and the messages of one of them -----------------------------------------------------------
-- Care threads only at stage `active`. A thread that is not the caller's, does not exist or is hidden by stage gives the SAME empty result.

create function msg.patient_threads()
    returns table (thread_id uuid, clinic_id uuid, topic text, message_count bigint, last_message_at timestamptz)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_stage text;
begin
    if v_actor is null then
        raise exception 'threads refused' using errcode = '42501';
    end if;
    v_stage := iam.patient_stage();
    return query
        select t.id, t.clinic_id, t.topic,
               (select count(*) from msg.messages m where m.thread_id = t.id),
               (select max(m.created_at) from msg.messages m where m.thread_id = t.id)
          from msg.threads t join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
           join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
         where pt.account_id = v_actor and pt.erased_at is null and (t.topic <> 'care' or v_stage = 'active')
         order by t.created_at;
end
$$;

create function msg.patient_thread_messages(p_thread_id uuid)
    returns table (message_id uuid, clinic_id uuid, thread_id uuid, topic text, sender_kind text, created_at timestamptz, body_ct bytea, body_key_version smallint, erased boolean)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_stage text;
begin
    if v_actor is null or p_thread_id is null then
        raise exception 'messages refused' using errcode = '42501';
    end if;
    v_stage := iam.patient_stage();
    return query
        select m.id, m.clinic_id, m.thread_id, m.topic, m.sender_kind, m.created_at, m.body_ct, m.body_key_version, (m.erased_at is not null)
          from msg.messages m join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id
          join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
          join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
         where m.thread_id = p_thread_id and pt.account_id = v_actor and pt.erased_at is null and (t.topic <> 'care' or v_stage = 'active')
         order by m.created_at;
end
$$;

-- ---- privileges --------------------------------------------------------------------------------------------------------------

revoke all on function msg.is_own_patient_message(uuid), kb.patient_screening_rules(uuid, text), msg.patient_open_thread(uuid, text, uuid, text),
    msg.patient_post_message(uuid, uuid, bytea, smallint, text), msg.patient_threads(), msg.patient_thread_messages(uuid) from public;
grant execute on function kb.patient_screening_rules(uuid, text), msg.patient_open_thread(uuid, text, uuid, text),
    msg.patient_post_message(uuid, uuid, bytea, smallint, text), msg.patient_threads(), msg.patient_thread_messages(uuid) to careflow_app;
-- msg.is_own_patient_message is called only by the re-issued record_screening (a definer, so it needs no grant to the application role).

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function msg.is_own_patient_message(uuid), kb.patient_screening_rules(uuid, text), msg.patient_open_thread(uuid, text, uuid, text), msg.patient_post_message(uuid, uuid, bytea, smallint, text), msg.patient_threads(), msg.patient_thread_messages(uuid) from %I', r);
        end if;
    end loop;
    if not exists (select 1 from pg_proc where oid = 'msg.record_screening(uuid, text, text, integer, text, jsonb, text)'::regprocedure and prosrc like '%msg.is_own_patient_message%' and prosecdef) then
        raise exception '0082: record_screening was not re-issued as a definer with the added condition';
    end if;
end $$;
