-- 0092_knowledge_editor_fixes (rls-security-reviewer report on 0089 to 0091, 2026-11-18; CLAUDE.md rules 5, 10). Fix forward: 0090 and 0091 are merged and are NOT edited.
--
--   * M1 the audit action of a REJECTION was written `kb.knowledge_draft.rejectd`; it is now `kb.knowledge_draft.rejected` (approved stays `approved`).
--   * M2 a legitimate replay of a draft CREATE was refused as a conflict when the title or body had padding or had been edited since: the stored text was compared with the raw input. A key now identifies the request by its
--        kind and code only (the same key with another kind or code is still refused); the content of the first request is not re-compared after the draft may have been edited.
--   * M3 input that reached a table CHECK (a whitespace-only title, a card edited to no audience, synonyms and phrases longer than the 4000-character cap, an unknown role in the audience) surfaced as an unmapped 23514 and a
--        500. Create and edit now validate the whole content up front with one helper and refuse with the usual 42501.
--   * M6 a family-link rule row with NO age bound at all allowed every age: a rule must now state at least ONE bound (counsel must choose one). The table ships empty, so nothing existing is affected.
-- The three functions are REPLACED with the same signatures, so the EXECUTE grants stay. Not changed here (owner decisions, see docs/design/threat-model.md 13d): aal2 on kb.knowledge_current_cards (M4), the
-- application-supplied age (M5), re-checking the requester at verification (M7).
--
-- NEW GRANTS (ledger): none.

alter table iam.family_link_rules add constraint family_link_rules_a_bound check (min_dependant_age_years is not null or max_dependant_age_years is not null);

create function kb.knowledge_content_ok(p_kind text, p_title text, p_body text, p_synonyms text[], p_patient_phrases text[], p_audience text[])
    returns boolean
    language sql immutable set search_path = ''
as $$
    select p_kind in ('card', 'template')
       and p_title is not null and length(btrim(p_title)) between 1 and 200
       and p_body is not null and length(btrim(p_body)) between 1 and 4000
       and p_synonyms is not null and cardinality(p_synonyms) <= 20 and length(array_to_string(p_synonyms, ' ')) <= 4000
       and p_patient_phrases is not null and cardinality(p_patient_phrases) <= 20 and length(array_to_string(p_patient_phrases, ' ')) <= 4000
       and not exists (select 1 from unnest(p_synonyms || p_patient_phrases) as x where x is null or length(btrim(x)) = 0 or length(x) > 200)
       and p_audience is not null and cardinality(p_audience) <= 5 and p_audience <@ array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']::text[]
       and (p_kind = 'template' or cardinality(p_audience) >= 1)
$$;
revoke all on function kb.knowledge_content_ok(text, text, text, text[], text[], text[]) from public;

create or replace function kb.knowledge_draft_create(
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
    if v_actor is null or p_clinic_id is null or p_kind is null or p_code is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_code !~ '^[a-z0-9][a-z0-9._-]{0,99}$' or not iam.aal2_session()
       or not kb.knowledge_content_ok(p_kind, p_title, p_body, p_synonyms, p_patient_phrases, p_audience)
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    select c.origin into v_origin from iam.clinics c where c.id = p_clinic_id;
    v_key := 'knowledge-draft:' || v_actor::text || ':' || p_idempotency_key;
    select d.id, d.kind, d.code into v_prior from kb.knowledge_drafts d where d.clinic_id = p_clinic_id and d.idempotency_key = v_key;
    if found then
        if v_prior.kind <> p_kind or v_prior.code <> p_code then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, false;  -- the same request again, whatever the draft has become since
        return;
    end if;
    if exists (select 1 from kb.knowledge_drafts d where d.clinic_id = p_clinic_id and d.kind = p_kind and d.code = p_code and d.status in ('draft', 'submitted')) then
        raise exception 'an open draft exists' using errcode = '23505';
    end if;
    insert into kb.knowledge_drafts (clinic_id, origin, kind, code, title, body, synonyms, patient_phrases, audience, author, idempotency_key)
    values (p_clinic_id, v_origin, p_kind, p_code, btrim(p_title), btrim(p_body), p_synonyms, p_patient_phrases, p_audience, v_actor, v_key)
    returning id into v_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, case when iam.has_clinic_role(p_clinic_id, array['doctor']) then 'doctor' else 'nurse' end, 'kb.knowledge_draft.created', 'kb.knowledge_drafts', v_id::text, jsonb_build_object('kind', p_kind, 'code', p_code), 'sys:knowledge-draft:' || gen_random_uuid()::text);
    return query select v_id, true;
end
$$;

create or replace function kb.knowledge_draft_edit(
    p_clinic_id uuid, p_draft_id uuid, p_title text, p_body text, p_synonyms text[], p_patient_phrases text[], p_audience text[], p_idempotency_key text
) returns table (o_result text)
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
    select d.id, d.kind, d.author, d.status, d.edit_key into v_d from kb.knowledge_drafts d where d.id = p_draft_id and d.clinic_id = p_clinic_id for update;
    if not found or v_d.author <> v_actor or not kb.knowledge_content_ok(v_d.kind, p_title, p_body, p_synonyms, p_patient_phrases, p_audience) then
        raise exception 'draft refused' using errcode = '42501';  -- only the author edits; the content is checked against the draft's own kind (a card needs an audience)
    end if;
    if v_d.edit_key = 'knowledge-edit:' || p_idempotency_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_d.status <> 'draft' then
        return query select 'invalid_state'::text;
        return;
    end if;
    update kb.knowledge_drafts set title = btrim(p_title), body = btrim(p_body), synonyms = p_synonyms, patient_phrases = p_patient_phrases, audience = p_audience, edit_key = 'knowledge-edit:' || p_idempotency_key,
           updated_at = clock_timestamp() where id = p_draft_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    select d.origin, d.clinic_id, v_actor, case when iam.has_clinic_role(d.clinic_id, array['doctor']) then 'doctor' else 'nurse' end, 'kb.knowledge_draft.edited', 'kb.knowledge_drafts', d.id::text, jsonb_build_object('kind', d.kind, 'code', d.code),
           'sys:knowledge-draft:' || gen_random_uuid()::text
      from kb.knowledge_drafts d where d.id = p_draft_id;
    return query select 'recorded'::text;
end
$$;

create or replace function kb.knowledge_draft_decide(p_clinic_id uuid, p_draft_id uuid, p_decision text, p_reason text, p_idempotency_key text)
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
        raise exception 'decision refused' using errcode = '42501';
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
    values (v_d.origin, p_clinic_id, v_actor, 'doctor', case when p_decision = 'approve' then 'kb.knowledge_draft.approved' else 'kb.knowledge_draft.rejected' end, 'kb.knowledge_drafts', p_draft_id::text,
            jsonb_build_object('kind', v_d.kind, 'code', v_d.code, 'version', v_ver), 'sys:knowledge-draft:' || gen_random_uuid()::text);
    return query select 'recorded'::text, v_ver;
end
$$;
