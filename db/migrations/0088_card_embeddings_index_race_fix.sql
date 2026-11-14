-- 0088_card_embeddings_index_race_fix (rls-security-reviewer report on 0086 and 0087, 2026-11-14; CLAUDE.md rules 3, 5, 10). Fix forward: 0085, 0086 and 0087 are merged and are NOT edited.
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off  (no clinical content: ids, hashes, numbers)
--
--   * F1 (Medium) 0087's handler for a LOST INSERT RACE re-read by key OR by card/version/model and compared only the hash, audience and vector, so the same key used at the same moment for two different cards with
--     identical content could answer "already indexed" for a card that was never written. It now compares the card id, version and model exactly as the pre-insert replay check does, and prefers the row that
--     matches the key.
--   * F2 (Medium/Low) a replay required the vector to be EXACTLY equal, so a hosted embedding model that returns slightly different numbers for the same text would turn a harmless re-run into a conflict. A replay now
--     accepts a vector within a cosine distance of 0.001 of the stored one (the same text); a genuinely different vector is still a conflict (23505). The tolerance is a technical number, not a clinical one.
--   * F5 (Low) 0087 asserted that the MIGRATOR bypasses row-level security; the functions run as their OWNER. This asserts it for the owner of each of the three functions (and the table's owner) at apply time.
-- The function is REPLACED with the same signature, so the EXECUTE grant to careflow_app stays.
--
-- NEW GRANTS (ledger): none.

do $$
begin
    if exists (
        select 1
          from (values ('kb.index_card_embedding(uuid, text, text, integer, text, text[], text, text, text)'),
                       ('kb.search_card_embeddings(uuid, text, text, text, integer)'),
                       ('kb.current_card_versions(uuid, text, text, text[])')) as f (sig)
          join pg_proc p on p.oid = to_regprocedure(f.sig)
          join pg_roles r on r.oid = p.proowner
         where not (r.rolsuper or r.rolbypassrls)
    ) or exists (
        select 1 from pg_class c join pg_roles r on r.oid = c.relowner
         where c.oid = 'kb.card_embeddings'::regclass and not (r.rolsuper or r.rolbypassrls)
    ) then
        raise exception '0088 needs the owner of kb.card_embeddings and of its three functions to bypass row-level security (the definer functions rely on it)';
    end if;
end $$;

create or replace function kb.index_card_embedding(
    p_clinic_id uuid, p_origin text, p_card_id text, p_card_version integer, p_model_id text, p_audience text[], p_embedding text, p_content_sha256 text, p_idempotency_key text
) returns table (o_id uuid, o_created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_clinic_origin text;
    v_key    text;
    v_prior  record;
    v_id     uuid;
    v_vec    extensions.vector(768);
    v_aud    text[];
    v_max    integer;
begin
    if v_actor is null or p_clinic_id is null or p_origin is null or p_card_id is null or p_card_version is null or p_model_id is null or p_audience is null or p_embedding is null
       or p_content_sha256 is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or length(p_embedding) > 30000 or length(p_card_id) > 100 or length(p_model_id) > 100 or cardinality(p_audience) > 5
       or not iam.has_clinic_role(p_clinic_id, array['queue_worker', 'admin']) then
        raise exception 'index refused' using errcode = '42501';
    end if;
    select c.origin into v_clinic_origin from iam.clinics c where c.id = p_clinic_id;
    if v_clinic_origin is distinct from p_origin then
        raise exception 'index refused' using errcode = '42501';  -- a row never carries an origin other than its clinic's
    end if;
    begin
        v_vec := p_embedding::extensions.vector(768);  -- passed as TEXT: the caller never needs USAGE on the extensions schema
    exception when invalid_text_representation or data_exception or numeric_value_out_of_range then
        raise exception 'index refused' using errcode = '42501';
    end;
    if (v_vec operator(extensions.<#>) v_vec) = 0 then
        raise exception 'index refused' using errcode = '42501';  -- an all-zero vector can never be compared
    end if;
    v_aud := array(select distinct x from unnest(p_audience) as x order by x);
    v_key := 'card-embedding:' || p_idempotency_key;
    select e.id, e.card_id, e.card_version, e.model_id, e.content_sha256, e.audience, e.embedding into v_prior
      from kb.card_embeddings e where e.clinic_id = p_clinic_id and e.idempotency_key = v_key;
    if found then
        if v_prior.card_id <> p_card_id or v_prior.card_version <> p_card_version or v_prior.model_id <> p_model_id or v_prior.content_sha256 <> p_content_sha256
           or v_prior.audience is distinct from v_aud or (v_prior.embedding operator(extensions.<=>) v_vec) > 0.001 then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    select e.id, e.content_sha256, e.audience, e.embedding into v_prior from kb.card_embeddings e
     where e.clinic_id = p_clinic_id and e.card_id = p_card_id and e.card_version = p_card_version and e.model_id = p_model_id;
    if found then
        if v_prior.content_sha256 <> p_content_sha256 or v_prior.audience is distinct from v_aud or (v_prior.embedding operator(extensions.<=>) v_vec) > 0.001 then
            raise exception 'a version is never rewritten' using errcode = '23505';  -- changed content, audience or vector is a NEW version
        end if;
        return query select v_prior.id, false;  -- the same version and content already indexed under another key
        return;
    end if;
    select coalesce(max(e.card_version), 0) into v_max from kb.card_embeddings e where e.clinic_id = p_clinic_id and e.card_id = p_card_id;
    if p_card_version > v_max + 1 then
        raise exception 'version out of sequence' using errcode = '23514';  -- at most one above the highest already indexed: a jump would retire every older version for good
    end if;
    begin
        insert into kb.card_embeddings (clinic_id, origin, card_id, card_version, model_id, audience, embedding, content_sha256, indexed_by, idempotency_key)
        values (p_clinic_id, p_origin, p_card_id, p_card_version, p_model_id, v_aud, v_vec, p_content_sha256, v_actor, v_key)
        returning id into v_id;
    exception when unique_violation then
        -- a concurrent call won the race: answer as a replay ONLY if the row is this very card version with the same content, otherwise as a conflict. The row that matches the key is preferred.
        select e.id, e.card_id, e.card_version, e.model_id, e.content_sha256, e.audience, e.embedding into v_prior from kb.card_embeddings e
         where e.clinic_id = p_clinic_id and (e.idempotency_key = v_key or (e.card_id = p_card_id and e.card_version = p_card_version and e.model_id = p_model_id))
         order by (e.idempotency_key = v_key) desc, e.created_at
         limit 1;
        if not found or v_prior.card_id <> p_card_id or v_prior.card_version <> p_card_version or v_prior.model_id <> p_model_id or v_prior.content_sha256 <> p_content_sha256
           or v_prior.audience is distinct from v_aud or (v_prior.embedding operator(extensions.<=>) v_vec) > 0.001 then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, false;
        return;
    end;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (p_origin, p_clinic_id, v_actor, 'kb.card_embedding.indexed', 'kb.card_embeddings', v_id::text,
            jsonb_build_object('card_id', p_card_id, 'card_version', p_card_version, 'model_id', p_model_id), 'sys:card-embedding:' || gen_random_uuid()::text);
    return query select v_id, true;
end
$$;
