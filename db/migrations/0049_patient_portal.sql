-- 0049_patient_portal: the patient-portal backend's database half (owner decision of 2026-11-06 item B; BUILD-SPEC.md 17.2, 18.4, 20.1; docs/design/deferred-items.md; CLAUDE.md rules 3, 5, 6, 7, 8, 10).
--
-- WHAT THIS BUILDS: registration of a patient sign-in account, versioned consent (notices and an append-only event log), activation after the care-delivery consent, and the CLAIM of a patient record that the
-- 17.2 rule asks for: a verified phone AND a matching name+date of birth, enforced here in the database (0012 left the application role unable to bind any account at all until this function existed).
-- WHAT IT DOES NOT BUILD: the front-desk resolution of a pending claim request (the request is recorded; nobody can yet resolve it), guardian accounts, the registered / new_patient / pending_activation / active
-- feature-gating levels of 17.2 (the account status here is only invited -> active), e-mail as the verified contact, nominee and erasure requests.
--
-- (1) iam.consent_notices: the EXACT text of each published notice, one row per (purpose, version). OWNER-MANAGED CONTENT (counsel's words, not this build's): the application can only READ them; no notice is created
--     by this migration, so with none published nothing can be consented to (unknown is not a value). A consent is only meaningful against the text shown, so an event must name the CURRENT notice of its purpose.
-- (2) iam.consent_events: append-only grants and withdrawals by the patient's own account, each naming the notice version and purpose it was about. Withdrawal is one event. Nobody but the account reads its own events.
-- (3) iam.register_patient_account(origin): a signed-in user with no account becomes an `invited` patient account (never active: activation is an explicit act). A user that already holds a non-patient account is refused.
-- (4) iam.activate_patient_account(): invited -> active, only while the account's newest care_delivery event is a grant of the CURRENT notice. Otherwise the word `consent_required`.
-- (5) mpi.claim_patient_record(...): see the function. Fixed words as results, never the reason a claim failed; five failed claims in 24 hours lock the account (a guessing limit, a security constant, not clinical);
--     a claim that finds no single CONFIRMED record becomes a pending request for the front desk. An unconfirmed record (one the unattended worker made, migration 0023) is never claimed automatically: a planted
--     record must not be capturable by the person it names.
-- RESIDUALS. (a) The tokens are computed by the application from the verified phone claim and the submitted name and date of birth; the database compares them and cannot know the phone was the verified one. (b) A patient
-- with a recycled phone number AND the right name and date of birth would claim the record: the same risk 17.2 accepts. (c) The origin of a registered account is chosen by the application from the deployment
-- profile.

create table iam.consent_notices (
    id           uuid primary key default gen_random_uuid(),
    purpose      text not null check (purpose in ('care_delivery', 'ai_processing', 'service_improvement')),
    version      integer not null check (version >= 1),
    body         text not null check (length(body) between 1 and 20000),
    published_at timestamptz not null default clock_timestamp(),
    constraint consent_notices_version_per_purpose unique (purpose, version),
    constraint consent_notices_id_purpose unique (id, purpose)
);

create table iam.consent_events (
    id              uuid primary key default gen_random_uuid(),
    account_id      uuid not null references iam.accounts (id),
    purpose         text not null,
    notice_id       uuid not null,
    action          text not null check (action in ('granted', 'withdrawn')),
    occurred_at     timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint consent_events_key_per_account unique (account_id, idempotency_key),
    foreign key (notice_id, purpose) references iam.consent_notices (id, purpose)
);
create index consent_events_latest_idx on iam.consent_events (account_id, purpose, occurred_at desc);

create function iam.reject_mutation_generic() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception '% is append-only: % is not permitted', tg_table_name, tg_op using errcode = 'restrict_violation';
end
$$;
revoke all on function iam.reject_mutation_generic() from public;
create trigger consent_events_no_update_delete before update or delete on iam.consent_events for each row execute function iam.reject_mutation_generic();
create trigger consent_events_no_truncate before truncate on iam.consent_events for each statement execute function iam.reject_mutation_generic();
create trigger consent_notices_no_update_delete before update or delete on iam.consent_notices for each row execute function iam.reject_mutation_generic();
create trigger consent_notices_no_truncate before truncate on iam.consent_notices for each statement execute function iam.reject_mutation_generic();

create table mpi.claim_attempts (
    id          uuid primary key default gen_random_uuid(),
    account_id  uuid not null references iam.accounts (id),
    clinic_id   uuid not null references iam.clinics (id),
    occurred_at timestamptz not null default clock_timestamp()
);
create index claim_attempts_account_idx on mpi.claim_attempts (account_id, occurred_at desc);

create table mpi.claim_requests (
    id          uuid primary key default gen_random_uuid(),
    clinic_id   uuid not null references iam.clinics (id),
    origin      text not null check (origin in ('live', 'test', 'eval', 'canary')),
    account_id  uuid not null references iam.accounts (id),
    status      text not null default 'pending' check (status in ('pending', 'resolved', 'rejected')),
    created_at  timestamptz not null default clock_timestamp()
);
create unique index claim_requests_one_pending on mpi.claim_requests (account_id, clinic_id) where status = 'pending';

alter table iam.consent_notices enable row level security;
alter table iam.consent_notices force row level security;
alter table iam.consent_events enable row level security;
alter table iam.consent_events force row level security;
alter table mpi.claim_attempts enable row level security;
alter table mpi.claim_attempts force row level security;
alter table mpi.claim_requests enable row level security;
alter table mpi.claim_requests force row level security;

-- notices are public text: any signed-in application session may read them; nobody writes them through the application role
create policy consent_notices_select on iam.consent_notices for select to careflow_app using (auth.uid() is not null);
-- a patient account reads and appends its OWN events, only against the CURRENT notice of the purpose
create policy consent_events_select_own on iam.consent_events for select to careflow_app using (account_id = auth.uid());
create policy consent_events_insert_own on iam.consent_events for insert to careflow_app
    with check (
        account_id = auth.uid()
        and exists (select 1 from iam.accounts a where a.id = auth.uid() and a.account_type = 'patient' and a.status in ('invited', 'active'))
        and notice_id = (select n.id from iam.consent_notices n where n.purpose = consent_events.purpose order by n.version desc limit 1)
    );
-- front desk of a clinic sees the pending claim requests of that clinic (resolving one is not built); the claimant sees their own
create policy claim_requests_select on mpi.claim_requests for select to careflow_app
    using (account_id = auth.uid() or iam.has_clinic_role(clinic_id, array['front_desk']));
-- claim_attempts: no policy at all. Only the claim function (owner) writes and reads it.

revoke all on iam.consent_notices, iam.consent_events, mpi.claim_attempts, mpi.claim_requests from public;
grant select on iam.consent_notices, iam.consent_events, mpi.claim_requests to careflow_app;
grant insert (account_id, purpose, notice_id, action, idempotency_key) on iam.consent_events to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on iam.consent_notices, iam.consent_events, mpi.claim_attempts, mpi.claim_requests from %I', r);
            execute format('revoke all on function iam.reject_mutation_generic() from %I', r);
        end if;
    end loop;
end $$;

-- (3)
create function iam.register_patient_account(p_origin text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_type  text;
begin
    if v_actor is null or p_origin is null or p_origin not in ('live', 'test', 'eval', 'canary') then
        raise exception 'patient registration refused' using errcode = 'insufficient_privilege';
    end if;
    select account_type into v_type from iam.accounts where id = v_actor;
    if found then
        if v_type <> 'patient' then
            raise exception 'patient registration refused' using errcode = 'insufficient_privilege';
        end if;
        return 'exists';  -- a repeat is the same registration
    end if;
    insert into iam.accounts (id, account_type, status, origin) values (v_actor, 'patient', 'invited', p_origin);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (p_origin, null, v_actor, 'patient', 'patient.register', 'account', v_actor::text, '{}'::jsonb, 'sys:patient-register:' || v_actor::text);
    return 'registered';
end
$$;

-- (4)
create function iam.activate_patient_account() returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_acc    record;
    v_latest text;
begin
    if v_actor is null then
        raise exception 'patient activation refused' using errcode = 'insufficient_privilege';
    end if;
    select account_type, status, origin into v_acc from iam.accounts where id = v_actor;
    if not found or v_acc.account_type <> 'patient' or v_acc.status not in ('invited', 'active') then
        raise exception 'patient activation refused' using errcode = 'insufficient_privilege';
    end if;
    if v_acc.status = 'active' then
        return 'already_active';
    end if;
    select e.action into v_latest
      from iam.consent_events e
      join iam.consent_notices n on n.id = e.notice_id
     where e.account_id = v_actor and e.purpose = 'care_delivery'
       and n.version = (select max(version) from iam.consent_notices where purpose = 'care_delivery')
     order by e.occurred_at desc, e.id desc limit 1;
    if v_latest is distinct from 'granted' then
        return 'consent_required';
    end if;
    update iam.accounts set status = 'active' where id = v_actor and status = 'invited';
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_acc.origin, null, v_actor, 'patient', 'patient.activate', 'account', v_actor::text, '{}'::jsonb, 'sys:patient-activate:' || v_actor::text)
    on conflict (idempotency_key) do nothing;
    return 'activated';
end
$$;

-- (5)
create function mpi.claim_patient_record(p_clinic_id uuid, p_phone_token bytea, p_name_dob_token bytea, p_key_version smallint) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_ids    uuid[];
    v_failed integer;
begin
    if v_actor is null or p_clinic_id is null or p_phone_token is null or p_name_dob_token is null or p_key_version is null or p_key_version < 1
       or octet_length(p_phone_token) <> 32 or octet_length(p_name_dob_token) <> 32 then
        raise exception 'patient claim refused' using errcode = 'insufficient_privilege';
    end if;
    select origin into v_origin from iam.accounts where id = v_actor and account_type = 'patient' and status = 'active';
    if v_origin is null or not exists (select 1 from iam.clinics where id = p_clinic_id and origin = v_origin) then
        raise exception 'patient claim refused' using errcode = 'insufficient_privilege';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('claim:' || v_actor::text, 0));
    if exists (select 1 from mpi.patients where account_id = v_actor) then
        return 'already_bound';
    end if;
    select count(*) into v_failed from mpi.claim_attempts where account_id = v_actor and occurred_at > clock_timestamp() - interval '24 hours';
    if v_failed >= 5 then
        return 'locked';
    end if;
    select array_agg(p.id) into v_ids
      from mpi.patients p
     where p.clinic_id = p_clinic_id and p.origin = v_origin and p.account_id is null and p.erased_at is null
       and exists (select 1 from mpi.identity_confirmations c where c.patient_id = p.id and c.clinic_id = p.clinic_id)
       and exists (select 1 from mpi.identity_tokens t where t.patient_id = p.id and t.clinic_id = p.clinic_id and t.kind = 'phone' and t.key_version = p_key_version and t.token = p_phone_token)
       and exists (select 1 from mpi.identity_tokens t where t.patient_id = p.id and t.clinic_id = p.clinic_id and t.kind = 'name_dob' and t.key_version = p_key_version and t.token = p_name_dob_token);
    if coalesce(array_length(v_ids, 1), 0) = 1 then
        update mpi.patients set account_id = v_actor where id = v_ids[1] and account_id is null;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_origin, p_clinic_id, v_actor, 'patient', 'patient.claim', 'patient', v_ids[1]::text, '{}'::jsonb, 'sys:patient-claim:' || v_actor::text);
        return 'claimed';
    end if;
    -- no single confirmed record agrees on BOTH: count it, and leave a request for the front desk (one pending per account and clinic). The same word for none and for several.
    insert into mpi.claim_attempts (account_id, clinic_id) values (v_actor, p_clinic_id);
    insert into mpi.claim_requests (clinic_id, origin, account_id) values (p_clinic_id, v_origin, v_actor) on conflict do nothing;
    return 'pending_review';
end
$$;

do $$
declare
    f text;
    r text;
begin
    foreach f in array array['iam.register_patient_account(text)', 'iam.activate_patient_account()', 'mpi.claim_patient_record(uuid,bytea,bytea,smallint)'] loop
        execute format('revoke all on function %s from public', f);
        execute format('grant execute on function %s to careflow_app', f);
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
        if has_function_privilege('public', f, 'execute') then
            raise exception '0049: % must not be executable by PUBLIC', f;
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'mpi.claim_attempts', 'select') or has_table_privilege('careflow_app', 'mpi.claim_attempts', 'insert')
       or has_table_privilege('careflow_app', 'iam.consent_notices', 'insert') or has_table_privilege('careflow_app', 'iam.consent_events', 'update')
       or has_table_privilege('careflow_app', 'mpi.claim_requests', 'insert') then
        raise exception '0049: the application role holds a privilege it must not';
    end if;
    if has_function_privilege('careflow_app', 'iam.reject_mutation_generic()', 'execute') then
        raise exception '0049: the guard function must not be executable by the application role';
    end if;
end $$;
