-- 0085_card_embeddings (owner decision 2026-11-14: retrieval at MECHANISM level inside the existing Supabase Postgres with pgvector, a documented deviation from the spec's Qdrant; BUILD-SPEC.md 10.1, 10.2,
-- 10.7, 10.8, 17.1; CLAUDE.md rules 1, 3, 5, 6, 7, 8, 10, 13).
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off
--
-- This migration contains NO clinical content. Protocol cards are clinician-owned and kb/cards stays empty: this is only the place a card's VECTOR can live once a card exists. Synthetic placeholder cards
-- (careflow/demo_cards.py, obviously invented words) are what the tests and the demo index.
--
--  * The `vector` extension is enabled in the `extensions` schema (Supabase's own place for extensions; no new service). The vector is 768 numbers: the embedding model is asked for exactly that many.
--  * kb.card_embeddings holds, per clinic, per origin, per card id, per card VERSION and per embedding model: the vector, an opaque content hash of the text that was embedded, who indexed it and when, and
--    the roles allowed to retrieve it. NO CARD TEXT, NO PATIENT TEXT, no free-text column of any kind: ids, a hash and numbers. Rows are APPEND-ONLY (update, delete and truncate refused, even for the owner,
--    ENABLE ALWAYS): a changed card is a NEW version; re-indexing the same version with different content is refused.
--  * RLS is enabled and FORCED and there is NO policy and NO table grant: the application role reaches the table only through the three definer functions below, which check auth.uid() themselves and answer
--    every refusal with ONE 42501. (A table nobody may select cannot be probed.)
--      kb.index_card_embedding(...)       writes one row (an admin or the queue worker of the clinic); idempotent on a key; refuses an origin that is not the clinic's origin.
--      kb.search_card_embeddings(...)     the retrieval: ALWAYS filters by clinic, by origin, by embedding model, by CURRENT VERSION (a card's older versions are never returned once a newer one exists, even
--                                         one the caller may not read) and by ROLE (the caller must hold, in that clinic, a role the row's audience names); nurse, doctor or the queue worker only.
--      kb.current_card_versions(...)      Postgres truth for the version join (10.8): the current indexed version of each named card the caller may read, under the same filters.
--  * Exact (sequential) cosine search, no ANN index: at the size of a card corpus (tens to hundreds of rows per clinic) it is exact and fast, and an approximate index that filters AFTER the search can silently
--    return too few rows when a filter is selective. An HNSW index is a later, measured decision.
--
-- The functions take the vector as TEXT and cast it inside, so the application role never needs USAGE on the `extensions` schema; a malformed or wrong-sized vector is refused with the same 42501.
--
-- NEW GRANTS (ledger): EXECUTE on the three functions to careflow_app. No table grant, no policy, no insert, update or delete for any application role.

create extension if not exists vector with schema extensions;

create table kb.card_embeddings (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    card_id         text not null check (card_id ~ '^[a-z0-9][a-z0-9._-]{0,99}$'),
    card_version    integer not null check (card_version >= 1),
    model_id        text not null check (model_id ~ '^[a-z0-9][a-z0-9._:-]{0,99}$'),
    audience        text[] not null check (cardinality(audience) between 1 and 5 and audience <@ array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']::text[]),
    embedding       extensions.vector(768) not null,
    content_sha256  text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
    indexed_by      uuid not null references iam.accounts (id),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint card_embeddings_one_version unique (clinic_id, card_id, card_version, model_id),
    constraint card_embeddings_key unique (clinic_id, idempotency_key)
);
create index card_embeddings_lookup_idx on kb.card_embeddings (clinic_id, origin, model_id, card_id, card_version desc);

alter table kb.card_embeddings enable row level security;
alter table kb.card_embeddings force row level security;
revoke all on kb.card_embeddings from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on kb.card_embeddings from %I', r);
        end if;
    end loop;
end $$;

create function kb.card_embeddings_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'kb.card_embeddings is append-only: a changed card is a new version' using errcode = 'restrict_violation';
end
$$;
create trigger card_embeddings_no_change before update or delete on kb.card_embeddings for each row execute function kb.card_embeddings_guard();
create trigger card_embeddings_no_truncate before truncate on kb.card_embeddings for each statement execute function kb.card_embeddings_guard();
alter table kb.card_embeddings enable always trigger card_embeddings_no_change;
alter table kb.card_embeddings enable always trigger card_embeddings_no_truncate;

-- ---- index one card version ---------------------------------------------------------------------------------------------------------------

create function kb.index_card_embedding(
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
begin
    if v_actor is null or p_clinic_id is null or p_origin is null or p_card_id is null or p_card_version is null or p_model_id is null or p_audience is null or p_embedding is null
       or p_content_sha256 is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or not iam.has_clinic_role(p_clinic_id, array['queue_worker', 'admin']) then
        raise exception 'index refused' using errcode = '42501';
    end if;
    select c.origin into v_clinic_origin from iam.clinics c where c.id = p_clinic_id;
    if v_clinic_origin is distinct from p_origin then
        raise exception 'index refused' using errcode = '42501';  -- a row never carries an origin other than its clinic's: a test card never lands in a live clinic
    end if;
    begin
        v_vec := p_embedding::extensions.vector(768);  -- passed as TEXT ('[0.1,0.2,...]'): the caller never needs USAGE on the extensions schema; a wrong size or malformed text is refused here
    exception when others then
        raise exception 'index refused' using errcode = '42501';
    end;
    v_key := 'card-embedding:' || p_idempotency_key;
    select e.id, e.card_id, e.card_version, e.model_id, e.content_sha256 into v_prior
      from kb.card_embeddings e where e.clinic_id = p_clinic_id and e.idempotency_key = v_key;
    if found then
        if v_prior.card_id <> p_card_id or v_prior.card_version <> p_card_version or v_prior.model_id <> p_model_id or v_prior.content_sha256 <> p_content_sha256 then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    select e.id, e.content_sha256 into v_prior from kb.card_embeddings e
     where e.clinic_id = p_clinic_id and e.card_id = p_card_id and e.card_version = p_card_version and e.model_id = p_model_id;
    if found then
        if v_prior.content_sha256 <> p_content_sha256 then
            raise exception 'a version is never rewritten' using errcode = '23505';  -- changed content is a NEW version
        end if;
        return query select v_prior.id, false;  -- the same version and content already indexed under another key
        return;
    end if;
    insert into kb.card_embeddings (clinic_id, origin, card_id, card_version, model_id, audience, embedding, content_sha256, indexed_by, idempotency_key)
    values (p_clinic_id, p_origin, p_card_id, p_card_version, p_model_id, p_audience, v_vec, p_content_sha256, v_actor, v_key)
    returning id into v_id;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (p_origin, p_clinic_id, v_actor, 'kb.card_embedding.indexed', 'kb.card_embeddings', v_id::text,
            jsonb_build_object('card_id', p_card_id, 'card_version', p_card_version, 'model_id', p_model_id), 'sys:card-embedding:' || gen_random_uuid()::text);
    return query select v_id, true;
end
$$;
revoke all on function kb.index_card_embedding(uuid, text, text, integer, text, text[], text, text, text) from public;
grant execute on function kb.index_card_embedding(uuid, text, text, integer, text, text[], text, text, text) to careflow_app;

-- ---- retrieval: clinic, origin, model, CURRENT version and role, always --------------------------------------------------------------------------

create function kb.search_card_embeddings(p_clinic_id uuid, p_origin text, p_model_id text, p_query text, p_limit integer)
    returns table (o_card_id text, o_card_version integer, o_similarity double precision)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_vec   extensions.vector(768);
begin
    if v_actor is null or p_clinic_id is null or p_origin is null or p_model_id is null or p_query is null or p_limit is null or p_limit not between 1 and 50
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor', 'queue_worker']) then
        raise exception 'search refused' using errcode = '42501';
    end if;
    begin
        v_vec := p_query::extensions.vector(768);
    exception when others then
        raise exception 'search refused' using errcode = '42501';
    end;
    return query
        select e.card_id, e.card_version, (1 - (e.embedding operator(extensions.<=>) v_vec))::double precision as sim
          from kb.card_embeddings e
         where e.clinic_id = p_clinic_id and e.origin = p_origin and e.model_id = p_model_id
           and iam.has_clinic_role(e.clinic_id, e.audience)
           -- the CURRENT version only: any newer version of the same card (whoever may read it) retires the older one
           and not exists (select 1 from kb.card_embeddings n where n.clinic_id = e.clinic_id and n.model_id = e.model_id and n.card_id = e.card_id and n.card_version > e.card_version)
         order by e.embedding operator(extensions.<=>) v_vec, e.card_id
         limit p_limit;
end
$$;
revoke all on function kb.search_card_embeddings(uuid, text, text, text, integer) from public;
grant execute on function kb.search_card_embeddings(uuid, text, text, text, integer) to careflow_app;

-- ---- Postgres truth for the version join ----------------------------------------------------------------------------------------------------------

create function kb.current_card_versions(p_clinic_id uuid, p_origin text, p_model_id text, p_card_ids text[])
    returns table (o_card_id text, o_card_version integer)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
begin
    if v_actor is null or p_clinic_id is null or p_origin is null or p_model_id is null or p_card_ids is null or cardinality(p_card_ids) > 100
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor', 'queue_worker']) then
        raise exception 'versions refused' using errcode = '42501';
    end if;
    return query
        select e.card_id, e.card_version
          from kb.card_embeddings e
         where e.clinic_id = p_clinic_id and e.origin = p_origin and e.model_id = p_model_id and e.card_id = any (p_card_ids)
           and iam.has_clinic_role(e.clinic_id, e.audience)
           and not exists (select 1 from kb.card_embeddings n where n.clinic_id = e.clinic_id and n.model_id = e.model_id and n.card_id = e.card_id and n.card_version > e.card_version);  -- the same rule as the search
end
$$;
revoke all on function kb.current_card_versions(uuid, text, text, text[]) from public;
grant execute on function kb.current_card_versions(uuid, text, text, text[]) to careflow_app;
