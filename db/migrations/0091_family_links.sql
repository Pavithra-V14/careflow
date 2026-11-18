-- 0091_family_links (owner instruction 2026-11-14, deferred item 21; BUILD-SPEC.md 17.2, 20.1; CLAUDE.md rules 1, 3, 5, 6, 8, 10). MECHANISM ONLY, OFF unless the owner sets CAREFLOW_FAMILY_LINKS=on, and DENY BY DEFAULT.
--
-- # CLINICAL-PLACEHOLDER: needs counsel sign-off (the age limits and the consent rule below are counsel's, not ours)
--
-- A patient account can ask to be linked as the guardian of a child or the carer of an adult at a clinic; a FRONT-DESK person of the clinic verifies the link (choosing the dependant's record, checking the dependant's age
-- against the clinic's rule, and recording that the linked person's consent was obtained, with a verified second factor); the patient can revoke it. A VERIFIED LINK GRANTS NOTHING in this build: no route lets one account
-- read or act for another (that is a later decision); this only records who is linked to whom, verified by whom, with consent recorded.
--   * iam.family_link_rules   OWNER-MANAGED, SHIPS EMPTY: per clinic and link type, the youngest and oldest dependant age allowed and whether the linked person's consent is required. COUNSEL'S VALUES, not ours:
--                             there is no default and no seed. With no row for a clinic and link type, no link can be requested or verified (`not_configured`, deny by default). RLS forced, no policy, no grant.
--   * iam.family_links        the links. RLS forced, no policy, no grant. A link moves only forward (requested, then verified, rejected or revoked) and is never deleted.
-- The patient functions check the patient's own sign-in (a phone code, no staff second factor); the staff functions need aal2. The dependant's age is computed by the application from the stored (encrypted) date of birth and
-- passed in; the database checks it against the rule. One 42501 for every refusal.
--
-- NEW GRANTS (ledger): EXECUTE on the five functions to careflow_app. Two new tables: no grant, no policy.

create table iam.family_link_rules (
    clinic_id                   uuid not null references iam.clinics (id),
    link_type                   text not null check (link_type in ('guardian_of_minor', 'carer_of_adult')),
    min_dependant_age_years     smallint check (min_dependant_age_years between 0 and 130),
    max_dependant_age_years     smallint check (max_dependant_age_years between 0 and 130),
    requires_dependant_consent  boolean not null,
    set_at                      timestamptz not null default clock_timestamp(),
    primary key (clinic_id, link_type),
    check (min_dependant_age_years is null or max_dependant_age_years is null or min_dependant_age_years <= max_dependant_age_years)
);

create table iam.family_links (
    id                       uuid primary key default gen_random_uuid(),
    clinic_id                uuid not null references iam.clinics (id),
    origin                   text not null check (origin in ('live', 'test', 'eval', 'canary')),
    requester_account_id     uuid not null references iam.accounts (id),
    requester_patient_id     uuid not null,
    link_type                text not null check (link_type in ('guardian_of_minor', 'carer_of_adult')),
    status                   text not null default 'requested' check (status in ('requested', 'verified', 'rejected', 'revoked')),
    dependant_patient_id     uuid,
    dependant_age_years      smallint check (dependant_age_years between 0 and 130),
    consent_recorded_at      timestamptz,
    decided_by               uuid references iam.accounts (id),
    decided_at               timestamptz,
    reject_reason            text check (reject_reason ~ '^[a-z][a-z0-9_]{0,59}$'),
    revoked_at               timestamptz,
    created_at               timestamptz not null default clock_timestamp(),
    idempotency_key          text not null check (length(idempotency_key) between 1 and 300),
    decide_key               text check (length(decide_key) between 1 and 300),
    revoke_key               text check (length(revoke_key) between 1 and 300),
    constraint family_links_key_per_clinic unique (clinic_id, idempotency_key),
    foreign key (requester_patient_id, clinic_id, origin) references mpi.patients (id, clinic_id, origin),
    foreign key (dependant_patient_id, clinic_id, origin) references mpi.patients (id, clinic_id, origin),
    check (dependant_patient_id is null or dependant_patient_id <> requester_patient_id),
    check (status <> 'verified' or (dependant_patient_id is not null and dependant_age_years is not null and decided_by is not null and decided_at is not null)),
    check ((status = 'rejected') = (reject_reason is not null)),
    check ((status = 'revoked') = (revoked_at is not null))
);
create unique index family_links_one_requested on iam.family_links (clinic_id, requester_account_id, link_type) where status = 'requested';
create unique index family_links_one_verified on iam.family_links (clinic_id, requester_account_id, dependant_patient_id) where status = 'verified';

do $$
declare t text; r text;
begin
    foreach t in array array['iam.family_link_rules', 'iam.family_links'] loop
        execute format('alter table %s enable row level security', t);
        execute format('alter table %s force row level security', t);
        execute format('revoke all on %s from public', t);
        foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on %s from %I', t, r);
            end if;
        end loop;
    end loop;
end $$;

create function iam.family_links_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op in ('DELETE', 'TRUNCATE') then
        raise exception 'iam.family_links rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if (old.id, old.clinic_id, old.origin, old.requester_account_id, old.requester_patient_id, old.link_type, old.created_at, old.idempotency_key)
       is distinct from (new.id, new.clinic_id, new.origin, new.requester_account_id, new.requester_patient_id, new.link_type, new.created_at, new.idempotency_key) then
        raise exception 'the identity of a link never changes' using errcode = 'restrict_violation';
    end if;
    if old.dependant_patient_id is not null and new.dependant_patient_id is distinct from old.dependant_patient_id then
        raise exception 'the dependant of a link is set once' using errcode = 'restrict_violation';
    end if;
    if not ((old.status = 'requested' and new.status in ('requested', 'verified', 'rejected', 'revoked')) or (old.status = 'verified' and new.status in ('verified', 'revoked'))
            or (old.status = new.status and old.status in ('rejected', 'revoked'))) then
        raise exception 'a link moves only forward: requested, then verified, rejected or revoked' using errcode = 'restrict_violation';
    end if;
    if old.status in ('rejected', 'revoked') and (new.* is distinct from old.*) then
        raise exception 'a closed link never changes' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;
create trigger family_links_guard_update before update on iam.family_links for each row execute function iam.family_links_guard();
create trigger family_links_guard_delete before delete on iam.family_links for each row execute function iam.family_links_guard();
create trigger family_links_guard_truncate before truncate on iam.family_links for each statement execute function iam.family_links_guard();
alter table iam.family_links enable always trigger family_links_guard_update;
alter table iam.family_links enable always trigger family_links_guard_delete;
alter table iam.family_links enable always trigger family_links_guard_truncate;

-- ---- a patient asks for a link --------------------------------------------------------------------------------------------------------------------

create function iam.family_link_request(p_clinic_id uuid, p_link_type text, p_idempotency_key text)
    returns table (o_result text, o_link_id uuid)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_pt    record;
    v_key   text;
    v_prior record;
    v_id    uuid;
begin
    if v_actor is null or p_clinic_id is null or p_link_type is null or p_link_type not in ('guardian_of_minor', 'carer_of_adult') or p_idempotency_key is null
       or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'link refused' using errcode = '42501';
    end if;
    select pt.id, pt.origin into v_pt
      from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
     where pt.account_id = v_actor and pt.clinic_id = p_clinic_id and pt.erased_at is null order by pt.created_at limit 1;
    if not found then
        raise exception 'link refused' using errcode = '42501';
    end if;
    if not exists (select 1 from iam.family_link_rules r where r.clinic_id = p_clinic_id and r.link_type = p_link_type) then
        return query select 'not_configured'::text, null::uuid;  -- deny by default: counsel's rules are not entered
        return;
    end if;
    v_key := 'family-link:' || v_actor::text || ':' || p_idempotency_key;
    select l.id, l.link_type into v_prior from iam.family_links l where l.clinic_id = p_clinic_id and l.idempotency_key = v_key;
    if found then
        if v_prior.link_type <> p_link_type then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select 'replay'::text, v_prior.id;
        return;
    end if;
    if exists (select 1 from iam.family_links l where l.clinic_id = p_clinic_id and l.requester_account_id = v_actor and l.link_type = p_link_type and l.status = 'requested') then
        return query select 'already_requested'::text, null::uuid;
        return;
    end if;
    insert into iam.family_links (clinic_id, origin, requester_account_id, requester_patient_id, link_type, idempotency_key)
    values (p_clinic_id, v_pt.origin, v_actor, v_pt.id, p_link_type, v_key) returning id into v_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_pt.origin, p_clinic_id, v_actor, 'patient', 'iam.family_link.requested', 'iam.family_links', v_id::text, jsonb_build_object('link_type', p_link_type), 'sys:family-link:' || gen_random_uuid()::text);
    return query select 'recorded'::text, v_id;
end
$$;

create function iam.patient_family_links()
    returns table (o_link_id uuid, o_clinic_id uuid, o_link_type text, o_status text, o_created_at timestamptz)
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or not exists (select 1 from iam.accounts a where a.id = auth.uid() and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'links refused' using errcode = '42501';
    end if;
    return query select l.id, l.clinic_id, l.link_type, l.status, l.created_at from iam.family_links l where l.requester_account_id = auth.uid() order by l.created_at desc, l.id limit 100;
end
$$;

create function iam.family_link_revoke(p_link_id uuid, p_idempotency_key text)
    returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_l     record;
begin
    if v_actor is null or p_link_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'revoke refused' using errcode = '42501';
    end if;
    select l.id, l.status, l.revoke_key, l.origin, l.clinic_id, l.requester_account_id into v_l from iam.family_links l where l.id = p_link_id for update;
    if not found or v_l.requester_account_id <> v_actor or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'revoke refused' using errcode = '42501';  -- not theirs, or missing: one answer
    end if;
    if v_l.status = 'revoked' and v_l.revoke_key = 'family-revoke:' || p_idempotency_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_l.status not in ('requested', 'verified') then
        return query select 'invalid_state'::text;
        return;
    end if;
    update iam.family_links set status = 'revoked', revoked_at = clock_timestamp(), revoke_key = 'family-revoke:' || p_idempotency_key where id = p_link_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_l.origin, v_l.clinic_id, v_actor, 'patient', 'iam.family_link.revoked', 'iam.family_links', p_link_id::text, '{}'::jsonb, 'sys:family-link:' || gen_random_uuid()::text);
    return query select 'recorded'::text;
end
$$;

-- ---- the front desk ------------------------------------------------------------------------------------------------------------------------------------

create function iam.staff_family_links(p_clinic_id uuid)
    returns table (o_link_id uuid, o_link_type text, o_status text, o_requester_patient_id uuid, o_dependant_patient_id uuid, o_created_at timestamptz)
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['front_desk']) then
        raise exception 'links refused' using errcode = '42501';
    end if;
    return query
        select l.id, l.link_type, l.status, l.requester_patient_id, l.dependant_patient_id, l.created_at
          from iam.family_links l where l.clinic_id = p_clinic_id and l.status in ('requested', 'verified') order by l.created_at, l.id limit 200;
end
$$;

create function iam.family_link_decide(
    p_clinic_id uuid, p_link_id uuid, p_decision text, p_dependant_patient_id uuid, p_dependant_age_years integer, p_dependant_consent boolean, p_reason text, p_idempotency_key text
) returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_l     record;
    v_rule  record;
begin
    if v_actor is null or p_clinic_id is null or p_link_id is null or p_decision is null or p_decision not in ('verify', 'reject') or p_idempotency_key is null
       or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['front_desk'])
       or (p_decision = 'reject' and (p_reason is null or p_reason !~ '^[a-z][a-z0-9_]{0,59}$'))
       or (p_decision = 'verify' and (p_dependant_patient_id is null or p_dependant_age_years is null or p_dependant_age_years not between 0 and 130 or p_dependant_consent is null or p_reason is not null)) then
        raise exception 'decision refused' using errcode = '42501';
    end if;
    select l.* into v_l from iam.family_links l where l.id = p_link_id and l.clinic_id = p_clinic_id for update;
    if not found then
        raise exception 'decision refused' using errcode = '42501';
    end if;
    if v_l.status in ('verified', 'rejected') and v_l.decide_key = 'family-decide:' || p_idempotency_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_l.status <> 'requested' then
        return query select 'invalid_state'::text;
        return;
    end if;
    if p_decision = 'reject' then
        update iam.family_links set status = 'rejected', reject_reason = p_reason, decided_by = v_actor, decided_at = clock_timestamp(), decide_key = 'family-decide:' || p_idempotency_key where id = p_link_id;
    else
        select r.min_dependant_age_years, r.max_dependant_age_years, r.requires_dependant_consent into v_rule from iam.family_link_rules r where r.clinic_id = p_clinic_id and r.link_type = v_l.link_type;
        if not found then
            return query select 'not_configured'::text;  -- deny by default
            return;
        end if;
        if not exists (select 1 from mpi.patients pt where pt.id = p_dependant_patient_id and pt.clinic_id = p_clinic_id and pt.origin = v_l.origin and pt.erased_at is null)
           or p_dependant_patient_id = v_l.requester_patient_id then
            raise exception 'decision refused' using errcode = '42501';  -- another clinic's record, an erased one, or the requester's own
        end if;
        if (v_rule.min_dependant_age_years is not null and p_dependant_age_years < v_rule.min_dependant_age_years) or (v_rule.max_dependant_age_years is not null and p_dependant_age_years > v_rule.max_dependant_age_years) then
            return query select 'age_not_allowed'::text;
            return;
        end if;
        if v_rule.requires_dependant_consent and not p_dependant_consent then
            return query select 'consent_required'::text;
            return;
        end if;
        update iam.family_links set status = 'verified', dependant_patient_id = p_dependant_patient_id, dependant_age_years = p_dependant_age_years,
               consent_recorded_at = case when p_dependant_consent then clock_timestamp() else null end, decided_by = v_actor, decided_at = clock_timestamp(), decide_key = 'family-decide:' || p_idempotency_key
         where id = p_link_id;
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_l.origin, p_clinic_id, v_actor, 'front_desk', 'iam.family_link.' || p_decision, 'iam.family_links', p_link_id::text, jsonb_build_object('link_type', v_l.link_type), 'sys:family-link:' || gen_random_uuid()::text);
    return query select 'recorded'::text;
end
$$;

revoke all on function iam.family_link_request(uuid, text, text), iam.patient_family_links(), iam.family_link_revoke(uuid, text), iam.staff_family_links(uuid),
    iam.family_link_decide(uuid, uuid, text, uuid, integer, boolean, text, text) from public;
grant execute on function iam.family_link_request(uuid, text, text), iam.patient_family_links(), iam.family_link_revoke(uuid, text), iam.staff_family_links(uuid),
    iam.family_link_decide(uuid, uuid, text, uuid, integer, boolean, text, text) to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.family_link_request(uuid, text, text), iam.patient_family_links(), iam.family_link_revoke(uuid, text), iam.staff_family_links(uuid), '
                           'iam.family_link_decide(uuid, uuid, text, uuid, integer, boolean, text, text) from %I', r);
        end if;
    end loop;
end $$;
