-- 0080_patient_portal_profile_notifications_requests_public_grievance (owner decision 2026-11-14 on frontend requests 20, 22, 24 and 26; BUILD-SPEC.md 19.3, 19.4, 20.1; CLAUDE.md rules 1, 3, 5, 6, 8, 10).
--
-- # CLINICAL-PLACEHOLDER / LEGAL-PLACEHOLDER: needs counsel and clinician sign-off  (the word lists and wording of anything a patient reads)
--
-- MECHANISM ONLY. No wording, no word list, no number is decided here; the lists are required settings of the application and the numbers are owner-managed rows.
--  1. iam.patient_profiles: a patient account's OWN profile fields (sex as a closed word, preferred language en or ta, spec 19.4). The name is NOT stored again: it is the name on the patient's own record. Written only by iam.set_patient_profile.
--  2. iam.notifications: IN-APP notices for a patient account (a closed kind, an opaque reference, read once). No e-mail or SMS: no provider is approved. Written by definer functions only; the patient reads and marks read.
--  3. iam.patient_requests: a patient's erasure, correction or nominee REQUEST (a closed kind; for a correction, a closed field name: never the value). It carries NO free text and no new personal data. Front desk of the clinic works it:
--     it feeds the EXISTING two-person erasure flow (front desk starts that flow as before and may LINK it here) and the existing record-edit paths; nothing is erased or changed by a request itself.
--  4. A PUBLIC grievance (no sign-in): reuses iam.grievances (so encryption, key rotation, erasure and the admin acknowledge or resolve flow are the same). `account_id` may now be null for a row whose source is `public`. The writer is
--     iam.file_public_grievance: it needs no session, writes only to the clinic the owner published (0070), refuses when the owner-managed hourly cap (iam.public_grievance_limits, no application grant, no row = refused) is reached
--     (SQLSTATE 54000), and takes an idempotency key. A per-address limit is in the application. The text is encrypted by the application before it arrives.
-- NEW GRANTS (ledger): SELECT on the three new tables through own-row or role policies; EXECUTE on the new functions to careflow_app. No insert, update or delete grant on any table.

-- ---- 1. profiles --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
create table iam.patient_profiles (
    account_id         uuid primary key references iam.accounts (id),
    origin             text not null check (origin in ('live', 'test', 'eval', 'canary')),
    sex                text check (sex is null or sex ~ '^[a-z][a-z_]{0,19}$'),
    preferred_language text check (preferred_language is null or preferred_language in ('en', 'ta')),
    updated_at         timestamptz not null default clock_timestamp()
);
alter table iam.patient_profiles enable row level security;
alter table iam.patient_profiles force row level security;
revoke all on iam.patient_profiles from public;
grant select on iam.patient_profiles to careflow_app;
create policy patient_profiles_select_own on iam.patient_profiles for select to careflow_app using (account_id = auth.uid());

create function iam.set_patient_profile(p_sex text, p_language text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
begin
    if v_actor is null or (p_sex is not null and p_sex !~ '^[a-z][a-z_]{0,19}$') or (p_language is not null and p_language not in ('en', 'ta'))
       or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'profile update refused' using errcode = '42501';
    end if;
    select a.origin into v_origin from iam.accounts a where a.id = v_actor;
    insert into iam.patient_profiles (account_id, origin, sex, preferred_language) values (v_actor, v_origin, p_sex, p_language)
    on conflict (account_id) do update set sex = excluded.sex, preferred_language = excluded.preferred_language, updated_at = clock_timestamp();
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, null, v_actor, 'patient', 'patient.profile_update', 'account', v_actor::text, '{}'::jsonb, 'sys:profile-update:' || v_actor::text || ':' || gen_random_uuid()::text);
    return true;
end
$$;

-- ---- 2. notifications -----------------------------------------------------------------------------------------------------------------------------------------------------------------------
create table iam.notifications (
    id              uuid primary key default gen_random_uuid(),
    account_id      uuid not null references iam.accounts (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    kind            text not null check (kind in ('request_update', 'grievance_update', 'offer_sent')),
    ref             text check (ref is null or ref ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'),  -- an opaque id (a request, grievance or hold), never text
    created_at      timestamptz not null default clock_timestamp(),
    read_at         timestamptz,
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint notifications_key_per_account unique (account_id, idempotency_key)
);
create index notifications_account_idx on iam.notifications (account_id, created_at desc);

create function iam.notifications_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'iam.notifications rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.read_at is null and new.read_at is not null and (to_jsonb(new) - 'read_at') = (to_jsonb(old) - 'read_at') then
        return new;  -- the one change: read, once
    end if;
    raise exception 'a notification can only be marked read, once' using errcode = 'restrict_violation';
end
$$;
create trigger notifications_guard before update or delete on iam.notifications for each row execute function iam.notifications_guard();
create trigger notifications_no_truncate before truncate on iam.notifications for each statement execute function iam.notifications_guard();
alter table iam.notifications enable always trigger notifications_guard;
alter table iam.notifications enable always trigger notifications_no_truncate;
alter table iam.notifications enable row level security;
alter table iam.notifications force row level security;
revoke all on iam.notifications from public;
grant select on iam.notifications to careflow_app;
create policy notifications_select_own on iam.notifications for select to careflow_app using (account_id = auth.uid());

create function iam.mark_notification_read(p_id uuid) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_rows  integer;
begin
    if v_actor is null or p_id is null or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'notification refused' using errcode = '42501';
    end if;
    update iam.notifications set read_at = clock_timestamp() where id = p_id and account_id = v_actor and read_at is null;
    get diagnostics v_rows = row_count;
    if v_rows = 0 and not exists (select 1 from iam.notifications n where n.id = p_id and n.account_id = v_actor) then
        raise exception 'notification refused' using errcode = '42501';  -- not yours, or not there: one answer
    end if;
    return v_rows = 1;  -- false: it was already read
end
$$;

-- a staff member of the patient's clinic tells the patient's bound account something happened (a closed kind and an opaque reference; no text)
create function iam.notify_patient(p_clinic_id uuid, p_patient_id uuid, p_kind text, p_ref text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor   uuid := auth.uid();
    v_account uuid;
    v_origin  text;
    v_rows    integer;
begin
    if v_actor is null or p_clinic_id is null or p_patient_id is null or p_kind is null or p_kind not in ('request_update', 'grievance_update', 'offer_sent')
       or (p_ref is not null and p_ref !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 300
       or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']) then
        raise exception 'notification refused' using errcode = '42501';
    end if;
    select pt.account_id, pt.origin into v_account, v_origin from mpi.patients pt where pt.id = p_patient_id and pt.clinic_id = p_clinic_id and pt.erased_at is null;
    if v_account is null then
        return false;  -- the patient has no signed-in account to tell: nothing to write, and that is not an error
    end if;
    insert into iam.notifications (account_id, origin, kind, ref, idempotency_key) values (v_account, v_origin, p_kind, p_ref, p_idempotency_key) on conflict (account_id, idempotency_key) do nothing;
    get diagnostics v_rows = row_count;
    if v_rows = 1 then
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_origin, p_clinic_id, v_actor, 'staff', 'patient_notification.send', 'patient', p_patient_id::text, jsonb_build_object('kind', p_kind), 'sys:patient-notify:' || v_account::text || ':' || p_idempotency_key);
    end if;
    return v_rows = 1;
end
$$;

-- ---- 3. patient requests --------------------------------------------------------------------------------------------------------------------------------------------------------------------
create table iam.patient_requests (
    id                 uuid primary key,
    clinic_id          uuid not null references iam.clinics (id),
    origin             text not null check (origin in ('live', 'test', 'eval', 'canary')),
    account_id         uuid not null references iam.accounts (id),
    patient_id         uuid not null,
    kind               text not null check (kind in ('erasure', 'correction', 'nominee')),
    field              text check (field is null or field in ('name', 'date_of_birth', 'phone')),
    status             text not null default 'open' check (status in ('open', 'acknowledged', 'resolved', 'declined')),
    erasure_request_id uuid,
    created_at         timestamptz not null default clock_timestamp(),
    updated_at         timestamptz not null default clock_timestamp(),
    idempotency_key    text not null check (length(idempotency_key) between 1 and 300),
    constraint patient_requests_key_per_account unique (account_id, idempotency_key),
    constraint patient_requests_id_clinic unique (id, clinic_id),
    constraint patient_requests_field_iff_correction check ((kind = 'correction') = (field is not null)),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id)
);
create index patient_requests_clinic_idx on iam.patient_requests (clinic_id, status, created_at);

create function iam.patient_requests_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'iam.patient_requests rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if (to_jsonb(new) - 'status' - 'erasure_request_id' - 'updated_at') <> (to_jsonb(old) - 'status' - 'erasure_request_id' - 'updated_at') then
        raise exception 'only the status and the erasure link of a request can change' using errcode = 'restrict_violation';
    end if;
    if new.status <> old.status and not ((old.status = 'open' and new.status in ('acknowledged', 'resolved', 'declined')) or (old.status = 'acknowledged' and new.status in ('resolved', 'declined'))) then
        raise exception 'a request status moves only forward' using errcode = 'restrict_violation';
    end if;
    if old.erasure_request_id is not null and new.erasure_request_id is distinct from old.erasure_request_id then
        raise exception 'the erasure link is set once' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;
create trigger patient_requests_guard before update or delete on iam.patient_requests for each row execute function iam.patient_requests_guard();
create trigger patient_requests_no_truncate before truncate on iam.patient_requests for each statement execute function iam.patient_requests_guard();
alter table iam.patient_requests enable always trigger patient_requests_guard;
alter table iam.patient_requests enable always trigger patient_requests_no_truncate;
alter table iam.patient_requests enable row level security;
alter table iam.patient_requests force row level security;
revoke all on iam.patient_requests from public;
grant select on iam.patient_requests to careflow_app;
create policy patient_requests_select on iam.patient_requests for select to careflow_app
    using (account_id = auth.uid() or (iam.has_clinic_role(clinic_id, array['front_desk']) and iam.aal2_session()));

create function iam.file_patient_request(p_clinic_id uuid, p_kind text, p_field text, p_id uuid, p_idempotency_key text) returns table (request_id uuid, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor   uuid := auth.uid();
    v_patient uuid;
    v_origin  text;
    v_prior   record;
begin
    if v_actor is null or p_clinic_id is null or p_id is null or p_kind is null or p_kind not in ('erasure', 'correction', 'nominee')
       or (p_kind = 'correction') <> (p_field is not null) or (p_field is not null and p_field not in ('name', 'date_of_birth', 'phone'))
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 300
       or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'request refused' using errcode = '42501';
    end if;
    select r.id, r.clinic_id, r.kind, r.field into v_prior from iam.patient_requests r where r.account_id = v_actor and r.idempotency_key = p_idempotency_key;
    if found then
        if v_prior.clinic_id <> p_clinic_id or v_prior.kind <> p_kind or v_prior.field is distinct from p_field then
            raise exception 'request refused' using errcode = '42501';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    select pt.id, pt.origin into v_patient, v_origin from mpi.patients pt where pt.account_id = v_actor and pt.clinic_id = p_clinic_id and pt.erased_at is null;
    if v_patient is null then
        raise exception 'request refused' using errcode = '42501';  -- no live record of yours at that clinic
    end if;
    if (select count(*) from iam.patient_requests r where r.account_id = v_actor and r.status in ('open', 'acknowledged')) >= 20 then
        raise exception 'request refused' using errcode = '42501';  -- an engineering bound against flooding the front-desk queue: twenty unfinished requests per account
    end if;
    begin
        insert into iam.patient_requests (id, clinic_id, origin, account_id, patient_id, kind, field, idempotency_key) values (p_id, p_clinic_id, v_origin, v_actor, v_patient, p_kind, p_field, p_idempotency_key);
    exception when unique_violation then
        select r.id, r.clinic_id, r.kind, r.field into v_prior from iam.patient_requests r where r.account_id = v_actor and r.idempotency_key = p_idempotency_key;
        if found and v_prior.clinic_id = p_clinic_id and v_prior.kind = p_kind and v_prior.field is not distinct from p_field then
            return query select v_prior.id, false;
            return;
        end if;
        raise exception 'request refused' using errcode = '42501';
    end;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'patient', 'patient_request.file', 'patient_request', p_id::text, jsonb_build_object('kind', p_kind, 'field', p_field), 'sys:patient-request-file:' || p_id::text);
    return query select p_id, true;
end
$$;

-- front desk of the clinic works a request: acknowledge, resolve or decline; may LINK the existing two-person erasure request (checked: same clinic and patient); the patient gets an in-app notice
create function iam.update_patient_request(p_request_id uuid, p_status text, p_erasure_request_id uuid) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_r     record;
begin
    if v_actor is null or p_request_id is null or p_status is null or p_status not in ('acknowledged', 'resolved', 'declined') or not iam.aal2_session() then
        raise exception 'request update refused' using errcode = '42501';
    end if;
    select r.id, r.clinic_id, r.origin, r.account_id, r.patient_id, r.kind, r.status, r.erasure_request_id into v_r from iam.patient_requests r where r.id = p_request_id for update;
    if not found or not iam.has_clinic_role(v_r.clinic_id, array['front_desk']) then
        raise exception 'request update refused' using errcode = '42501';
    end if;
    if p_erasure_request_id is not null and (v_r.kind <> 'erasure' or not exists (
        select 1 from mpi.erasure_requests e where e.id = p_erasure_request_id and e.clinic_id = v_r.clinic_id and e.patient_id = v_r.patient_id)) then
        raise exception 'request update refused' using errcode = '42501';
    end if;
    if v_r.erasure_request_id is not null and p_erasure_request_id is not null and p_erasure_request_id <> v_r.erasure_request_id then
        return 'invalid_transition';  -- the erasure link is set once: a different one is refused, not silently ignored
    end if;
    if v_r.status = p_status and (p_erasure_request_id is null or v_r.erasure_request_id is not distinct from p_erasure_request_id) then
        return 'replay';
    end if;
    if not ((v_r.status = 'open' and p_status in ('acknowledged', 'resolved', 'declined')) or (v_r.status = 'acknowledged' and p_status in ('resolved', 'declined'))) then
        return 'invalid_transition';
    end if;
    update iam.patient_requests set status = p_status, erasure_request_id = coalesce(v_r.erasure_request_id, p_erasure_request_id), updated_at = clock_timestamp() where id = p_request_id;
    insert into iam.notifications (account_id, origin, kind, ref, idempotency_key) values (v_r.account_id, v_r.origin, 'request_update', p_request_id::text, 'request-update:' || p_request_id::text || ':' || p_status)
    on conflict (account_id, idempotency_key) do nothing;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_r.origin, v_r.clinic_id, v_actor, 'front_desk', 'patient_request.update', 'patient_request', p_request_id::text,
            jsonb_build_object('kind', v_r.kind, 'status', p_status, 'linked', p_erasure_request_id is not null), 'sys:patient-request-update:' || p_request_id::text || ':' || p_status);
    return 'recorded';
end
$$;

-- ---- 4. public grievance (no sign-in) ---------------------------------------------------------------------------------------------------------------------------------------------------
alter table iam.grievances alter column account_id drop not null;
alter table iam.grievances add column source text not null default 'patient' check (source in ('patient', 'public'));
alter table iam.grievances add constraint grievances_source_matches_account check ((source = 'public') = (account_id is null));
create unique index grievances_public_key on iam.grievances (clinic_id, idempotency_key) where account_id is null;

create table iam.public_grievance_limits (
    clinic_id     uuid primary key references iam.clinics (id),
    max_per_hour  integer not null check (max_per_hour between 1 and 1000),
    set_at        timestamptz not null default clock_timestamp()
);
alter table iam.public_grievance_limits enable row level security;
alter table iam.public_grievance_limits force row level security;
revoke all on iam.public_grievance_limits from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.public_grievance_limits from %I', r);
        end if;
    end loop;
end $$;

create function iam.file_public_grievance(p_category text, p_body_ct bytea, p_key_version smallint, p_grievance_id uuid, p_idempotency_key text)
    returns table (grievance_id uuid, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_clinic uuid;
    v_origin text;
    v_cap    integer;
    v_prior  record;
begin
    -- the key of an ANONYMOUS caller is global to the clinic, so it must be unguessable: 32 to 300 characters (a random uuid is the intended shape)
    if p_category is null or p_category !~ '^[a-z][a-z0-9_]{0,39}$' or p_body_ct is null or octet_length(p_body_ct) not between 29 and 20100 or p_key_version is null or p_key_version < 1
       or p_grievance_id is null or p_idempotency_key is null or length(p_idempotency_key) not between 32 and 300 or p_idempotency_key !~ '^[A-Za-z0-9._:-]+$' then
        raise exception 'grievance refused' using errcode = '42501';
    end if;
    select pc.clinic_id into v_clinic from iam.public_clinic() pc;
    if v_clinic is null then
        raise exception 'no public clinic' using errcode = 'no_data_found';  -- nothing published: the route answers 404
    end if;
    select l.max_per_hour into v_cap from iam.public_grievance_limits l where l.clinic_id = v_clinic;
    if v_cap is null then
        raise exception 'grievance refused' using errcode = '42501';  -- no cap set by the owner: unknown is not a value
    end if;
    select g.id, g.category into v_prior from iam.grievances g where g.clinic_id = v_clinic and g.account_id is null and g.idempotency_key = p_idempotency_key;
    if found then
        if v_prior.category <> p_category then
            raise exception 'grievance refused' using errcode = '42501';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    if (select count(*) from iam.grievances g where g.clinic_id = v_clinic and g.account_id is null and g.created_at > clock_timestamp() - interval '1 hour') >= v_cap then
        raise exception 'too many public grievances' using errcode = 'program_limit_exceeded';
    end if;
    select c.origin into v_origin from iam.clinics c where c.id = v_clinic;
    begin
        insert into iam.grievances (id, clinic_id, origin, account_id, category, body_ct, body_key_version, idempotency_key, source)
        values (p_grievance_id, v_clinic, v_origin, null, p_category, p_body_ct, p_key_version, p_idempotency_key, 'public');
    exception when unique_violation then
        select g.id, g.category into v_prior from iam.grievances g where g.clinic_id = v_clinic and g.account_id is null and g.idempotency_key = p_idempotency_key;
        if found and v_prior.category = p_category then
            return query select v_prior.id, false;
            return;
        end if;
        raise exception 'grievance refused' using errcode = '42501';
    end;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, v_clinic, null, 'public', 'grievance.file_public', 'grievance', p_grievance_id::text, jsonb_build_object('category', p_category), 'sys:grievance-file-public:' || p_grievance_id::text);
    return query select p_grievance_id, true;
end
$$;

-- ---- grants, ACLs, closing assertions ------------------------------------------------------------------------------------------------------------------------------------------------
revoke all on function iam.set_patient_profile(text, text), iam.mark_notification_read(uuid), iam.notify_patient(uuid, uuid, text, text, text), iam.file_patient_request(uuid, text, text, uuid, text),
              iam.update_patient_request(uuid, text, uuid), iam.file_public_grievance(text, bytea, smallint, uuid, text), iam.notifications_guard(), iam.patient_requests_guard() from public;
grant execute on function iam.set_patient_profile(text, text), iam.mark_notification_read(uuid), iam.notify_patient(uuid, uuid, text, text, text), iam.file_patient_request(uuid, text, text, uuid, text),
                          iam.update_patient_request(uuid, text, uuid), iam.file_public_grievance(text, bytea, smallint, uuid, text) to careflow_app;

do $$
declare r text; f text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.set_patient_profile(text,text), iam.mark_notification_read(uuid), iam.notify_patient(uuid,uuid,text,text,text), iam.file_patient_request(uuid,text,text,uuid,text), iam.update_patient_request(uuid,text,uuid), iam.file_public_grievance(text,bytea,smallint,uuid,text), iam.notifications_guard(), iam.patient_requests_guard() from %I', r);
            foreach f in array array['iam.set_patient_profile(text,text)', 'iam.mark_notification_read(uuid)', 'iam.notify_patient(uuid,uuid,text,text,text)', 'iam.file_patient_request(uuid,text,text,uuid,text)',
                                     'iam.update_patient_request(uuid,text,uuid)', 'iam.file_public_grievance(text,bytea,smallint,uuid,text)'] loop
                if has_function_privilege(r, f, 'execute') then
                    raise exception '0080: % must not be executable by %', f, r;
                end if;
            end loop;
            execute format('revoke all on iam.patient_profiles, iam.notifications, iam.patient_requests from %I', r);
        end if;
    end loop;
    if has_any_column_privilege('careflow_app', 'iam.patient_profiles', 'insert') or has_any_column_privilege('careflow_app', 'iam.patient_profiles', 'update')
       or has_any_column_privilege('careflow_app', 'iam.notifications', 'insert') or has_any_column_privilege('careflow_app', 'iam.notifications', 'update') or has_table_privilege('careflow_app', 'iam.notifications', 'delete') or has_table_privilege('careflow_app', 'iam.notifications', 'truncate')
       or has_any_column_privilege('careflow_app', 'iam.patient_requests', 'insert') or has_any_column_privilege('careflow_app', 'iam.patient_requests', 'update') or has_table_privilege('careflow_app', 'iam.patient_requests', 'delete') or has_table_privilege('careflow_app', 'iam.patient_requests', 'truncate')
       or has_function_privilege('public', 'iam.notifications_guard()', 'execute') or has_function_privilege('public', 'iam.patient_requests_guard()', 'execute') then
        raise exception '0080: the new tables are written only by their functions';
    end if;
    if has_table_privilege('careflow_app', 'iam.public_grievance_limits', 'select') or has_any_column_privilege('careflow_app', 'iam.public_grievance_limits', 'select') then
        raise exception '0080: the application role must have no access to the public grievance limits';
    end if;
end $$;
