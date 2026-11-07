-- 0062_break_glass_reopen_cap_and_patient_stages (owner decisions of 2026-11-08; BUILD-SPEC.md 18.4, 17.5; CLAUDE.md rules 5, 8, 10). Fix forward from 0058 and 0059: no merged migration is edited.
--
-- (1) BREAK-GLASS CAP. iam.open_break_glass gains a required `p_max_reopens` (1 to 50): one person may open at most that many grants for the same patient in any 24 hours, so a window cannot be chained
--     for ever. The application takes the number from the required setting CAREFLOW_BREAK_GLASS_MAX_REOPENS (no default). The old five-argument function is dropped. Past the cap the function answers 54000.
-- (2) PATIENT STAGES, derived (spec 17.5, owner decision): iam.patient_stage() returns for the caller's own patient account `registered` (invited), `pending_activation` (a portal claim of theirs is
--     pending), `active` (a record is bound to the account through a portal claim a front-desk person approved) or `new_patient` (everything else that is active). The care topic is exported to the
--     patient only at `active`. Clinical notes are never in the patient download (owner decision).

drop function iam.open_break_glass(uuid, uuid, text, integer, text);

create function iam.patient_stage() returns text
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_status text;
begin
    if v_actor is null then
        raise exception 'stage refused' using errcode = '42501';
    end if;
    select a.status into v_status from iam.accounts a where a.id = v_actor and a.account_type = 'patient';
    if not found then
        raise exception 'stage refused' using errcode = '42501';
    end if;
    if v_status <> 'active' then return 'registered'; end if;
    -- `active` is tested FIRST: an account bound through an approved claim stays active even if a second, older request at another clinic is still pending
    if exists (select 1 from mpi.claim_requests c join mpi.patients pt on pt.account_id = c.account_id and pt.clinic_id = c.clinic_id
                where c.account_id = v_actor and c.status = 'resolved' and pt.erased_at is null) then
        return 'active';
    end if;
    if exists (select 1 from mpi.claim_requests c where c.account_id = v_actor and c.status = 'pending') then return 'pending_activation'; end if;
    return 'new_patient';
end
$$;

create function iam.open_break_glass(p_clinic_id uuid, p_patient_id uuid, p_reason_code text, p_minutes integer, p_max_reopens integer, p_idempotency_key text)
    returns table (grant_id uuid, expires_at timestamptz, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_role   text;
    v_prior  record;
    v_id     uuid;
    v_exp    timestamptz;
begin
    if v_actor is null or p_clinic_id is null or p_patient_id is null or p_reason_code is null or p_reason_code !~ '^[a-z][a-z0-9_]{0,39}$' or p_minutes is null or p_minutes not between 1 and 240 or p_max_reopens is null or p_max_reopens not between 1 and 50
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 300 or not iam.aal2_session()
       or not iam.has_clinic_role(p_clinic_id, array['front_desk', 'nurse', 'doctor']) then
        raise exception 'break-glass refused' using errcode = '42501';
    end if;
    -- one open at a time per person and patient: the count below and the insert cannot be raced past the cap, and a concurrent twin with the same key waits and gets the clean replay
    perform pg_advisory_xact_lock(hashtextextended('bg-open:' || v_actor::text || ':' || p_patient_id::text, 0));
    select b.id, b.opened_by, b.patient_id, b.reason_code, b.expires_at into v_prior from iam.break_glass_grants b where b.clinic_id = p_clinic_id and b.idempotency_key = p_idempotency_key;
    if found then
        -- a replay by the same person for the same patient and reason returns the same grant; anything else under that key is refused
        if v_prior.opened_by <> v_actor or v_prior.patient_id <> p_patient_id or v_prior.reason_code <> p_reason_code then
            raise exception 'break-glass refused' using errcode = '42501';
        end if;
        return query select v_prior.id, v_prior.expires_at, false;
        return;
    end if;
    -- a cap on CHAINED re-opens: one person may open at most p_max_reopens grants for the same patient in any 24 hours (a replay above returns before this check)
    if (select count(*) from iam.break_glass_grants g where g.clinic_id = p_clinic_id and g.opened_by = v_actor and g.patient_id = p_patient_id and g.opened_at > clock_timestamp() - interval '24 hours') >= p_max_reopens then
        raise exception 'break-glass limit reached' using errcode = '54000';
    end if;
    select p.origin into v_origin from mpi.patients p where p.id = p_patient_id and p.clinic_id = p_clinic_id and p.erased_at is null;
    if not found then
        raise exception 'break-glass refused' using errcode = '42501';
    end if;
    if not exists (select 1 from iam.break_glass_reasons rr where rr.clinic_id = p_clinic_id and rr.code = p_reason_code) then
        raise exception 'that reason code is not on the clinic list' using errcode = '22023';  -- unknown is not a value: no list, no grant
    end if;
    v_exp := clock_timestamp() + make_interval(mins => p_minutes);
    insert into iam.break_glass_grants (clinic_id, origin, patient_id, opened_by, reason_code, expires_at, idempotency_key)
    values (p_clinic_id, v_origin, p_patient_id, v_actor, p_reason_code, v_exp, p_idempotency_key)
    on conflict (clinic_id, idempotency_key) do nothing returning id into v_id;
    if v_id is null then
        raise exception 'break-glass refused' using errcode = '42501';  -- a concurrent call with the same key won; the caller retries and gets the replay
    end if;
    v_role := case when iam.has_clinic_role(p_clinic_id, array['doctor']) then 'doctor' when iam.has_clinic_role(p_clinic_id, array['nurse']) then 'nurse' else 'front_desk' end;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, reason, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, v_role, 'break_glass.open', 'patient', p_patient_id::text, p_reason_code, jsonb_build_object('grant_id', v_id::text, 'minutes', p_minutes), 'sys:break-glass-open:' || v_id::text);
    return query select v_id, v_exp, true;
end
$$;

create or replace function iam.export_my_messages()
    returns table (clinic_id uuid, message_id uuid, thread_id uuid, topic text, sender_kind text, created_at timestamptz, erased_at timestamptz, body_ct bytea, body_key_version smallint)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_n     integer;
    v_care  boolean;
begin
    if v_actor is null or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'export refused' using errcode = '42501';
    end if;
    -- appointments and safety topics always; the care topic only at the `active` stage (spec 17.5: it opens after activation)
    v_care := iam.patient_stage() = 'active';
    select count(*) into v_n from (
        select m.id from msg.messages m join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
         where pt.account_id = v_actor and pt.erased_at is null and (m.topic in ('appointments', 'safety') or (v_care and m.topic = 'care')) limit 2001) c;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    select pt.origin, pt.clinic_id, v_actor, 'patient', 'portal.export', 'account', v_actor::text, jsonb_build_object('part', 'messages', 'messages', v_n), 'sys:portal-export:' || gen_random_uuid()::text
      from mpi.patients pt where pt.account_id = v_actor limit 1;
    return query
    select m.clinic_id, m.id, m.thread_id, m.topic, m.sender_kind, m.created_at, m.erased_at, m.body_ct, m.body_key_version
      from msg.messages m join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id join mpi.patients pt on pt.id = t.patient_id and pt.clinic_id = t.clinic_id
     where pt.account_id = v_actor and pt.erased_at is null and (m.topic in ('appointments', 'safety') or (v_care and m.topic = 'care')) order by m.created_at, m.id limit 2001;
end
$$;

revoke all on function iam.open_break_glass(uuid, uuid, text, integer, integer, text), iam.patient_stage() from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.open_break_glass(uuid, uuid, text, integer, integer, text), iam.patient_stage() from %I', r);
        end if;
    end loop;
end $$;
grant execute on function iam.open_break_glass(uuid, uuid, text, integer, integer, text), iam.patient_stage() to careflow_app;

do $$
declare r text; f text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0062: the migrating role must bypass row-level security';
    end if;
    foreach f in array array['iam.open_break_glass(uuid,uuid,text,integer,integer,text)', 'iam.patient_stage()', 'iam.export_my_messages()'] loop
        if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0062: % must be executable by the application login and not by PUBLIC', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0062: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
end $$;
