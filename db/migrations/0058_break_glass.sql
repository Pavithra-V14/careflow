-- 0058_break_glass (owner request 2026-11-08; BUILD-SPEC.md 18.4, 12.1; CLAUDE.md rules 5, 6, 8, 10).
--
-- Spec 18.4: "A break-glass access path exists for emergencies, requires a stated reason, is audited immediately, and is reviewed after the fact." The spec names no two-person rule for it (12.5 lists the two-person
-- acts and break-glass is not one), so none is built; the review afterwards is the control.
--
-- WHAT IT IS. An active front-desk, nurse or doctor of a clinic, with a verified second factor, opens a TIME-LIMITED grant for ONE patient of that clinic, naming a reason CODE (a closed word from a required
-- setting; free text is never stored, so patient text cannot land in a log). While the grant is live its holder (and only its holder) can read that patient's messages, every topic, through
-- msg.break_glass_messages, which returns the stored ciphertext (the application decrypts it with the vault) and writes an audit row per call. Opening writes an audit row in the same transaction (the reason
-- code goes in audit.log.reason, the column 0001 reserved for exactly this). An ADMIN of the clinic, a different person, reviews the grant afterwards (appropriate or inappropriate); the review is an audit row too.
-- Reads of other record types are NOT covered (named in the gap audit).
--
-- NO GRANT to the application role beyond SELECT on the grants table (its holder and the clinic's admins, second factor required) and EXECUTE on the three definer functions. It cannot insert, update or delete a grant.

create table iam.break_glass_grants (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    patient_id      uuid not null,
    opened_by       uuid not null references iam.accounts (id),
    reason_code     text not null check (reason_code ~ '^[a-z][a-z0-9_]{0,39}$'),
    opened_at       timestamptz not null default clock_timestamp(),
    expires_at      timestamptz not null,
    reviewed_by     uuid references iam.accounts (id),
    reviewed_at     timestamptz,
    review_outcome  text check (review_outcome in ('appropriate', 'inappropriate')),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint break_glass_key_per_clinic unique (clinic_id, idempotency_key),
    constraint break_glass_window check (expires_at > opened_at and expires_at <= opened_at + interval '4 hours'),
    constraint break_glass_review_whole check ((reviewed_by is null) = (reviewed_at is null) and (reviewed_at is null) = (review_outcome is null)),
    constraint break_glass_reviewer_is_another check (reviewed_by is null or reviewed_by <> opened_by),
    foreign key (patient_id, clinic_id, origin) references mpi.patients (id, clinic_id, origin)
);
create index break_glass_open_idx on iam.break_glass_grants (clinic_id, opened_at desc) where reviewed_at is null;

create function iam.break_glass_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'iam.break_glass_grants rows are never deleted' using errcode = 'restrict_violation';
    end if;
    -- the ONE change: a grant that has not been reviewed gets its review (the three fields together), nothing else
    if old.reviewed_at is null and new.reviewed_at is not null
       and (to_jsonb(new) - 'reviewed_by' - 'reviewed_at' - 'review_outcome') = (to_jsonb(old) - 'reviewed_by' - 'reviewed_at' - 'review_outcome') then
        return new;
    end if;
    raise exception 'a break-glass grant can only be reviewed, once' using errcode = 'restrict_violation';
end
$$;
revoke all on function iam.break_glass_guard() from public;
create trigger break_glass_guard before update or delete on iam.break_glass_grants for each row execute function iam.break_glass_guard();
create trigger break_glass_no_truncate before truncate on iam.break_glass_grants for each statement execute function iam.break_glass_guard();

alter table iam.break_glass_grants enable row level security;
alter table iam.break_glass_grants force row level security;
revoke all on iam.break_glass_grants from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.break_glass_grants from %I', r);
            execute format('revoke all on function iam.break_glass_guard() from %I', r);
        end if;
    end loop;
end $$;
grant select on iam.break_glass_grants to careflow_app;
create policy break_glass_select on iam.break_glass_grants for select to careflow_app
    using (opened_by = auth.uid() or iam.has_clinic_role(clinic_id, array['admin']));
create policy break_glass_aal2 on iam.break_glass_grants as restrictive for select to careflow_app using (iam.aal2_session());

-- the closed list of reason codes a clinic accepts: owner-managed (inserted by a migration or an owner-run script), NO grant to any application role, so the application cannot invent a code
create table iam.break_glass_reasons (
    clinic_id uuid not null references iam.clinics (id),
    code      text not null check (code ~ '^[a-z][a-z0-9_]{0,39}$'),
    primary key (clinic_id, code)
);
alter table iam.break_glass_reasons enable row level security;
alter table iam.break_glass_reasons force row level security;
revoke all on iam.break_glass_reasons from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.break_glass_reasons from %I', r);
        end if;
    end loop;
end $$;

-- ---- open ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
create function iam.open_break_glass(p_clinic_id uuid, p_patient_id uuid, p_reason_code text, p_minutes integer, p_idempotency_key text)
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
    if v_actor is null or p_clinic_id is null or p_patient_id is null or p_reason_code is null or p_reason_code !~ '^[a-z][a-z0-9_]{0,39}$' or p_minutes is null or p_minutes not between 1 and 240
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 300 or not iam.aal2_session()
       or not iam.has_clinic_role(p_clinic_id, array['front_desk', 'nurse', 'doctor']) then
        raise exception 'break-glass refused' using errcode = '42501';
    end if;
    select b.id, b.opened_by, b.patient_id, b.reason_code, b.expires_at into v_prior from iam.break_glass_grants b where b.clinic_id = p_clinic_id and b.idempotency_key = p_idempotency_key;
    if found then
        -- a replay by the same person for the same patient and reason returns the same grant; anything else under that key is refused
        if v_prior.opened_by <> v_actor or v_prior.patient_id <> p_patient_id or v_prior.reason_code <> p_reason_code then
            raise exception 'break-glass refused' using errcode = '42501';
        end if;
        return query select v_prior.id, v_prior.expires_at, false;
        return;
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

-- ---- read (ciphertext; the application decrypts). EVERY call writes its own audit row (a server-made key); a refusal past the identity gate is audited too and answered with status 'refused' -------------------------
create function msg.break_glass_messages(p_grant_id uuid)
    returns table (status text, message_id uuid, thread_id uuid, topic text, sender_kind text, created_at timestamptz, erased_at timestamptz, body_ct bytea, body_key_version smallint)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_g     record;
    v_n     integer;
    v_role  text;
begin
    if v_actor is null or p_grant_id is null or not iam.aal2_session() then
        raise exception 'break-glass refused' using errcode = '42501';
    end if;
    select b.id, b.clinic_id, b.origin, b.patient_id, b.opened_by, b.expires_at, b.reason_code, b.reviewed_at into v_g from iam.break_glass_grants b where b.id = p_grant_id;
    if not found then
        raise exception 'break-glass refused' using errcode = '42501';
    end if;
    v_role := case when iam.has_clinic_role(v_g.clinic_id, array['doctor']) then 'doctor' when iam.has_clinic_role(v_g.clinic_id, array['nurse']) then 'nurse' when iam.has_clinic_role(v_g.clinic_id, array['front_desk']) then 'front_desk' else null end;
    -- only the holder, only while they still hold a staff role of the clinic
    if v_g.opened_by <> v_actor or v_role is null then
        raise exception 'break-glass refused' using errcode = '42501';
    end if;
    -- only while the window is open and the grant is not yet reviewed, never for an erased patient; a refusal here is audited
    if v_g.expires_at <= clock_timestamp() or v_g.reviewed_at is not null
       or not exists (select 1 from mpi.patients p where p.id = v_g.patient_id and p.clinic_id = v_g.clinic_id and p.erased_at is null) then
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, reason, details, idempotency_key)
        values (v_g.origin, v_g.clinic_id, v_actor, v_role, 'break_glass.refused', 'patient', v_g.patient_id::text, v_g.reason_code, jsonb_build_object('grant_id', p_grant_id::text), 'sys:break-glass-refused:' || gen_random_uuid()::text);
        return query select 'refused'::text, null::uuid, null::uuid, null::text, null::text, null::timestamptz, null::timestamptz, null::bytea, null::smallint;
        return;
    end if;
    select count(*) into v_n from (select m.id from msg.messages m join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id
                                    where t.patient_id = v_g.patient_id and t.clinic_id = v_g.clinic_id limit 500) c;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, reason, details, idempotency_key)
    values (v_g.origin, v_g.clinic_id, v_actor, v_role, 'break_glass.read', 'patient', v_g.patient_id::text, v_g.reason_code, jsonb_build_object('grant_id', p_grant_id::text, 'messages', v_n),
            'sys:break-glass-read:' || gen_random_uuid()::text);
    if v_n = 0 then
        return query select 'empty'::text, null::uuid, null::uuid, null::text, null::text, null::timestamptz, null::timestamptz, null::bytea, null::smallint;
        return;
    end if;
    return query
    select 'ok'::text, m.id, m.thread_id, m.topic, m.sender_kind, m.created_at, m.erased_at, m.body_ct, m.body_key_version
      from msg.messages m join msg.threads t on t.id = m.thread_id and t.clinic_id = m.clinic_id
     where t.patient_id = v_g.patient_id and t.clinic_id = v_g.clinic_id order by m.created_at, m.id limit 500;
end
$$;

-- ---- review (an admin, another person) -------------------------------------------------------------------------------------------------------------------------------------------------------------
create function iam.review_break_glass(p_grant_id uuid, p_outcome text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_g     record;
begin
    if v_actor is null or p_grant_id is null or p_outcome is null or p_outcome not in ('appropriate', 'inappropriate') or not iam.aal2_session() then
        raise exception 'break-glass review refused' using errcode = '42501';
    end if;
    select b.id, b.clinic_id, b.origin, b.opened_by, b.reviewed_at, b.review_outcome, b.patient_id into v_g from iam.break_glass_grants b where b.id = p_grant_id for update;
    if not found or not iam.has_clinic_role(v_g.clinic_id, array['admin']) then
        raise exception 'break-glass review refused' using errcode = '42501';
    end if;
    if v_g.reviewed_at is not null then
        return case when v_g.review_outcome = p_outcome then 'reviewed' else 'already_reviewed' end;
    end if;
    if v_g.opened_by = v_actor then
        return 'same_person';  -- a review by the person who opened the grant is no review
    end if;
    update iam.break_glass_grants set reviewed_by = v_actor, reviewed_at = clock_timestamp(), review_outcome = p_outcome where id = p_grant_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_g.origin, v_g.clinic_id, v_actor, 'admin', 'break_glass.review', 'patient', v_g.patient_id::text, jsonb_build_object('grant_id', p_grant_id::text, 'outcome', p_outcome), 'sys:break-glass-review:' || p_grant_id::text);
    return 'reviewed';
end
$$;

revoke all on function iam.open_break_glass(uuid, uuid, text, integer, text), msg.break_glass_messages(uuid), iam.review_break_glass(uuid, text) from public;
do $$
declare r text; f text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.open_break_glass(uuid, uuid, text, integer, text), msg.break_glass_messages(uuid), iam.review_break_glass(uuid, text) from %I', r);
        end if;
    end loop;
end $$;
grant execute on function iam.open_break_glass(uuid, uuid, text, integer, text), msg.break_glass_messages(uuid), iam.review_break_glass(uuid, text) to careflow_app;

do $$
declare f text; r text;
begin
    if has_any_column_privilege('careflow_app', 'iam.break_glass_reasons', 'select') then
        raise exception '0058: the reason list is owner-managed';
    end if;
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0058: the migrating role must bypass row-level security';
    end if;
    if has_any_column_privilege('careflow_app', 'iam.break_glass_grants', 'insert') or has_any_column_privilege('careflow_app', 'iam.break_glass_grants', 'update') or has_table_privilege('careflow_app', 'iam.break_glass_grants', 'delete') then
        raise exception '0058: the application role must not write a break-glass grant directly';
    end if;
    foreach f in array array['iam.open_break_glass(uuid,uuid,text,integer,text)', 'msg.break_glass_messages(uuid)', 'iam.review_break_glass(uuid,text)'] loop
        if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0058: % must be executable by the application login and not by PUBLIC', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0058: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
    if has_function_privilege('public', 'iam.break_glass_guard()', 'execute') then
        raise exception '0058: the guard function must not be executable by PUBLIC';
    end if;
end $$;
