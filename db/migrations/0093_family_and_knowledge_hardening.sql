-- 0093_family_and_knowledge_hardening (owner decisions 2026-11-18 on the reviewer report for 0089 to 0091; CLAUDE.md rules 5, 8, 10). Fix forward: no merged migration is edited.
--
--   * M7 at VERIFICATION the family-link requester is re-checked: their patient record must still be at this clinic, not erased, bound to the same active patient account. Otherwise the answer is `requester_not_active`.
--   * A FRONT-DESK REVOKE of a family link (a consent withdrawn, a safeguarding reason): front desk of the clinic, with a verified second factor, a closed reason word, an idempotency key, an audit row. New columns
--     `revoked_by` and `revoke_reason` (the existing `reject_reason` belongs to rejections).
--   * L1 replay keys are TIED TO THE ACTING ACCOUNT for the knowledge editor's edit, submit and decide and for the family-link decide: the stored key is `prefix:<actor>:<key>`, so another person who supplies the same key
--     does not get a replay (and a reject carrying an approve's key is not a replay).
--   * The audit names `iam.family_link.verify` and `.reject` become past tense (`verified`, `rejected`).
--   * A `provenance` text column on iam.family_link_rules, so a row can say where its numbers came from (the dev demo seed writes "DEMO PLACEHOLDER, not counsel's values"; counsel's real rows may leave it empty).
-- NOT done here, recorded: the closed item set of visit preparation (0089) is enforced by the application because the items live in an application setting; enforcing it in the database needs a table of allowed items,
-- which is not a small change.
-- The functions are REPLACED with the same signatures (the EXECUTE grants stay); the one new function is granted.
--
-- NEW GRANTS (ledger): EXECUTE on iam.family_link_staff_revoke to careflow_app.

alter table iam.family_links add column revoked_by uuid references iam.accounts (id);
alter table iam.family_links add column revoke_reason text check (revoke_reason ~ '^[a-z][a-z0-9_]{0,59}$');
alter table iam.family_link_rules add column provenance text check (provenance is null or length(provenance) <= 200);

-- ---- knowledge editor: submit, edit, decide with actor-bound replay keys ------------------------------------------------------------------------------

create or replace function kb.knowledge_draft_edit(
    p_clinic_id uuid, p_draft_id uuid, p_title text, p_body text, p_synonyms text[], p_patient_phrases text[], p_audience text[], p_idempotency_key text
) returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_d     record;
    v_key   text := 'knowledge-edit:' || auth.uid()::text || ':' || p_idempotency_key;
begin
    if v_actor is null or p_clinic_id is null or p_draft_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session()
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    select d.id, d.kind, d.author, d.status, d.edit_key into v_d from kb.knowledge_drafts d where d.id = p_draft_id and d.clinic_id = p_clinic_id for update;
    if not found or v_d.author <> v_actor or not kb.knowledge_content_ok(v_d.kind, p_title, p_body, p_synonyms, p_patient_phrases, p_audience) then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    if v_d.edit_key = v_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_d.status <> 'draft' then
        return query select 'invalid_state'::text;
        return;
    end if;
    update kb.knowledge_drafts set title = btrim(p_title), body = btrim(p_body), synonyms = p_synonyms, patient_phrases = p_patient_phrases, audience = p_audience, edit_key = v_key, updated_at = clock_timestamp()
     where id = p_draft_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    select d.origin, d.clinic_id, v_actor, case when iam.has_clinic_role(d.clinic_id, array['doctor']) then 'doctor' else 'nurse' end, 'kb.knowledge_draft.edited', 'kb.knowledge_drafts', d.id::text,
           jsonb_build_object('kind', d.kind, 'code', d.code), 'sys:knowledge-draft:' || gen_random_uuid()::text
      from kb.knowledge_drafts d where d.id = p_draft_id;
    return query select 'recorded'::text;
end
$$;

create or replace function kb.knowledge_draft_submit(p_clinic_id uuid, p_draft_id uuid, p_idempotency_key text)
    returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_d     record;
    v_key   text := 'knowledge-submit:' || auth.uid()::text || ':' || p_idempotency_key;
begin
    if v_actor is null or p_clinic_id is null or p_draft_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session()
       or not iam.has_clinic_role(p_clinic_id, array['nurse', 'doctor']) then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    select d.id, d.author, d.status, d.submit_key, d.origin, d.kind, d.code into v_d from kb.knowledge_drafts d where d.id = p_draft_id and d.clinic_id = p_clinic_id for update;
    if not found or v_d.author <> v_actor then
        raise exception 'draft refused' using errcode = '42501';
    end if;
    if v_d.status = 'submitted' and v_d.submit_key = v_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_d.status <> 'draft' then
        return query select 'invalid_state'::text;
        return;
    end if;
    update kb.knowledge_drafts set status = 'submitted', submitted_at = clock_timestamp(), submit_key = v_key, updated_at = clock_timestamp() where id = p_draft_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_d.origin, p_clinic_id, v_actor, case when iam.has_clinic_role(p_clinic_id, array['doctor']) then 'doctor' else 'nurse' end, 'kb.knowledge_draft.submitted', 'kb.knowledge_drafts', p_draft_id::text,
            jsonb_build_object('kind', v_d.kind, 'code', v_d.code), 'sys:knowledge-draft:' || gen_random_uuid()::text);
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
    v_key   text := 'knowledge-decide:' || auth.uid()::text || ':' || p_idempotency_key || ':' || coalesce(p_decision, '');
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
    if v_d.status in ('approved', 'rejected') and v_d.decide_key = v_key then
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
        update kb.knowledge_drafts set status = 'approved', decided_by = v_actor, decided_at = clock_timestamp(), decide_key = v_key, updated_at = clock_timestamp() where id = p_draft_id;
    else
        v_ver := null;
        update kb.knowledge_drafts set status = 'rejected', decided_by = v_actor, decided_at = clock_timestamp(), reject_reason = p_reason, decide_key = v_key, updated_at = clock_timestamp() where id = p_draft_id;
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_d.origin, p_clinic_id, v_actor, 'doctor', case when p_decision = 'approve' then 'kb.knowledge_draft.approved' else 'kb.knowledge_draft.rejected' end, 'kb.knowledge_drafts', p_draft_id::text,
            jsonb_build_object('kind', v_d.kind, 'code', v_d.code, 'version', v_ver), 'sys:knowledge-draft:' || gen_random_uuid()::text);
    return query select 'recorded'::text, v_ver;
end
$$;

-- ---- family links: verify re-checks the requester; actor-bound key; past-tense audit names ----------------------------------------------------------

create or replace function iam.family_link_decide(
    p_clinic_id uuid, p_link_id uuid, p_decision text, p_dependant_patient_id uuid, p_dependant_age_years integer, p_dependant_consent boolean, p_reason text, p_idempotency_key text
) returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_l     record;
    v_rule  record;
    v_key   text := 'family-decide:' || auth.uid()::text || ':' || p_idempotency_key || ':' || coalesce(p_decision, '');
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
    if v_l.status in ('verified', 'rejected') and v_l.decide_key = v_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_l.status <> 'requested' then
        return query select 'invalid_state'::text;
        return;
    end if;
    if p_decision = 'reject' then
        update iam.family_links set status = 'rejected', reject_reason = p_reason, decided_by = v_actor, decided_at = clock_timestamp(), decide_key = v_key where id = p_link_id;
    else
        select r.min_dependant_age_years, r.max_dependant_age_years, r.requires_dependant_consent into v_rule from iam.family_link_rules r where r.clinic_id = p_clinic_id and r.link_type = v_l.link_type;
        if not found then
            return query select 'not_configured'::text;
            return;
        end if;
        if not exists (select 1 from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
                        where pt.id = v_l.requester_patient_id and pt.clinic_id = p_clinic_id and pt.account_id = v_l.requester_account_id and pt.erased_at is null) then
            return query select 'requester_not_active'::text;  -- the requester's record was erased, moved or their account closed since the request
            return;
        end if;
        if not exists (select 1 from mpi.patients pt where pt.id = p_dependant_patient_id and pt.clinic_id = p_clinic_id and pt.origin = v_l.origin and pt.erased_at is null)
           or p_dependant_patient_id = v_l.requester_patient_id then
            raise exception 'decision refused' using errcode = '42501';
        end if;
        if exists (select 1 from iam.family_links x where x.clinic_id = p_clinic_id and x.requester_account_id = v_l.requester_account_id and x.dependant_patient_id = p_dependant_patient_id and x.status = 'verified') then
            return query select 'already_linked'::text;  -- a second verified link to the same dependant would break the one-verified index
            return;
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
               consent_recorded_at = case when p_dependant_consent then clock_timestamp() else null end, decided_by = v_actor, decided_at = clock_timestamp(), decide_key = v_key
         where id = p_link_id;
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_l.origin, p_clinic_id, v_actor, 'front_desk', case when p_decision = 'verify' then 'iam.family_link.verified' else 'iam.family_link.rejected' end, 'iam.family_links', p_link_id::text,
            jsonb_build_object('link_type', v_l.link_type), 'sys:family-link:' || gen_random_uuid()::text);
    return query select 'recorded'::text;
end
$$;

-- ---- the front desk revokes a link --------------------------------------------------------------------------------------------------------------------

create function iam.family_link_staff_revoke(p_clinic_id uuid, p_link_id uuid, p_reason text, p_idempotency_key text)
    returns table (o_result text)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_l     record;
    v_key   text := 'family-staff-revoke:' || auth.uid()::text || ':' || p_idempotency_key;
begin
    if v_actor is null or p_clinic_id is null or p_link_id is null or p_reason is null or p_reason !~ '^[a-z][a-z0-9_]{0,59}$' or p_idempotency_key is null
       or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session() or not iam.has_clinic_role(p_clinic_id, array['front_desk']) then
        raise exception 'revoke refused' using errcode = '42501';
    end if;
    select l.id, l.status, l.revoke_key, l.origin into v_l from iam.family_links l where l.id = p_link_id and l.clinic_id = p_clinic_id for update;
    if not found then
        raise exception 'revoke refused' using errcode = '42501';
    end if;
    if v_l.status = 'revoked' and v_l.revoke_key = v_key then
        return query select 'replay'::text;
        return;
    end if;
    if v_l.status not in ('requested', 'verified') then
        return query select 'invalid_state'::text;
        return;
    end if;
    update iam.family_links set status = 'revoked', revoked_at = clock_timestamp(), revoked_by = v_actor, revoke_reason = p_reason, revoke_key = v_key where id = p_link_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_l.origin, p_clinic_id, v_actor, 'front_desk', 'iam.family_link.staff_revoked', 'iam.family_links', p_link_id::text, jsonb_build_object('reason', p_reason), 'sys:family-link:' || gen_random_uuid()::text);
    return query select 'recorded'::text;
end
$$;

revoke all on function iam.family_link_staff_revoke(uuid, uuid, text, text) from public;
grant execute on function iam.family_link_staff_revoke(uuid, uuid, text, text) to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.family_link_staff_revoke(uuid, uuid, text, text) from %I', r);
        end if;
    end loop;
end $$;
