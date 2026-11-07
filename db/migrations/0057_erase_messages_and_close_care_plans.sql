-- 0057_erase_messages_and_close_care_plans (owner decisions of 2026-11-08; BUILD-SPEC.md 9.4, 17.2, 20.1; CLAUDE.md rules 5, 6, 8, 10). Fix forward from 0056: no merged migration is edited.
--
-- (1) The audited two-person erasure (mpi.approve_patient_erasure) now also erases the patient's MESSAGES (bodies and key versions become null, erased_at is set; ids, topics, times stay) and DELETES the
--     stored voice transcript chunks of the account bound to the patient, in the same transaction. The message guard already allowed the erasure transition; the chunk guard gets ONE new delete case (the
--     chunk's session belongs to the account bound to an erased patient). The count of each goes into the `care_record.erased` audit row (counts only; its details gain `messages` and `voice_chunks`).
-- (2) Erased care plans are CLOSED: the plan gets the status `erased` (together with erased_at, enforced by a check), its open waits are cancelled (open = false), its open steps are closed. The staff list
--     and the one-plan route hide them. The guards allow exactly these transitions; a plan erased under 0056 is closed by a one-time backfill at the end of this migration, which also erases the messages and
--     chunks of patients erased before 0056/0057.
-- Kept (owner decision): result code, verified flag and times. No new grant to any application role; the application login still cannot update any of these columns.

do $$
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0057: the migrating role must bypass row-level security (the erasure function and the backfill rely on it)';
    end if;
end $$;

alter table ops.care_plans drop constraint if exists care_plans_status_check;
alter table ops.care_plans add constraint care_plans_status_check check (status in ('screening', 'awaiting_confirmation', 'active', 'completed', 'rejected', 'held', 'erased'));

create or replace function ops.care_plans_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plans rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.erased_at is not null then
        if old.status <> 'erased' and new.status = 'erased' and (to_jsonb(new) - 'status' - 'updated_at') = (to_jsonb(old) - 'status' - 'updated_at') then
            new.updated_at := clock_timestamp();
            return new;  -- a plan erased under 0056 is closed now (one time; every other change to an erased plan is still refused)
        end if;
        raise exception 'an erased care plan is final' using errcode = 'restrict_violation';
    end if;
    -- erasure: the text and key version become null, the plan is marked erased AND closed (status `erased`); nothing else changes
    if new.erased_at is not null and new.plan_ct is null and new.plan_key_version is null and new.status = 'erased'
       and (to_jsonb(new) - 'plan_ct' - 'plan_key_version' - 'erased_at' - 'status' - 'updated_at') = (to_jsonb(old) - 'plan_ct' - 'plan_key_version' - 'erased_at' - 'status' - 'updated_at') then
        new.updated_at := clock_timestamp();
        return new;
    end if;
    -- key rotation: the text and its key version change (the version MUST change), nothing else
    if new.erased_at is null and new.plan_ct is not null and new.plan_key_version is distinct from old.plan_key_version and new.plan_ct is distinct from old.plan_ct
       and (to_jsonb(new) - 'plan_ct' - 'plan_key_version' - 'updated_at') = (to_jsonb(old) - 'plan_ct' - 'plan_key_version' - 'updated_at') then
        new.updated_at := clock_timestamp();
        return new;
    end if;
    if old.status in ('completed', 'rejected') then
        raise exception 'a finished care plan is final' using errcode = 'restrict_violation';
    end if;
    if new.id <> old.id or new.clinic_id <> old.clinic_id or new.origin <> old.origin or new.patient_id <> old.patient_id or new.plan_ct is distinct from old.plan_ct
       or new.plan_key_version is distinct from old.plan_key_version or new.erased_at is distinct from old.erased_at or new.created_by <> old.created_by or new.created_at <> old.created_at
       or new.idempotency_key <> old.idempotency_key then
        raise exception 'only the status of a care plan can change' using errcode = 'restrict_violation';
    end if;
    if new.status <> old.status then
        if not ((old.status, new.status) in (('screening', 'awaiting_confirmation'), ('screening', 'held'), ('screening', 'rejected'), ('awaiting_confirmation', 'active'), ('awaiting_confirmation', 'rejected'),
                                             ('awaiting_confirmation', 'held'), ('held', 'awaiting_confirmation'), ('held', 'active'), ('held', 'rejected'), ('active', 'completed'), ('active', 'held'))) then
            raise exception 'that care plan status change is not allowed' using errcode = 'restrict_violation';
        end if;
        if new.status in ('active', 'rejected') and not iam.has_clinic_role(new.clinic_id, array['doctor']) then
            raise exception 'only a doctor can confirm or reject a care plan' using errcode = 'insufficient_privilege';
        end if;
        if new.status = 'active' and not exists (select 1 from ops.care_plan_steps s where s.plan_id = new.id and s.clinic_id = new.clinic_id) then
            raise exception 'a care plan with no steps cannot be activated' using errcode = 'restrict_violation';
        end if;
        if new.status = 'completed' and exists (select 1 from ops.care_plan_steps s where s.plan_id = new.id and s.clinic_id = new.clinic_id and s.kind = 'wait_result' and s.status in ('open', 'needs_doctor')) then
            raise exception 'a care plan cannot be completed while a result it waits for is still open' using errcode = 'restrict_violation';
        end if;
    end if;
    new.updated_at := clock_timestamp();
    return new;
end
$$;

create or replace function ops.care_plan_steps_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plan_steps rows are never deleted' using errcode = 'restrict_violation';
    end if;
    -- erasure of a patient's care record: the label may become the word `erased`; and, once the plan itself is erased, an open step closes (nothing else changes)
    if (to_jsonb(new) - 'label' - 'status') = (to_jsonb(old) - 'label' - 'status')
       and (new.label = old.label or new.label = 'erased')
       and (new.status = old.status or (new.status = 'closed' and old.status in ('open', 'needs_doctor')
                                        and exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.erased_at is not null)))
       and (new.label is distinct from old.label or new.status is distinct from old.status) then
        return new;
    end if;
    if old.status <> 'open' and old.status <> 'needs_doctor' then
        raise exception 'a closed care plan step is final' using errcode = 'restrict_violation';
    end if;
    if (to_jsonb(new) - 'status' - 'evidence_result_id') <> (to_jsonb(old) - 'status' - 'evidence_result_id') then
        raise exception 'only the status and the evidence of a step can change' using errcode = 'restrict_violation';
    end if;
    if exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status in ('completed', 'rejected', 'erased')) then
        raise exception 'the care plan is finished' using errcode = 'restrict_violation';
    end if;
    if new.status = 'closed' and old.kind = 'wait_result' then
        raise exception 'a step that waits for a result closes only as met or not_met, on a result' using errcode = 'restrict_violation';
    end if;
    if old.status = 'needs_doctor' and new.status <> old.status and not iam.has_clinic_role(new.clinic_id, array['doctor']) then
        raise exception 'only a doctor releases a step held for a doctor' using errcode = 'insufficient_privilege';
    end if;
    if new.status in ('met', 'not_met') then
        if new.kind <> 'wait_result' or not exists (
            select 1 from clinical.results r join ops.care_plans p on p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status = 'active'
             where r.id = new.evidence_result_id and r.clinic_id = new.clinic_id and r.patient_id = p.patient_id and r.verified
               and left(lower(r.code), length(new.result_prefix)) = lower(new.result_prefix)
               and not exists (select 1 from clinical.results s where s.supersedes_id = r.id)
        ) then
            raise exception 'a step can close only on a verified result of this patient that matches the step, in an active plan' using errcode = 'restrict_violation';
        end if;
    end if;
    return new;
end
$$;

create or replace function ops.care_plan_waits_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plan_waits rows are never deleted' using errcode = 'restrict_violation';
    end if;
    -- a wait of an erased plan is closed (nothing else changes, and it never opens again)
    if old.open and not new.open and new.claimed_result_id is not distinct from old.claimed_result_id and (to_jsonb(new) - 'open') = (to_jsonb(old) - 'open')
       and exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.erased_at is not null) then
        return new;
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
    if exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status in ('completed', 'rejected', 'erased')) then
        raise exception 'the care plan is finished' using errcode = 'restrict_violation';
    end if;
    if new.claimed_result_id is not null and old.claimed_result_id is null then
        if old.open is not true or not exists (
            select 1 from clinical.results r where r.id = new.claimed_result_id and r.clinic_id = new.clinic_id and r.patient_id = new.patient_id
               and left(lower(r.code), length(new.prefix)) = lower(new.prefix)
        ) then
            raise exception 'an open wait can be claimed only by a result of this patient that matches its prefix' using errcode = 'restrict_violation';
        end if;
    end if;
    return new;
end
$$;

create or replace function msg.voice_chunks_guard() returns trigger
    language plpgsql set search_path = ''
as $$
declare
    v_days integer;
begin
    if tg_op = 'INSERT' then
        select transcript_retention_days into v_days from msg.voice_sessions where id = new.session_id and clinic_id = new.clinic_id;
        if v_days is null then
            raise exception 'no such voice session' using errcode = 'restrict_violation';
        end if;
        if exists (select 1 from msg.voice_sessions s join mpi.patients pt on pt.account_id = s.account_id and pt.clinic_id = s.clinic_id
                    where s.id = new.session_id and s.clinic_id = new.clinic_id and pt.erased_at is not null) then
            raise exception 'the patient of this voice session is erased' using errcode = 'restrict_violation';  -- nothing is written after an erasure
        end if;
        new.created_at := clock_timestamp();
        new.expires_at := clock_timestamp() + make_interval(days => v_days);  -- the expiry is the database's, from the session's retention; the caller cannot choose it
        return new;
    end if;
    if tg_op = 'DELETE' then
        if old.expires_at <= clock_timestamp() then
            return old;  -- an EXPIRED chunk may go (the audited purge package)
        end if;
        if exists (select 1 from msg.voice_sessions s join mpi.patients pt on pt.account_id = s.account_id and pt.clinic_id = s.clinic_id
                    where s.id = old.session_id and s.clinic_id = old.clinic_id and pt.erased_at is not null) then
            return old;  -- the patient bound to the session's account is erased (0057): the transcript goes with them
        end if;
    end if;
    raise exception 'msg.voice_transcript_chunks is append-only until a chunk has expired' using errcode = 'restrict_violation';
end
$$;

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
    update mpi.erasure_requests set status = 'erased', approved_by = v_actor, decided_at = clock_timestamp() where id = p_request_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'patient.erase', 'patient', v_req.patient_id::text,
            jsonb_build_object('reason', v_req.reason, 'role_scope', iam.role_scope()), 'sys:erase:' || p_request_id::text);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'care_record.erased', 'patient', v_req.patient_id::text,
            jsonb_build_object('care_plans', v_plans, 'results', v_results, 'messages', v_messages, 'voice_chunks', v_voice), 'sys:erase-care-record:' || p_request_id::text);
    return true;
end
$$;

-- ---- one-time backfill: patients erased BEFORE this migration ------------------------------------------------------------------------------------------------------------------------------------------------
update ops.care_plans set status = 'erased' where erased_at is not null and status <> 'erased';
update ops.care_plan_waits w set open = false from ops.care_plans p where w.plan_id = p.id and w.clinic_id = p.clinic_id and p.erased_at is not null and w.open;
update ops.care_plan_steps s set status = 'closed' from ops.care_plans p where s.plan_id = p.id and s.clinic_id = p.clinic_id and p.erased_at is not null and s.status in ('open', 'needs_doctor');
update ops.care_plans p set plan_ct = null, plan_key_version = null, erased_at = clock_timestamp(), status = 'erased'
    where p.erased_at is null and p.patient_id in (select id from mpi.patients where erased_at is not null);
update ops.care_plan_waits w set open = false from ops.care_plans p where w.plan_id = p.id and w.clinic_id = p.clinic_id and p.erased_at is not null and w.open;
update ops.care_plan_steps s set label = 'erased', status = case when s.status in ('open', 'needs_doctor') then 'closed' else s.status end
    from ops.care_plans p where s.plan_id = p.id and s.clinic_id = p.clinic_id and p.erased_at is not null and (s.label <> 'erased' or s.status in ('open', 'needs_doctor'));
update msg.messages m set body_ct = null, body_key_version = null, erased_at = clock_timestamp()
    from msg.threads t where m.thread_id = t.id and m.clinic_id = t.clinic_id and t.patient_id in (select id from mpi.patients where erased_at is not null) and m.erased_at is null;
update msg.voice_sessions vs set status = 'ended'
    where vs.status = 'open' and exists (select 1 from mpi.patients pt where pt.account_id = vs.account_id and pt.clinic_id = vs.clinic_id and pt.erased_at is not null);
delete from msg.voice_transcript_chunks c
    where exists (select 1 from msg.voice_sessions s join mpi.patients pt on pt.account_id = s.account_id and pt.clinic_id = s.clinic_id where s.id = c.session_id and s.clinic_id = c.clinic_id and pt.erased_at is not null);
alter table ops.care_plans add constraint care_plans_erased_status check ((erased_at is not null) = (status = 'erased'));

do $$
declare r text; f text;
begin
    if has_column_privilege('careflow_app', 'ops.care_plans', 'plan_ct', 'update') or has_column_privilege('careflow_app', 'ops.care_plan_steps', 'label', 'update') or has_column_privilege('careflow_app', 'ops.care_plans', 'erased_at', 'update')
       or has_column_privilege('careflow_app', 'msg.messages', 'body_ct', 'update') or has_column_privilege('careflow_app', 'msg.messages', 'erased_at', 'update')
       or has_table_privilege('careflow_app', 'msg.voice_transcript_chunks', 'delete') then
        raise exception '0057: the application role must not be able to erase or close any of these';
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, 'mpi.approve_patient_erasure(uuid,text)', 'execute') then
            raise exception '0057: the erasure approval must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'mpi.approve_patient_erasure(uuid,text)', 'execute') or not has_function_privilege('careflow_app', 'mpi.approve_patient_erasure(uuid,text)', 'execute') then
        raise exception '0057: the erasure approval keeps its existing callers (the application login) and no others';
    end if;
    foreach f in array array['ops.care_plans_guard()', 'ops.care_plan_steps_guard()', 'ops.care_plan_waits_guard()', 'msg.voice_chunks_guard()'] loop
        if has_function_privilege('public', f, 'execute') then
            raise exception '0057: % must not be executable by PUBLIC', f;
        end if;
    end loop;
end $$;
