-- 0090_knowledge_editor (owner instruction 2026-11-14, deferred item 27; BUILD-SPEC.md 10.2, 12.5, 20.2; CLAUDE.md rules 1, 3, 5, 6, 8, 10). MECHANISM ONLY, OFF unless the owner sets CAREFLOW_KNOWLEDGE_EDITOR=on.
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off
--
-- The WAY a protocol card or a message template is drafted, edited, submitted and approved, and nothing else. NO CARD OR TEMPLATE IS SEEDED, kb/cards stays empty, and the software never authors a word: every
-- row is typed by a nurse or doctor. Cards stay clinician-owned.
--   * kb.knowledge_drafts     the working copy. A draft is editable ONLY by its author and ONLY while it is `draft`; `submit` freezes it; a DOCTOR who is NOT the author approves or rejects it (the two-person rule:
--                             an author never approves their own draft). The guard below enforces the freeze even for the owner: content is immutable once submitted, a decided draft never changes, nothing is deleted.
--   * kb.knowledge_versions   APPEND-ONLY. An approval writes the next version (1, 2, 3 ... per clinic, kind and code). A newer version of a card retires the older one in retrieval (the existing rule; an open question
--                             for the clinician, sheet item 38). A version is never edited; a change is a new draft and a new version.
-- Both tables: RLS enabled and FORCED, NO policy, NO table grant; everything goes through definer functions that check auth.uid(), the clinic role and a verified second factor (aal2). One 42501 for every refusal.
-- INDEXING an approved card into the vector table is NOT done here and NOT by the approver's session: the queue WORKER reads kb.knowledge_versions_to_index and calls the existing kb.index_card_embedding (which needs
-- the worker or an admin role and a registered embedding route; with none, the card is approved but not searchable).
--
-- NEW GRANTS (ledger): EXECUTE on the eight functions to careflow_app. Two new tables: no grant, no policy.

create table kb.knowledge_drafts (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    kind            text not null check (kind in ('card', 'template')),
    code            text not null check (code ~ '^[a-z0-9][a-z0-9._-]{0,99}$'),
    title           text not null check (length(btrim(title)) between 1 and 200),
    body            text not null check (length(btrim(body)) between 1 and 4000),
    synonyms        text[] not null default '{}' check (cardinality(synonyms) <= 20 and length(array_to_string(synonyms, ' ')) <= 4000),
    patient_phrases text[] not null default '{}' check (cardinality(patient_phrases) <= 20 and length(array_to_string(patient_phrases, ' ')) <= 4000),
    audience        text[] not null default '{}' check (cardinality(audience) <= 5 and audience <@ array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']::text[]),
    status          text not null default 'draft' check (status in ('draft', 'submitted', 'approved', 'rejected')),
    author          uuid not null references iam.accounts (id),
    submitted_at    timestamptz,
    decided_by      uuid references iam.accounts (id),
    decided_at      timestamptz,
    reject_reason   text check (reject_reason ~ '^[a-z][a-z0-9_]{0,59}$'),
    created_at      timestamptz not null default clock_timestamp(),
    updated_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    edit_key        text check (length(edit_key) between 1 and 300),
    submit_key      text check (length(submit_key) between 1 and 300),
    decide_key      text check (length(decide_key) between 1 and 300),
    constraint knowledge_drafts_key_per_clinic unique (clinic_id, idempotency_key),
    constraint knowledge_drafts_id_clinic unique (id, clinic_id),
    check (kind = 'template' or cardinality(audience) >= 1),
    check ((status in ('approved', 'rejected')) = (decided_by is not null and decided_at is not null)),
    check (status <> 'approved' or decided_by <> author),  -- the two-person rule, in the database
    check ((status = 'rejected') = (reject_reason is not null))
);
-- at most one OPEN draft (draft or submitted) per clinic, kind and code
create unique index knowledge_drafts_one_open on kb.knowledge_drafts (clinic_id, kind, code) where status in ('draft', 'submitted');

create table kb.knowledge_versions (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    kind            text not null check (kind in ('card', 'template')),
    code            text not null check (code ~ '^[a-z0-9][a-z0-9._-]{0,99}$'),
    version         integer not null check (version between 1 and 100000),
    title           text not null,
    body            text not null,
    synonyms        text[] not null,
    patient_phrases text[] not null,
    audience        text[] not null,
    draft_id        uuid not null,
    author          uuid not null references iam.accounts (id),
    approved_by     uuid not null references iam.accounts (id),
    approved_at     timestamptz not null default clock_timestamp(),
    constraint knowledge_versions_one unique (clinic_id, kind, code, version),
    constraint knowledge_versions_one_per_draft unique (draft_id),
    foreign key (draft_id, clinic_id) references kb.knowledge_drafts (id, clinic_id),
    check (approved_by <> author)
);

do $$
declare t text; r text;
begin
    foreach t in array array['kb.knowledge_drafts', 'kb.knowledge_versions'] loop
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

create function kb.knowledge_versions_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'kb.knowledge_versions is append-only: a change is a new draft and a new version' using errcode = 'restrict_violation';
end
$$;
create trigger knowledge_versions_no_change before update or delete on kb.knowledge_versions for each row execute function kb.knowledge_versions_guard();
create trigger knowledge_versions_no_truncate before truncate on kb.knowledge_versions for each statement execute function kb.knowledge_versions_guard();
alter table kb.knowledge_versions enable always trigger knowledge_versions_no_change;
alter table kb.knowledge_versions enable always trigger knowledge_versions_no_truncate;

create function kb.knowledge_drafts_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op in ('DELETE', 'TRUNCATE') then
        raise exception 'kb.knowledge_drafts rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if (old.id, old.clinic_id, old.origin, old.kind, old.code, old.author, old.created_at, old.idempotency_key)
       is distinct from (new.id, new.clinic_id, new.origin, new.kind, new.code, new.author, new.created_at, new.idempotency_key) then
        raise exception 'the identity of a draft never changes' using errcode = 'restrict_violation';
    end if;
    if old.status in ('approved', 'rejected') then
        raise exception 'a decided draft never changes' using errcode = 'restrict_violation';
    end if;
    if (old.title, old.body, old.synonyms, old.patient_phrases, old.audience) is distinct from (new.title, new.body, new.synonyms, new.patient_phrases, new.audience) and old.status <> 'draft' then
        raise exception 'a submitted draft is frozen' using errcode = 'restrict_violation';
    end if;
    if not ((old.status = 'draft' and new.status in ('draft', 'submitted')) or (old.status = 'submitted' and new.status in ('submitted', 'approved', 'rejected'))) then
        raise exception 'a draft moves only forward: draft, submitted, approved or rejected' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;
create trigger knowledge_drafts_guard_update before update on kb.knowledge_drafts for each row execute function kb.knowledge_drafts_guard();
create trigger knowledge_drafts_guard_other before delete on kb.knowledge_drafts for each row execute function kb.knowledge_drafts_guard();
create trigger knowledge_drafts_guard_truncate before truncate on kb.knowledge_drafts for each statement execute function kb.knowledge_drafts_guard();
alter table kb.knowledge_drafts enable always trigger knowledge_drafts_guard_update;
alter table kb.knowledge_drafts enable always trigger knowledge_drafts_guard_other;
alter table kb.knowledge_drafts enable always trigger knowledge_drafts_guard_truncate;

-- ---- create ------------------------------------------------------------------------------------------------------------------------------------

create function kb.knowledge_draft_create(
    p_clinic_id uuid, p_kind text, p_code text, p_title text, p_body text, p_synonyms text[], p_patient_phrases text[], p_audience text[], p_idempotency_key text
) returns table (o_id uuid, o_created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_key    text;
    v_prior  record;
    v_id     uuid;
begin
    if v_actor is null or p_clinic_id is null or p_kind is null or p_code is null or p_title is null or p_body is null or p_synonyms is null or p_patient_phrases is null or p_audience is null
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session()
       or exists (select 1 from unnest(p_synonyms || p_patient_phrases) as x where x is null or length(btrim(x)) = 0 or length(x) > 200)
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    select c.origin into v_origin from iam.clinics c where c.id = p_clinic_id;
    v_key := 'knowledge-draft:' || v_actor::text || ':' || p_idempotency_key;
    select d.id, d.kind, d.code, d.title, d.body into v_prior from kb.knowledge_drafts d where d.clinic_id = p_clinic_id and d.idempotency_key = v_key;
    if found then
        if v_prior.kind <> p_kind or v_prior.code <> p_code or v_prior.title <> p_title or v_prior.body <> p_body then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    if exists (select 1 from kb.knowledge_drafts d where d.clinic_id = p_clinic_id and d.kind = p_kind and d.code = p_code and d.status in ('draft', 'submitted')) then
        raise exception 'an open draft exists' using errcode = '23505';  -- finish or decide the open one first
    end if;
    insert into kb.knowledge_drafts (clinic_id, origin, kind, code, title, body, synonyms, patient_phrases, audience, author, idempotency_key)
    values (p_clinic_id, v_origin, p_kind, p_code, btrim(p_title), btrim(p_body), p_synonyms, p_patient_phrases, p_audience, v_actor, v_key)
    returning id into v_id;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'kb.knowledge_draft.created', 'kb.knowledge_drafts', v_id::text, jsonb_build_object('kind', p_kind, 'code', p_code), 'sys:knowledge-draft:' || gen_random_uuid()::text);
    return query select v_id, true;
end
$$;

-- ---- edit (author only, while a draft) -------------------------------------------------------------------------------------------------------------

create function kb.knowledge_draft_edit(
    p_clinic_id uuid, p_draft_id uuid, p_title text, p_body text, p_synonyms text[], p_patient_phrases text[], p_audience text[], p_idempotency_key text
) returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_d     record;
begin
    if v_actor is null or p_clinic_id is null or p_draft_id is null or p_title is null or p_body is null or p_synonyms is null or p_patient_phrases is null or p_audience is null
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session()
       or exists (select 1 from unnest(p_synonyms || p_patient_phrases) as x where x is null or length(btrim(x)) = 0 or length(x) > 200)
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    select d.id, d.author, d.status, d.edit_key into v_d from kb.knowledge_drafts d where d.id = p_draft_id and d.clinic_id = p_clinic_id for update;
    if not found or v_d.author <> v_actor then
        raise exception 'draft refused' using errcode = '42501';  -- only the author edits, and a draft of another clinic is nobody's
    end if;
    if v_d.edit_key = 'knowledge-edit:' || p_idempotency_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_d.status <> 'draft' then
        return query select 'invalid_state'::text;  -- a submitted or decided draft is frozen
        return;
    end if;
    update kb.knowledge_drafts set title = btrim(p_title), body = btrim(p_body), synonyms = p_synonyms, patient_phrases = p_patient_phrases, audience = p_audience, edit_key = 'knowledge-edit:' || p_idempotency_key,
           updated_at = clock_timestamp() where id = p_draft_id;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    select d.origin, d.clinic_id, v_actor, 'kb.knowledge_draft.edited', 'kb.knowledge_drafts', d.id::text, jsonb_build_object('kind', d.kind, 'code', d.code), 'sys:knowledge-draft:' || gen_random_uuid()::text
      from kb.knowledge_drafts d where d.id = p_draft_id;
    return query select 'recorded'::text;
end
$$;

-- ---- submit (author only) ---------------------------------------------------------------------------------------------------------------------------

create function kb.knowledge_draft_submit(p_clinic_id uuid, p_draft_id uuid, p_idempotency_key text)
    returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_d     record;
begin
    if v_actor is null or p_clinic_id is null or p_draft_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session()
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    select d.id, d.author, d.status, d.submit_key, d.origin, d.kind, d.code into v_d from kb.knowledge_drafts d where d.id = p_draft_id and d.clinic_id = p_clinic_id for update;
    if not found or v_d.author <> v_actor then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    if v_d.status = 'submitted' and v_d.submit_key = 'knowledge-submit:' || p_idempotency_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_d.status <> 'draft' then
        return query select 'invalid_state'::text;
        return;
    end if;
    update kb.knowledge_drafts set status = 'submitted', submitted_at = clock_timestamp(), submit_key = 'knowledge-submit:' || p_idempotency_key, updated_at = clock_timestamp() where id = p_draft_id;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (v_d.origin, p_clinic_id, v_actor, 'kb.knowledge_draft.submitted', 'kb.knowledge_drafts', p_draft_id::text, jsonb_build_object('kind', v_d.kind, 'code', v_d.code), 'sys:knowledge-draft:' || gen_random_uuid()::text);
    return query select 'recorded'::text;
end
$$;

-- ---- decide (a DOCTOR who is not the author) ------------------------------------------------------------------------------------------------------

create function kb.knowledge_draft_decide(p_clinic_id uuid, p_draft_id uuid, p_decision text, p_reason text, p_idempotency_key text)
    returns table (o_result text, o_version integer)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_d     record;
    v_ver   integer;
begin
    if v_actor is null or p_clinic_id is null or p_draft_id is null or p_decision is null or p_decision not in ('approve', 'reject') or p_idempotency_key is null
       or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session()
       or (p_decision = 'reject' and (p_reason is null or p_reason !~ '^[a-z][a-z0-9_]{0,59}$')) or (p_decision = 'approve' and p_reason is not null)
       or not iam.has_clinic_role(p_clinic_id, array['doctor']) then
        raise exception 'decision refused' using errcode = '42501';
    end if;
    select d.* into v_d from kb.knowledge_drafts d where d.id = p_draft_id and d.clinic_id = p_clinic_id for update;
    if not found or v_d.author = v_actor then
        raise exception 'decision refused' using errcode = '42501';  -- an author never decides their own draft; one answer with every other refusal
    end if;
    if v_d.status in ('approved', 'rejected') and v_d.decide_key = 'knowledge-decide:' || p_idempotency_key then
        return query select 'replay'::text, (select v.version from kb.knowledge_versions v where v.draft_id = p_draft_id);
        return;
    end if;
    if v_d.status <> 'submitted' then
        return query select 'invalid_state'::text, null::integer;
        return;
    end if;
    if p_decision = 'approve' then
        select coalesce(max(v.version), 0) + 1 into v_ver from kb.knowledge_versions v where v.clinic_id = p_clinic_id and v.kind = v_d.kind and v.code = v_d.code;
        insert into kb.knowledge_versions (clinic_id, origin, kind, code, version, title, body, synonyms, patient_phrases, audience, draft_id, author, approved_by)
        values (p_clinic_id, v_d.origin, v_d.kind, v_d.code, v_ver, v_d.title, v_d.body, v_d.synonyms, v_d.patient_phrases, v_d.audience, p_draft_id, v_d.author, v_actor);
        update kb.knowledge_drafts set status = 'approved', decided_by = v_actor, decided_at = clock_timestamp(), decide_key = 'knowledge-decide:' || p_idempotency_key, updated_at = clock_timestamp() where id = p_draft_id;
    else
        v_ver := null;
        update kb.knowledge_drafts set status = 'rejected', decided_by = v_actor, decided_at = clock_timestamp(), reject_reason = p_reason, decide_key = 'knowledge-decide:' || p_idempotency_key, updated_at = clock_timestamp()
         where id = p_draft_id;
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_d.origin, p_clinic_id, v_actor, 'doctor', 'kb.knowledge_draft.' || p_decision || 'd', 'kb.knowledge_drafts', p_draft_id::text,
            jsonb_build_object('kind', v_d.kind, 'code', v_d.code, 'version', v_ver), 'sys:knowledge-draft:' || gen_random_uuid()::text);
    return query select 'recorded'::text, v_ver;
end
$$;

-- ---- lists (nurse or doctor, aal2) -------------------------------------------------------------------------------------------------------------------

create function kb.knowledge_drafts_list(p_clinic_id uuid, p_limit integer)
    returns table (o_id uuid, o_kind text, o_code text, o_title text, o_body text, o_synonyms text[], o_patient_phrases text[], o_audience text[], o_status text, o_author uuid, o_decided_by uuid,
                   o_reject_reason text, o_updated_at timestamptz)
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or p_limit is null or p_limit not between 1 and 200 or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'list refused' using errcode = '42501';
    end if;
    return query
        select d.id, d.kind, d.code, d.title, d.body, d.synonyms, d.patient_phrases, d.audience, d.status, d.author, d.decided_by, d.reject_reason, d.updated_at
          from kb.knowledge_drafts d where d.clinic_id = p_clinic_id order by d.updated_at desc, d.id limit p_limit;
end
$$;

create function kb.knowledge_versions_list(p_clinic_id uuid, p_limit integer)
    returns table (o_kind text, o_code text, o_version integer, o_title text, o_audience text[], o_author uuid, o_approved_by uuid, o_approved_at timestamptz)
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or p_limit is null or p_limit not between 1 and 200 or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'list refused' using errcode = '42501';
    end if;
    return query
        select v.kind, v.code, v.version, v.title, v.audience, v.author, v.approved_by, v.approved_at
          from kb.knowledge_versions v where v.clinic_id = p_clinic_id order by v.approved_at desc, v.code, v.version desc limit p_limit;
end
$$;

-- ---- the worker's read: approved card versions that are not in the vector index yet ---------------------------------------------------------------

create function kb.knowledge_versions_to_index(p_clinic_id uuid, p_limit integer)
    returns table (o_code text, o_version integer, o_title text, o_body text, o_synonyms text[], o_patient_phrases text[], o_audience text[])
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or p_limit is null or p_limit not between 1 and 100 or not iam.has_clinic_role(p_clinic_id, array['queue_worker']) then
        raise exception 'index list refused' using errcode = '42501';
    end if;
    return query
        select v.code, v.version, v.title, v.body, v.synonyms, v.patient_phrases, v.audience
          from kb.knowledge_versions v
         where v.clinic_id = p_clinic_id and v.kind = 'card'
           and not exists (select 1 from kb.card_embeddings e where e.clinic_id = v.clinic_id and e.card_id = v.code and e.card_version = v.version)
         order by v.code, v.version limit p_limit;
end
$$;

-- ---- the current approved cards, for the search's keyword side (nurse, doctor or worker; only the cards the caller's role may read) -------------------------------

create function kb.knowledge_current_cards(p_clinic_id uuid)
    returns table (o_code text, o_version integer, o_title text, o_body text, o_synonyms text[], o_patient_phrases text[])
    language plpgsql stable security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor', 'queue_worker']) then
        raise exception 'cards refused' using errcode = '42501';
    end if;
    return query
        select v.code, v.version, v.title, v.body, v.synonyms, v.patient_phrases
          from kb.knowledge_versions v
         where v.clinic_id = p_clinic_id and v.kind = 'card' and iam.has_clinic_role(v.clinic_id, v.audience)
           and not exists (select 1 from kb.knowledge_versions n where n.clinic_id = v.clinic_id and n.kind = v.kind and n.code = v.code and n.version > v.version)
         order by v.code limit 500;
end
$$;

revoke all on function kb.knowledge_draft_create(uuid, text, text, text, text, text[], text[], text[], text), kb.knowledge_draft_edit(uuid, uuid, text, text, text[], text[], text[], text),
    kb.knowledge_draft_submit(uuid, uuid, text), kb.knowledge_draft_decide(uuid, uuid, text, text, text), kb.knowledge_drafts_list(uuid, integer), kb.knowledge_versions_list(uuid, integer),
    kb.knowledge_versions_to_index(uuid, integer), kb.knowledge_current_cards(uuid) from public;
grant execute on function kb.knowledge_draft_create(uuid, text, text, text, text, text[], text[], text[], text), kb.knowledge_draft_edit(uuid, uuid, text, text, text[], text[], text[], text),
    kb.knowledge_draft_submit(uuid, uuid, text), kb.knowledge_draft_decide(uuid, uuid, text, text, text), kb.knowledge_drafts_list(uuid, integer), kb.knowledge_versions_list(uuid, integer),
    kb.knowledge_versions_to_index(uuid, integer), kb.knowledge_current_cards(uuid) to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function kb.knowledge_draft_create(uuid, text, text, text, text, text[], text[], text[], text), kb.knowledge_draft_edit(uuid, uuid, text, text, text[], text[], text[], text), '
                           'kb.knowledge_draft_submit(uuid, uuid, text), kb.knowledge_draft_decide(uuid, uuid, text, text, text), kb.knowledge_drafts_list(uuid, integer), '
                           'kb.knowledge_versions_list(uuid, integer), kb.knowledge_versions_to_index(uuid, integer), kb.knowledge_current_cards(uuid) from %I', r);
        end if;
    end loop;
end $$;
