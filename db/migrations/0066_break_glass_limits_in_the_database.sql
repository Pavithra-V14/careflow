-- 0066_break_glass_limits_in_the_database (owner decision of 2026-11-08; the same move 0047 made for the canary cap; CLAUDE.md rules 8, 10). Fix forward from 0062: no merged migration is edited.
--
-- The break-glass window and the re-open cap were ARGUMENTS the application passed, so any code that could run SQL as the application role could pass the largest values. They now live in
-- iam.break_glass_settings (one row per clinic; owner-managed: inserted by the owner-run script scripts/seed_break_glass_reasons.py or a migration; NO grant to any application role). A BEFORE INSERT
-- trigger on iam.break_glass_grants enforces both limits whatever the function is given: the window may not exceed max_minutes, and one person may open at most max_reopens grants for the same patient in
-- any 24 hours. With no settings row for the clinic NO grant can be opened (unknown is not a value). iam.open_break_glass loses its cap argument; its minutes argument is optional (shorter than the
-- maximum is allowed; null means the maximum). The application's two settings (CAREFLOW_BREAK_GLASS_MINUTES, CAREFLOW_BREAK_GLASS_MAX_REOPENS) become INPUTS TO THE OWNER-RUN SEED, not runtime values.

create table iam.break_glass_settings (
    clinic_id    uuid primary key references iam.clinics (id),
    max_minutes  integer not null check (max_minutes between 1 and 240),
    max_reopens  integer not null check (max_reopens between 1 and 50)
);
alter table iam.break_glass_settings enable row level security;
alter table iam.break_glass_settings force row level security;
revoke all on iam.break_glass_settings from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.break_glass_settings from %I', r);
        end if;
    end loop;
end $$;

create function iam.break_glass_caps_guard() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    s record;
begin
    select bs.max_minutes, bs.max_reopens into s from iam.break_glass_settings bs where bs.clinic_id = new.clinic_id;
    if not found then
        raise exception 'break-glass is not configured for this clinic' using errcode = '55000';
    end if;
    if new.expires_at > new.opened_at + make_interval(mins => s.max_minutes) then
        raise exception 'break-glass window above the clinic maximum' using errcode = '54000';
    end if;
    if (select count(*) from iam.break_glass_grants g where g.clinic_id = new.clinic_id and g.opened_by = new.opened_by and g.patient_id = new.patient_id
                                                             and g.opened_at > new.opened_at - interval '24 hours') >= s.max_reopens then
        raise exception 'break-glass limit reached' using errcode = '54000';
    end if;
    return new;
end
$$;
revoke all on function iam.break_glass_caps_guard() from public;
create trigger break_glass_caps before insert on iam.break_glass_grants for each row execute function iam.break_glass_caps_guard();
alter table iam.break_glass_grants enable always trigger break_glass_caps;

drop function iam.open_break_glass(uuid, uuid, text, integer, integer, text);

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
    v_max    integer;
    v_minutes integer;
begin
    if v_actor is null or p_clinic_id is null or p_patient_id is null or p_reason_code is null or p_reason_code !~ '^[a-z][a-z0-9_]{0,39}$' or (p_minutes is not null and p_minutes not between 1 and 240)
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
    -- the limits are the clinic's, held in an owner-managed table the application cannot read or write; the trigger on iam.break_glass_grants enforces the window and the re-open cap whatever is passed here
    select s.max_minutes into v_max from iam.break_glass_settings s where s.clinic_id = p_clinic_id;
    if not found then
        raise exception 'break-glass is not configured for this clinic' using errcode = '55000';  -- unknown is not a value: no settings, no grant
    end if;
    if p_minutes is not null and p_minutes > v_max then
        raise exception 'the requested window is above the clinic maximum' using errcode = '22003';
    end if;
    v_minutes := coalesce(p_minutes, v_max);
    select p.origin into v_origin from mpi.patients p where p.id = p_patient_id and p.clinic_id = p_clinic_id and p.erased_at is null;
    if not found then
        raise exception 'break-glass refused' using errcode = '42501';
    end if;
    if not exists (select 1 from iam.break_glass_reasons rr where rr.clinic_id = p_clinic_id and rr.code = p_reason_code) then
        raise exception 'that reason code is not on the clinic list' using errcode = '22023';  -- unknown is not a value: no list, no grant
    end if;
    v_exp := clock_timestamp() + make_interval(mins => v_minutes);
    insert into iam.break_glass_grants (clinic_id, origin, patient_id, opened_by, reason_code, expires_at, idempotency_key)
    values (p_clinic_id, v_origin, p_patient_id, v_actor, p_reason_code, v_exp, p_idempotency_key)
    on conflict (clinic_id, idempotency_key) do nothing returning id into v_id;
    if v_id is null then
        raise exception 'break-glass refused' using errcode = '42501';  -- a concurrent call with the same key won; the caller retries and gets the replay
    end if;
    v_role := case when iam.has_clinic_role(p_clinic_id, array['doctor']) then 'doctor' when iam.has_clinic_role(p_clinic_id, array['nurse']) then 'nurse' else 'front_desk' end;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, reason, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, v_role, 'break_glass.open', 'patient', p_patient_id::text, p_reason_code, jsonb_build_object('grant_id', v_id::text, 'minutes', v_minutes), 'sys:break-glass-open:' || v_id::text);
    return query select v_id, v_exp, true;
end
$$;

revoke all on function iam.open_break_glass(uuid, uuid, text, integer, text) from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.open_break_glass(uuid, uuid, text, integer, text), iam.break_glass_caps_guard() from %I', r);
        end if;
    end loop;
end $$;
grant execute on function iam.open_break_glass(uuid, uuid, text, integer, text) to careflow_app;

do $$
declare r text; f text := 'iam.open_break_glass(uuid,uuid,text,integer,text)';
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0066: the migrating role must bypass row-level security';
    end if;
    if has_any_column_privilege('careflow_app', 'iam.break_glass_settings', 'select') or has_any_column_privilege('careflow_app', 'iam.break_glass_settings', 'insert')
       or has_any_column_privilege('careflow_app', 'iam.break_glass_settings', 'update') then
        raise exception '0066: the break-glass limits are owner-managed';
    end if;
    if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
        raise exception '0066: % must be executable by the application login and not by PUBLIC', f;
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
            raise exception '0066: % must not be executable by %', f, r;
        end if;
    end loop;
    if has_function_privilege('public', 'iam.break_glass_caps_guard()', 'execute') then
        raise exception '0066: the caps guard must not be executable by PUBLIC';
    end if;
end $$;
