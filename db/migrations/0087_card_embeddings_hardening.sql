-- 0087_card_embeddings_hardening (rls-security-reviewer report on 0085, 2026-11-14; BUILD-SPEC.md 10.7, 10.8; CLAUDE.md rules 3, 5, 8, 10). Fix forward: 0085 is merged and is NOT edited.
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off  (no clinical content: ids, hashes, numbers)
--
-- The reviewer found no path to read or write the table or learn that rows exist (every refusal is one 42501 before the table is touched). It found integrity and availability gaps, fixed here:
--   * HIGH   version retirement was unbounded and irreversible: any worker or admin of a clinic could index card_version 2147483647 and retire every older version for every reader, forever. Now a version may be at
--            most ONE above the highest version already indexed for that card in that clinic (a first index is version 1), and a CHECK caps it at 100000. (A tombstone for a mistaken version needs a published-cards
--            table that does not exist yet: a clinician-owned card lifecycle, docs/design/clinician-sheet.md item 38. Until then a wrong version is corrected by indexing the next one.)
--   * HIGH   the definer functions rely on their owner bypassing row-level security (FORCE RLS with no policy). That is this project's convention (0083 and others) and holds on Supabase, but it was undeclared:
--            this migration now ASSERTS it (and that the vector extension lives in `extensions`) before anything else, in the way 0024 does, so a project where it is false fails loudly instead of returning empty searches.
--   * MEDIUM "current version" was computed per embedding model, so a newer version under model A did not retire an older version under model B. It is now global per card in the clinic (search and versions).
--   * MEDIUM a replay (same key, or the same version and content under another key) silently ignored a DIFFERENT audience or vector. Now the stored audience (normalised: distinct, sorted) and vector must match too,
--            otherwise 23505. The audience is stored normalised from now on.
--   * MEDIUM an all-zero vector (permanent, and cosine against it is NaN) is refused on index (42501), and a zero query vector on search.
--   * MEDIUM the three functions are revoked from anon, authenticated and service_role as well as public, and the table from careflow_app explicitly (defence in depth: the role check already refused them).
--   * LOW    the text arguments are length-capped before they are parsed (vectors 30000 characters, card ids 100, audience 5 entries); the cast handler is narrowed to data errors; a concurrent first insert that
--            loses the race re-reads and answers as a replay instead of a spurious conflict.
-- The functions are REPLACED with the same signatures, so the EXECUTE grants to careflow_app stay.
--
-- NEW GRANTS (ledger): none. Revokes only (anon, authenticated, service_role on the three functions; careflow_app on the table).

do $$
begin
    if not exists (select 1 from pg_extension e where e.extname = 'vector' and e.extnamespace = 'extensions'::regnamespace) then
        raise exception '0087 needs the vector extension in the extensions schema (0085 creates it there)';
    end if;
    if not exists (select 1 from pg_roles where rolname = current_user and (rolsuper or rolbypassrls)) then
        raise exception '0087 needs the function owner to bypass row-level security (the kb.card_embeddings definer functions rely on it, as the other forced-RLS definers do)';
    end if;
end $$;

alter table kb.card_embeddings add constraint card_embeddings_version_bound check (card_version <= 100000);

-- ---- index one card version ---------------------------------------------------------------------------------------------------------------

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
           or v_prior.audience is distinct from v_aud or not (v_prior.embedding operator(extensions.=) v_vec) then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    select e.id, e.content_sha256, e.audience, e.embedding into v_prior from kb.card_embeddings e
     where e.clinic_id = p_clinic_id and e.card_id = p_card_id and e.card_version = p_card_version and e.model_id = p_model_id;
    if found then
        if v_prior.content_sha256 <> p_content_sha256 or v_prior.audience is distinct from v_aud or not (v_prior.embedding operator(extensions.=) v_vec) then
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
        -- a concurrent call won the race: answer as a replay if it is the same card version and content, otherwise as a conflict
        select e.id, e.content_sha256, e.audience, e.embedding into v_prior from kb.card_embeddings e
         where e.clinic_id = p_clinic_id and (e.idempotency_key = v_key or (e.card_id = p_card_id and e.card_version = p_card_version and e.model_id = p_model_id));
        if not found or v_prior.content_sha256 <> p_content_sha256 or v_prior.audience is distinct from v_aud or not (v_prior.embedding operator(extensions.=) v_vec) then
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

-- ---- retrieval: the current version is global per card ----------------------------------------------------------------------------------------

create or replace function kb.search_card_embeddings(p_clinic_id uuid, p_origin text, p_model_id text, p_query text, p_limit integer)
    returns table (o_card_id text, o_card_version integer, o_similarity double precision)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_vec   extensions.vector(768);
begin
    if v_actor is null or p_clinic_id is null or p_origin is null or p_model_id is null or p_query is null or p_limit is null or p_limit not between 1 and 50
       or length(p_query) > 30000 or length(p_model_id) > 100
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor', 'queue_worker']) then
        raise exception 'search refused' using errcode = '42501';
    end if;
    begin
        v_vec := p_query::extensions.vector(768);
    exception when invalid_text_representation or data_exception or numeric_value_out_of_range then
        raise exception 'search refused' using errcode = '42501';
    end;
    if (v_vec operator(extensions.<#>) v_vec) = 0 then
        raise exception 'search refused' using errcode = '42501';  -- a zero query has no direction: cosine against it is NaN
    end if;
    return query
        select e.card_id, e.card_version, (1 - (e.embedding operator(extensions.<=>) v_vec))::double precision as sim
          from kb.card_embeddings e
         where e.clinic_id = p_clinic_id and e.origin = p_origin and e.model_id = p_model_id
           and iam.has_clinic_role(e.clinic_id, e.audience)
           -- the CURRENT version only: ANY newer version of the same card in this clinic (any embedding model, whoever may read it) retires the older one
           and not exists (select 1 from kb.card_embeddings n where n.clinic_id = e.clinic_id and n.card_id = e.card_id and n.card_version > e.card_version)
         order by e.embedding operator(extensions.<=>) v_vec, e.card_id
         limit p_limit;
end
$$;

-- ---- Postgres truth for the version join ----------------------------------------------------------------------------------------------------------

create or replace function kb.current_card_versions(p_clinic_id uuid, p_origin text, p_model_id text, p_card_ids text[])
    returns table (o_card_id text, o_card_version integer)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
begin
    if v_actor is null or p_clinic_id is null or p_origin is null or p_model_id is null or p_card_ids is null or cardinality(p_card_ids) > 100 or length(p_model_id) > 100
       or exists (select 1 from unnest(p_card_ids) as x where x is null or length(x) > 100)
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor', 'queue_worker']) then
        raise exception 'versions refused' using errcode = '42501';
    end if;
    return query
        select e.card_id, e.card_version
          from kb.card_embeddings e
         where e.clinic_id = p_clinic_id and e.origin = p_origin and e.model_id = p_model_id and e.card_id = any (p_card_ids)
           and iam.has_clinic_role(e.clinic_id, e.audience)
           and not exists (select 1 from kb.card_embeddings n where n.clinic_id = e.clinic_id and n.card_id = e.card_id and n.card_version > e.card_version);  -- the same rule as the search
end
$$;

-- ---- revokes (defence in depth; the role check already refuses them) ------------------------------------------------------------------------------

do $$
declare
    r text;
    f text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            foreach f in array array[
                'kb.index_card_embedding(uuid, text, text, integer, text, text[], text, text, text)',
                'kb.search_card_embeddings(uuid, text, text, text, integer)',
                'kb.current_card_versions(uuid, text, text, text[])'] loop
                execute format('revoke all on function %s from %I', f, r);
            end loop;
        end if;
    end loop;
end $$;
revoke all on kb.card_embeddings from careflow_app;
