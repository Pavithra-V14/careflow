-- 0100_clinic_public_profile: the hospital's PUBLIC profile for the signed-out portal home page (owner request 2026-11-20). The page showed "[PLACEHOLDER: ...]" for about, departments, address, opening hours,
-- phone and the privacy promise because nothing could hold them. MECHANISM ONLY: no value is entered here, no default exists, nothing is invented; the hospital supplies every field, and the privacy promise is
-- counsel's.
--
-- iam.clinic_public_profile: ONE row per clinic, every text field NULLABLE (null means "not supplied": an empty string is refused, so the page can never show an empty value pretending to be content).
--   about, address, opening_hours, phone, privacy_promise: single-line text (no control characters), length-capped.
--   departments: a jsonb array (at most 30) of {"name": text, "doctors": [text, ...]} where the doctor display names are OPTIONAL and chosen by the clinic (at most 20 per department).
--   provenance: a label such as "DEMO PLACEHOLDER, not approved by the clinic owner" written by the owner-run demo seed; a real write through the function below clears it.
-- Forced row-level security, NO grant to any application role: it is read and written only through the two definer functions below.
--
-- iam.public_clinic_profile(): PUBLIC (no sign-in). Returns the profile of the ONE published clinic (iam.public_clinics), or NO row when no clinic is published, so another clinic is never revealed. It returns
-- the clinic name, the nine content fields and two booleans the page needs to say what is really on (a bookable visit type exists; a family-link rule exists): booleans, never counts, never an id. No PHI, no
-- patient count.
--
-- iam.set_clinic_public_profile(...): an ADMIN of the clinic with a verified second factor (iam.aal2_session()), an idempotency key (a repeat of the same key and content is a `replay`; the same key with
-- other content is refused 23505), validation of every field (fixed refusal 42501 otherwise), and ONE audit row that names the fields set, never their text. A real write clears the demo label.
--
-- ops.demo_placeholder_rows() is re-issued with one more source (iam.clinic_public_profile), so the startup check that refuses a non-demo deployment while a placeholder row exists covers this table too.

create table iam.clinic_public_profile (
    clinic_id      uuid primary key references iam.clinics (id),
    about          text check (about is null or (length(btrim(about)) between 1 and 2000 and about !~ '[[:cntrl:]]')),
    departments    jsonb check (departments is null or (jsonb_typeof(departments) = 'array' and jsonb_array_length(departments) between 1 and 30)),
    address        text check (address is null or (length(btrim(address)) between 1 and 300 and address !~ '[[:cntrl:]]')),
    opening_hours  text check (opening_hours is null or (length(btrim(opening_hours)) between 1 and 500 and opening_hours !~ '[[:cntrl:]]')),
    phone          text check (phone is null or (length(btrim(phone)) between 1 and 60 and phone !~ '[[:cntrl:]]')),
    privacy_promise text check (privacy_promise is null or (length(btrim(privacy_promise)) between 1 and 4000 and privacy_promise !~ '[[:cntrl:]]')),
    provenance     text check (provenance is null or length(provenance) <= 200),
    updated_by     uuid references iam.accounts (id),
    updated_at     timestamptz not null default clock_timestamp(),
    last_key       text check (last_key is null or length(last_key) <= 200),
    last_hash      text check (last_hash is null or length(last_hash) = 32)
);
alter table iam.clinic_public_profile enable row level security;
alter table iam.clinic_public_profile force row level security;
revoke all on iam.clinic_public_profile from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.clinic_public_profile from %I', r);
        end if;
    end loop;
end $$;

-- a department list must hold well-formed entries: validated here so both the function and any owner script use the same rule
create function iam.valid_public_departments(p jsonb) returns boolean
    language plpgsql immutable set search_path = ''
as $$
declare
    d jsonb;
    n jsonb;
begin
    if p is null then
        return true;
    end if;
    if jsonb_typeof(p) <> 'array' or jsonb_array_length(p) not between 1 and 30 then
        return false;
    end if;
    for d in select value from jsonb_array_elements(p) loop
        if jsonb_typeof(d) <> 'object' then
            return false;
        end if;
        if exists (select 1 from jsonb_object_keys(d) k where k not in ('name', 'doctors')) then
            return false;
        end if;
        if jsonb_typeof(d -> 'name') is distinct from 'string' then
            return false;
        end if;
        if length(btrim(d ->> 'name')) not between 1 and 80 or (d ->> 'name') ~ '[[:cntrl:]]' then
            return false;
        end if;
        if d ? 'doctors' then
            if jsonb_typeof(d -> 'doctors') is distinct from 'array' then
                return false;
            end if;
            if jsonb_array_length(d -> 'doctors') > 20 then
                return false;
            end if;
            for n in select value from jsonb_array_elements(d -> 'doctors') loop
                if jsonb_typeof(n) is distinct from 'string' then
                    return false;
                end if;
                if length(btrim(n #>> '{}')) not between 1 and 80 or (n #>> '{}') ~ '[[:cntrl:]]' then
                    return false;
                end if;
            end loop;
        end if;
    end loop;
    return true;
end
$$;
revoke all on function iam.valid_public_departments(jsonb) from public;

alter table iam.clinic_public_profile add constraint clinic_public_profile_departments_valid check (iam.valid_public_departments(departments));

create function iam.public_clinic_profile()
    returns table (o_name text, o_about text, o_departments jsonb, o_address text, o_opening_hours text, o_phone text, o_privacy_promise text, o_placeholder boolean, o_has_bookable_type boolean, o_has_family_rule boolean)
    language sql stable security definer set search_path = ''
as $$
    select c.name, p.about, p.departments, p.address, p.opening_hours, p.phone, p.privacy_promise, coalesce(p.provenance like 'DEMO PLACEHOLDER%', false),
           exists (select 1 from sched.patient_bookable_visit_types b join sched.visit_types vt on vt.id = b.visit_type_id and vt.clinic_id = b.clinic_id and vt.active where b.clinic_id = pc.clinic_id),
           exists (select 1 from iam.family_link_rules r where r.clinic_id = pc.clinic_id)
      from iam.public_clinics pc
      join iam.clinics c on c.id = pc.clinic_id
      left join iam.clinic_public_profile p on p.clinic_id = pc.clinic_id
     limit 1
$$;

create function iam.set_clinic_public_profile(
    p_clinic_id uuid, p_about text, p_departments jsonb, p_address text, p_opening_hours text, p_phone text, p_privacy_promise text, p_idempotency_key text
) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_hash   text := md5(jsonb_build_array(p_about, p_departments, p_address, p_opening_hours, p_phone, p_privacy_promise)::text);
    v_prior  record;
    v_set    text[];
begin
    if v_actor is null or p_clinic_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['admin']) then
        raise exception 'clinic profile refused' using errcode = '42501';
    end if;
    -- every field is either null (not supplied) or real text: a blank or control-character string is a refusal, never stored
    if (p_about is not null and (length(btrim(p_about)) not between 1 and 2000 or p_about ~ '[[:cntrl:]]'))
       or (p_address is not null and (length(btrim(p_address)) not between 1 and 300 or p_address ~ '[[:cntrl:]]'))
       or (p_opening_hours is not null and (length(btrim(p_opening_hours)) not between 1 and 500 or p_opening_hours ~ '[[:cntrl:]]'))
       or (p_phone is not null and (length(btrim(p_phone)) not between 1 and 60 or p_phone ~ '[[:cntrl:]]'))
       or (p_privacy_promise is not null and (length(btrim(p_privacy_promise)) not between 1 and 4000 or p_privacy_promise ~ '[[:cntrl:]]'))
       or not iam.valid_public_departments(p_departments) then
        raise exception 'clinic profile refused' using errcode = '42501';
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    select last_key, last_hash into v_prior from iam.clinic_public_profile where clinic_id = p_clinic_id for update;
    if found and v_prior.last_key = p_idempotency_key then
        if v_prior.last_hash = v_hash then
            return 'replay';
        end if;
        raise exception 'idempotency key reused' using errcode = '23505';
    end if;
    insert into iam.clinic_public_profile (clinic_id, about, departments, address, opening_hours, phone, privacy_promise, provenance, updated_by, last_key, last_hash)
    values (p_clinic_id, btrim(p_about), p_departments, btrim(p_address), btrim(p_opening_hours), btrim(p_phone), btrim(p_privacy_promise), null, v_actor, p_idempotency_key, v_hash)
    on conflict (clinic_id) do update set
        about = excluded.about, departments = excluded.departments, address = excluded.address, opening_hours = excluded.opening_hours, phone = excluded.phone,
        privacy_promise = excluded.privacy_promise, provenance = null, updated_by = excluded.updated_by, updated_at = clock_timestamp(), last_key = excluded.last_key, last_hash = excluded.last_hash;
    v_set := array_remove(array[
        case when p_about is not null then 'about' end, case when p_departments is not null then 'departments' end, case when p_address is not null then 'address' end,
        case when p_opening_hours is not null then 'opening_hours' end, case when p_phone is not null then 'phone' end, case when p_privacy_promise is not null then 'privacy_promise' end
    ], null);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'admin', 'clinic.public_profile.set', 'clinic', p_clinic_id::text, jsonb_build_object('fields_set', to_jsonb(v_set)),
            'sys:clinic-profile:' || v_actor::text || ':' || md5(p_idempotency_key || ':' || v_hash));
    return 'recorded';
end
$$;

do $$
declare f text; r text;
begin
    foreach f in array array['iam.public_clinic_profile()', 'iam.set_clinic_public_profile(uuid,text,jsonb,text,text,text,text,text)'] loop
        execute format('revoke all on function %s from public', f);
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
        execute format('grant execute on function %s to careflow_app', f);
    end loop;
end $$;

-- the startup check must count this table's placeholder rows too
create or replace function ops.demo_placeholder_rows()
    returns table (o_source text, o_rows bigint)
    language sql stable security definer set search_path = ''
as $$
    select 'iam.break_glass_reasons', count(*) from iam.break_glass_reasons where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.break_glass_settings', count(*) from iam.break_glass_settings where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.public_grievance_limits', count(*) from iam.public_grievance_limits where provenance like 'DEMO PLACEHOLDER%'
    union all select 'docs.canary_settings', count(*) from docs.canary_settings where provenance like 'DEMO PLACEHOLDER%'
    union all select 'ops.spend_limits', count(*) from ops.spend_limits where provenance like 'DEMO PLACEHOLDER%'
    union all select 'sched.patient_bookable_visit_types', count(*) from sched.patient_bookable_visit_types where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.family_link_rules', count(*) from iam.family_link_rules where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.clinic_public_profile', count(*) from iam.clinic_public_profile where provenance like 'DEMO PLACEHOLDER%'
    union all select 'iam.consent_notices', count(*) from iam.consent_notices where body like 'DEMO ONLY%' or body like 'DEMO PLACEHOLDER%'
$$;

do $$
begin
    if has_table_privilege('careflow_app', 'iam.clinic_public_profile', 'select') or has_table_privilege('careflow_app', 'iam.clinic_public_profile', 'insert') then
        raise exception '0100: the application role must have no grant on iam.clinic_public_profile';
    end if;
    if not has_function_privilege('careflow_app', 'iam.public_clinic_profile()', 'execute') or has_function_privilege('public', 'iam.public_clinic_profile()', 'execute')
       or has_function_privilege('public', 'iam.set_clinic_public_profile(uuid,text,jsonb,text,text,text,text,text)', 'execute') then
        raise exception '0100: the profile functions must be executable by careflow_app only';
    end if;
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0100: the migrating role must bypass row-level security (the profile functions read the owner-managed tables through it)';
    end if;
end $$;
