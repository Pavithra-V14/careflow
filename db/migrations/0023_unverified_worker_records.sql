-- 0023_unverified_worker_records: pre-launch blocker 10 (planted tokens), option A (owner-approved 2026-10-28). Design: docs/design/planted-token-design.md. CLAUDE.md rules 5, 8, 10.
-- Depends on 0022 (the registration function), which is not merged when this is written; this file never edits a merged migration.
--
-- THE RISK. The worker computes identity tokens itself, so it can register a junk record carrying the token of a person who is NOT registered yet; the person's real referral would
-- then find exactly one record agreeing on both strong identifiers and be linked to it confidently and silently.
--
-- THE FIX, in three parts.
--   1. mpi.patients.registered_via ('front_desk' | 'referral_worker'), NOT NULL, default 'front_desk' (so every existing and every direct front-desk row is 'front_desk'). The
--      registration function (re-created below) writes 'referral_worker'; it has no parameter for it, and the application role has NO column grant on it (0012 made INSERT and
--      UPDATE column-level, "columns added later receive no grant"), so no application path can set or change it.
--   2. mpi.identity_confirmations: an APPEND-ONLY record that a front-desk person confirmed a patient's identity. A patient counts as VERIFIED when registered_via = 'front_desk'
--      or a confirmation exists; the patient row itself is never updated. Front desk inserts it (policy: front desk of the clinic, confirmed_by = auth.uid()), and reads it with
--      nurse, doctor and queue worker (the matcher needs to read it). One confirmation per patient; every write carries an idempotency key.
--   3. The matcher (careflow/mpi.py) no longer treats an UNVERIFIED record as a confident match: it is a review, a person decides.
--
-- NO nurse approval of a triage proposal confirms identity (owner decision): approving triage does not check who the person is. That rule is enforced by the route, not by the
-- database: the database accepts a confirmation from any front-desk user of the clinic.
--
-- RESIDUALS (stated; reviewed by rls-security-reviewer 2026-10-28):
--   * ROWS THAT EXISTED BEFORE 0023 count as front-desk-created ('front_desk' default). Nothing in the schema says who created an older row, so worker-created rows written under 0019
--     (before 0022) are NOT demoted, and a token planted on one would still auto-link. The owner must confirm no such live rows exist before launch, or run a reviewed one-off.
--   * A confirmation records only that some front-desk user of the clinic confirmed; it captures no evidence, and being append-only it cannot be undone (a mistaken one is fixed
--     forward by a merge or an erasure). The database does not check that the patient has no pending candidate.
--   * If front desk merges an unverified record into a verified master, the unverified record's tokens become verified (the matcher takes the master's status). A deliberate human act, but
--     a worker can steer a reviewer towards it by queueing a near-match against a verified patient.
--   * A planted token now causes a visible review for the real person (a flood vector; rate limiting belongs to Layer 10). Unverified records stay unverified until someone acts, so
--     repeat referrals keep going to a person (accepted until a console exists).
--   * The table owner can disable the append-only triggers (the same residual as 0012). A worker account that also holds front_desk keeps the direct INSERTs: migration 0024.
--
-- GRANTS. mpi.identity_confirmations: INSERT and SELECT to careflow_app (row-level security decides), nothing else; the owner is also blocked from UPDATE/DELETE/TRUNCATE by a trigger.
-- The re-created function keeps its signature, its owner, its search_path pin and its EXECUTE grant (create or replace does not reset them); the grants are restated below.

alter table mpi.patients add column registered_via text not null default 'front_desk' check (registered_via in ('front_desk', 'referral_worker'));

create table mpi.identity_confirmations (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null,
    patient_id      uuid not null,
    confirmed_by    uuid not null references iam.accounts (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at      timestamptz not null default now(),
    idempotency_key text not null unique check (length(btrim(idempotency_key)) > 0),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    unique (patient_id)
);

create function mpi.reject_confirmation_mutation() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'mpi.identity_confirmations is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;
create trigger identity_confirmations_no_update_delete before update or delete on mpi.identity_confirmations
    for each row execute function mpi.reject_confirmation_mutation();
create trigger identity_confirmations_no_truncate before truncate on mpi.identity_confirmations
    for each statement execute function mpi.reject_confirmation_mutation();

-- The confirmation's origin must be the patient's own (the composite FK pins only patient and clinic). A trigger, not a CHECK: it has to read the patient row.
create function mpi.confirmation_origin_matches_patient() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if not exists (select 1 from mpi.patients p where p.id = new.patient_id and p.clinic_id = new.clinic_id and p.origin = new.origin) then
        raise exception 'a confirmation carries the origin of its patient' using errcode = '23514';
    end if;
    return new;
end
$$;
create trigger identity_confirmations_origin before insert on mpi.identity_confirmations
    for each row execute function mpi.confirmation_origin_matches_patient();
revoke all on function mpi.confirmation_origin_matches_patient() from public, careflow_app;

alter table mpi.identity_confirmations enable row level security;
alter table mpi.identity_confirmations force row level security;

create policy identity_confirmations_select on mpi.identity_confirmations for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'queue_worker']));
create policy identity_confirmations_insert_front_desk on mpi.identity_confirmations for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']) and confirmed_by = auth.uid());

revoke all on mpi.identity_confirmations from public;
revoke all on mpi.identity_confirmations from careflow_app;
grant insert, select on mpi.identity_confirmations to careflow_app;
revoke all on function mpi.reject_confirmation_mutation() from public, careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on mpi.identity_confirmations from %I', r);
            execute format('revoke all on function mpi.reject_confirmation_mutation() from %I', r);
            execute format('revoke all on function mpi.confirmation_origin_matches_patient() from %I', r);
        end if;
    end loop;
end $$;

-- The registration function of 0022, re-created with ONE change: the new patient is marked 'referral_worker'. Signature, SECURITY DEFINER, search_path = '' and the caller check
-- are unchanged.
create or replace function mpi.register_patient_from_referral(
    p_clinic uuid,
    p_origin text,
    p_name_ct bytea,
    p_name_kv smallint,
    p_dob_ct bytea,
    p_dob_kv smallint,
    p_phone_ct bytea,
    p_phone_kv smallint,
    p_idempotency_key text,
    p_token_kinds text[],
    p_tokens bytea[],
    p_token_kv smallint,
    p_candidates jsonb
) returns uuid
    language plpgsql security definer set search_path = ''
as $$
declare
    v_patient uuid;
    v_n integer;
    v_item jsonb;
    v_other uuid;
    v_kinds text[];
    v_reason text;
begin
    if p_clinic is null or not iam.has_clinic_role(p_clinic, array['queue_worker']) then
        raise exception 'this role may not register patients in this clinic' using errcode = '42501';
    end if;
    if p_origin is null or p_origin not in ('live', 'test', 'eval', 'canary') then
        raise exception 'origin must be one of live, test, eval, canary' using errcode = '22023';
    end if;
    if p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 then
        raise exception 'an idempotency key is required on every write' using errcode = '22023';
    end if;

    v_n := coalesce(cardinality(p_token_kinds), 0);
    if v_n < 1 or v_n > 2 or v_n <> coalesce(cardinality(p_tokens), -1) or p_token_kv is null or p_token_kv < 1 then
        raise exception 'identity tokens must be one or two kind/value pairs with a key version' using errcode = '22023';
    end if;
    if exists (select 1 from unnest(p_token_kinds) as k where k is null or k not in ('name_dob', 'phone'))
       or (select count(distinct k) from unnest(p_token_kinds) as k) <> v_n
       or exists (select 1 from unnest(p_tokens) as t where t is null or octet_length(t) = 0) then
        raise exception 'identity token kinds must be distinct members of the closed set, each with a non-empty value' using errcode = '22023';
    end if;

    if p_candidates is null or jsonb_typeof(p_candidates) is distinct from 'array' or jsonb_array_length(p_candidates) > 20 then
        raise exception 'candidates must be a JSON array of at most 20 entries' using errcode = '22023';
    end if;
    if (select count(distinct x ->> 'patient_id') from jsonb_array_elements(p_candidates) as x) <> jsonb_array_length(p_candidates) then
        raise exception 'each candidate must name a different patient' using errcode = '22023';
    end if;

    insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key, registered_via)
    values (p_clinic, p_name_ct, p_name_kv, p_dob_ct, p_dob_kv, p_phone_ct, p_phone_kv, p_origin, p_idempotency_key, 'referral_worker')
    returning id into v_patient;

    insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin)
    select v_patient, p_clinic, k.kind, t.token, p_token_kv, p_origin
      from unnest(p_token_kinds) with ordinality as k(kind, i)
      join unnest(p_tokens) with ordinality as t(token, i) using (i);

    for v_item in select * from jsonb_array_elements(p_candidates) loop
        if jsonb_typeof(v_item) is distinct from 'object' or jsonb_typeof(v_item -> 'patient_id') is distinct from 'string'
           or jsonb_typeof(v_item -> 'kinds') is distinct from 'array' or jsonb_typeof(v_item -> 'reason') is distinct from 'string' then
            raise exception 'each candidate needs patient_id, kinds and reason' using errcode = '22023';
        end if;
        if (v_item ->> 'patient_id') !~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
            raise exception 'a candidate patient_id must be a uuid' using errcode = '22023';
        end if;
        v_other := (v_item ->> 'patient_id')::uuid;
        v_reason := v_item ->> 'reason';
        select array_agg(e) into v_kinds from jsonb_array_elements_text(v_item -> 'kinds') as e;
        if v_reason not in ('near_match', 'ambiguous') or coalesce(cardinality(v_kinds), 0) = 0 then
            raise exception 'a candidate needs a reason of near_match or ambiguous and at least one matched kind' using errcode = '22023';
        end if;
        if cardinality(v_kinds) > 2 or exists (select 1 from unnest(v_kinds) as k where k is null or k not in ('name_dob', 'phone'))
           or (select count(distinct k) from unnest(v_kinds) as k) <> cardinality(v_kinds) then
            raise exception 'matched kinds must be distinct members of the closed set' using errcode = '22023';
        end if;
        if v_other = v_patient or not exists (
            select 1 from mpi.patients p
             where p.id = v_other and p.clinic_id = p_clinic and p.origin = p_origin and p.erased_at is null
        ) then
            raise exception 'a possible duplicate must be another live patient of this clinic and origin' using errcode = '22023';
        end if;
        insert into mpi.match_candidates (clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, status, origin, idempotency_key)
        values (p_clinic, v_patient, v_other, v_kinds, v_reason, 'pending', p_origin, 'cand:' || p_idempotency_key || ':' || v_other::text);
    end loop;

    return v_patient;
end
$$;

revoke all on function mpi.register_patient_from_referral(uuid, text, bytea, smallint, bytea, smallint, bytea, smallint, text, text[], bytea[], smallint, jsonb) from public;
grant execute on function mpi.register_patient_from_referral(uuid, text, bytea, smallint, bytea, smallint, bytea, smallint, text, text[], bytea[], smallint, jsonb) to careflow_app;
