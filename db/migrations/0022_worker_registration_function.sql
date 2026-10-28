-- 0022_worker_registration_function: close pre-launch blocker 1 (0019's residual risk 4). Design: docs/design/blocker1-worker-token-forgery.md (owner-approved 2026-10-28).
-- CLAUDE.md rules 5, 8, 10 (this is a NEW migration; 0019 is merged and is never edited).
--
-- THE ATTACK. 0019 gave the queue worker INSERT on mpi.patients, mpi.identity_tokens and mpi.match_candidates. The foreign keys pin only the clinic, so a worker (a bug in it, a
-- compromise of it, anything holding its token) could attach an identity token to ANY existing patient of its clinic (a later referral then links to the wrong person), forge a
-- pending possible-duplicate between two existing patients, and choose row ids.
--
-- THE FIX. mpi.register_patient_from_referral is now the ONLY way the worker writes these three tables:
--   * it takes no patient id: it creates ONE new patient (the database generates the id), attaches the tokens to THAT patient only, and records possible-duplicates whose
--     patient_id side is that patient only. So "attach a token to an existing patient" and "a candidate between two existing patients" cannot be expressed;
--   * it checks the caller is a queue_worker of the clinic (iam.has_clinic_role, which reads the verified claims), else 42501;
--   * each candidate must be an existing, non-erased patient of the same clinic and origin, and never the new patient; status is forced to 'pending';
--   * bounds: at most 2 tokens (kinds name_dob / phone, distinct), at most 20 candidates, a non-blank idempotency key, origin from the allowed set;
--   * a repeated idempotency key raises the existing unique violation (the caller's replay check normally answers first); candidate keys derive from it ('cand:<key>:<other>').
-- It is SECURITY DEFINER with search_path = '' (everything schema-qualified), owned by the migrating role, which bypasses RLS as msg.record_screening does (0013).
--
-- GRANTS. EXECUTE is revoked from PUBLIC and from the Supabase API roles and granted to careflow_app only (the DB role every authenticated request uses; WHO may call it is decided
-- inside the function by the claims). The worker's three INSERT policies from 0019 are DROPPED; its SELECT policies stay (matching reads). Net: the worker has FEWER privileges than
-- before. Front desk is unchanged: it keeps inserting directly under its own policies (a person's authenticated request).
--
-- RESIDUAL (stated). A worker can still create new patient records (that is its job) and compute tokens (it holds the vault key). A record carrying a real person's token makes
-- the matcher see two records with that token, which classify() treats as ambiguous and sends to a person: a surfaced review, not a silent wrong link.

create function mpi.register_patient_from_referral(
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

    -- tokens: 1 or 2, kinds distinct and from the closed set, one value per kind
    v_n := coalesce(cardinality(p_token_kinds), 0);
    if v_n < 1 or v_n > 2 or v_n <> coalesce(cardinality(p_tokens), -1) or p_token_kv is null or p_token_kv < 1 then
        raise exception 'identity tokens must be one or two kind/value pairs with a key version' using errcode = '22023';
    end if;
    if exists (select 1 from unnest(p_token_kinds) as k where k is null or k not in ('name_dob', 'phone'))
       or (select count(distinct k) from unnest(p_token_kinds) as k) <> v_n
       or exists (select 1 from unnest(p_tokens) as t where t is null or octet_length(t) = 0) then
        raise exception 'identity token kinds must be distinct members of the closed set, each with a non-empty value' using errcode = '22023';
    end if;

    -- candidates: a JSON array of at most 20 objects
    if p_candidates is null or jsonb_typeof(p_candidates) is distinct from 'array' or jsonb_array_length(p_candidates) > 20 then
        raise exception 'candidates must be a JSON array of at most 20 entries' using errcode = '22023';
    end if;

    -- ONE new patient. The id is generated by the database; nothing here accepts a patient id. The table's own CHECKs (a live record needs a name and a date of birth, ciphertext and
    -- key version together) apply as for any insert.
    insert into mpi.patients (clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, origin, idempotency_key)
    values (p_clinic, p_name_ct, p_name_kv, p_dob_ct, p_dob_kv, p_phone_ct, p_phone_kv, p_origin, p_idempotency_key)
    returning id into v_patient;

    -- the tokens belong to THAT patient and no other
    insert into mpi.identity_tokens (patient_id, clinic_id, kind, token, key_version, origin)
    select v_patient, p_clinic, k.kind, t.token, p_token_kv, p_origin
      from unnest(p_token_kinds) with ordinality as k(kind, i)
      join unnest(p_tokens) with ordinality as t(token, i) using (i);

    -- possible-duplicates: patient_id is always the new patient; the other side must exist in this clinic, share the origin, be live, and differ from the new patient
    for v_item in select * from jsonb_array_elements(p_candidates) loop
        if jsonb_typeof(v_item) is distinct from 'object' or jsonb_typeof(v_item -> 'patient_id') is distinct from 'string'
           or jsonb_typeof(v_item -> 'kinds') is distinct from 'array' or jsonb_typeof(v_item -> 'reason') is distinct from 'string' then
            raise exception 'each candidate needs patient_id, kinds and reason' using errcode = '22023';
        end if;
        v_other := (v_item ->> 'patient_id')::uuid;
        v_reason := v_item ->> 'reason';
        select array_agg(e) into v_kinds from jsonb_array_elements_text(v_item -> 'kinds') as e;
        if v_reason not in ('near_match', 'ambiguous') or coalesce(cardinality(v_kinds), 0) = 0 then
            raise exception 'a candidate needs a reason of near_match or ambiguous and at least one matched kind' using errcode = '22023';
        end if;
        if v_other = v_patient or not exists (
            select 1 from mpi.patients p
             where p.id = v_other and p.clinic_id = p_clinic and p.origin = p_origin and p.erased_at is null
        ) then
            raise exception 'a possible duplicate must be another live patient of this clinic and origin' using errcode = '42501';
        end if;
        insert into mpi.match_candidates (clinic_id, patient_id, candidate_patient_id, matched_kinds, reason, status, origin, idempotency_key)
        values (p_clinic, v_patient, v_other, v_kinds, v_reason, 'pending', p_origin, 'cand:' || p_idempotency_key || ':' || v_other::text);
    end loop;

    return v_patient;
end
$$;

revoke all on function mpi.register_patient_from_referral(uuid, text, bytea, smallint, bytea, smallint, bytea, smallint, text, text[], bytea[], smallint, jsonb) from public;
grant execute on function mpi.register_patient_from_referral(uuid, text, bytea, smallint, bytea, smallint, bytea, smallint, text, text[], bytea[], smallint, jsonb) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format(
                'revoke all on function mpi.register_patient_from_referral(uuid, text, bytea, smallint, bytea, smallint, bytea, smallint, text, text[], bytea[], smallint, jsonb) from %I', r);
        end if;
    end loop;
end $$;

-- the worker no longer writes these three tables directly (0019's INSERT policies are removed; its SELECT policies stay)
drop policy patients_insert_worker on mpi.patients;
drop policy tokens_insert_worker on mpi.identity_tokens;
drop policy match_candidates_insert_worker on mpi.match_candidates;
