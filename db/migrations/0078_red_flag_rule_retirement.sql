-- 0078_red_flag_rule_retirement (owner decision 2026-11-13; BUILD-SPEC.md 12.5, 20.2; CLAUDE.md rules 1, 3, 5, 8, 10). Fix forward from 0072, 0076 and 0077; no merged migration is edited.
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off
--
-- RETIRING an APPROVED red-flag rule version is a TWO-PERSON decision through the existing decision door (kb.decide_red_flag_rule gains the decision `retire`):
--   * two DIFFERENT doctors of the clinic, each with a verified second factor, each record a `retire` decision; nothing is retired by one person. The draft-author rule does not apply to retiring (any two different
--     doctors, the author and the approvers included).
--   * only an APPROVED version can be retired (`not_approved` otherwise). A retired version NEVER comes back: the guard allows no transition out of `retired`; a new version is the way back.
--   * a retired NEWEST version switches off the whole rule code, including an older approved version (kb.active_red_flag_rules walks newest first): that is the designed meaning of `retired`, unchanged. Retiring an
--     OLDER version while a newer approved one exists changes nothing in force.
--   * the decision ledger stays append-only; a retire decision is a ledger row like the others (ids and a content hash only in the audit rows). The ledger's one-row-per-person rule now applies per KIND: a doctor
--     may record one approve-or-reject decision and one retire decision on a version (the two approvers of a version may also be the two who retire it).
--   * the guard (re-issued from 0077) now refuses `approved -> retired` unless two different doctors recorded a retire decision, for everyone, the owner included, and no longer allows `draft -> retired` at all
--     (a draft is WITHDRAWN, 0077). Nothing else about the guard changes.
-- NEW GRANTS: none. The existing function keeps its ACL (create or replace).

alter table kb.red_flag_rule_decisions drop constraint red_flag_rule_decisions_decision_check;
alter table kb.red_flag_rule_decisions add constraint red_flag_rule_decisions_decision_check check (decision in ('approve', 'reject', 'retire'));
alter table kb.red_flag_rule_decisions drop constraint red_flag_rule_decisions_one_per_person;
create unique index red_flag_rule_decisions_one_per_person_kind on kb.red_flag_rule_decisions (rule_id, decided_by, (decision = 'retire'));

create or replace function kb.red_flag_rules_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'DELETE' or tg_op = 'TRUNCATE' then
        raise exception 'kb.red_flag_rules rows are never deleted; a new version is a new row' using errcode = 'restrict_violation';
    end if;
    if (old.id, old.clinic_id, old.rule_code, old.version_id, old.phrases, old.patterns, old.token_sets, old.vitals_criteria, old.governance_metadata, old.authored_by, old.origin, old.created_at, old.idempotency_key)
       is distinct from
       (new.id, new.clinic_id, new.rule_code, new.version_id, new.phrases, new.patterns, new.token_sets, new.vitals_criteria, new.governance_metadata, new.authored_by, new.origin, new.created_at, new.idempotency_key) then
        raise exception 'the content of a red-flag rule version is never edited; write a new version' using errcode = 'restrict_violation';
    end if;
    if not ((old.status = 'draft_unreviewed' and new.status in ('approved', 'withdrawn')) or (old.status = 'approved' and new.status = 'retired')) then
        raise exception 'a red-flag rule version status moves only forward: draft to approved or withdrawn, approved to retired; a retired version never comes back' using errcode = 'restrict_violation';
    end if;
    if new.status = 'approved' and (old.approved_by_1 is not null or old.approved_by_2 is not null) then
        raise exception 'approvers are set once' using errcode = 'restrict_violation';
    end if;
    if new.status = 'approved' and (
        new.approved_by_1 is null or new.approved_by_2 is null or new.approved_by_1 = new.approved_by_2
        or (select count(*) from kb.red_flag_rule_decisions d where d.rule_id = old.id and d.decision = 'approve' and d.decided_by in (new.approved_by_1, new.approved_by_2)) <> 2
        or exists (select 1 from kb.red_flag_rule_decisions d where d.rule_id = old.id and d.decision = 'reject')) then
        raise exception 'a version is approved only through kb.decide_red_flag_rule: two approve rows from the two named approvers, and no reject' using errcode = 'restrict_violation';
    end if;
    if new.status = 'retired' and (new.approved_by_1 is distinct from old.approved_by_1 or new.approved_by_2 is distinct from old.approved_by_2 or new.approved_at is distinct from old.approved_at) then
        raise exception 'retiring a version does not change who approved it' using errcode = 'restrict_violation';
    end if;
    if new.status = 'withdrawn' and (new.approved_by_1 is distinct from old.approved_by_1 or new.approved_by_2 is distinct from old.approved_by_2 or new.approved_at is distinct from old.approved_at
                                    or old.status <> 'draft_unreviewed' or new.withdrawn_at is null or new.withdrawn_by is null) then
        raise exception 'only a DRAFT version is withdrawn, by a named person and time, and nothing else changes' using errcode = 'restrict_violation';
    end if;
    if new.status <> 'withdrawn' and (new.withdrawn_by is not null or new.withdrawn_at is not null) then
        raise exception 'withdrawal columns are set only by a withdrawal' using errcode = 'restrict_violation';
    end if;
    if new.status = 'retired' and (select count(distinct d.decided_by) from kb.red_flag_rule_decisions d where d.rule_id = old.id and d.decision = 'retire') < 2 then
        raise exception 'a version is retired only through kb.decide_red_flag_rule: two retire decisions from two different doctors' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;

create or replace function kb.decide_red_flag_rule(p_rule_id uuid, p_decision text, p_idempotency_key text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor    uuid := auth.uid();
    v_rule     record;
    v_mine     record;
    v_hash     text;
    v_key      text;
    v_approvals integer;
    v_first    uuid;
begin
    if v_actor is null or p_rule_id is null or p_decision is null or p_decision not in ('approve', 'reject', 'retire')
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session() then
        raise exception 'red-flag rule decision refused' using errcode = 'insufficient_privilege';
    end if;
    select * into v_rule from kb.red_flag_rules where id = p_rule_id for update;
    if not found then
        raise exception 'red-flag rule decision refused' using errcode = 'insufficient_privilege';
    end if;
    if v_rule.clinic_id is null then
        if not iam.has_any_role(array['doctor']) then
            raise exception 'red-flag rule decision refused' using errcode = 'insufficient_privilege';
        end if;
        return 'platform_rule';  -- who may approve a rule that applies to every clinic is an open owner decision: nobody, through this door
    end if;
    if not iam.has_clinic_role(v_rule.clinic_id, array['doctor']) then
        raise exception 'red-flag rule decision refused' using errcode = 'insufficient_privilege';
    end if;
    v_key := v_actor::text || ':' || p_idempotency_key;
    if p_decision = 'retire' then
        -- RETIRING an approved version: a two-person decision. Any two DIFFERENT doctors of the clinic (the author and the approvers included); nothing is retired by one person.
        select * into v_mine from kb.red_flag_rule_decisions where rule_id = p_rule_id and decided_by = v_actor and decision = 'retire';
        if found then
            return case when v_rule.status = 'retired' then 'retired' else 'recorded' end;  -- a replay by the same doctor
        end if;
        if exists (select 1 from kb.red_flag_rule_decisions where idempotency_key = v_key) then
            raise exception 'red-flag rule decision refused' using errcode = 'insufficient_privilege';  -- the key was used for another decision
        end if;
        if v_rule.status <> 'approved' then
            return 'not_approved';  -- only an approved version is retired; a retired one never comes back (a new version is the way back)
        end if;
        if v_rule.origin is distinct from ops.clinic_origin(v_rule.clinic_id) then
            return 'origin_mismatch';
        end if;
        v_hash := encode(sha256(convert_to(
            v_rule.rule_code || '|' || v_rule.version_id::text || '|' || array_to_string(v_rule.phrases, E'\x1f') || '|' || array_to_string(v_rule.patterns, E'\x1f') || '|' || v_rule.token_sets::text || '|' || v_rule.vitals_criteria::text, 'UTF8')), 'hex');
        insert into kb.red_flag_rule_decisions (rule_id, clinic_id, origin, decision, decided_by, decided_role, content_hash, idempotency_key)
        values (p_rule_id, v_rule.clinic_id, v_rule.origin, 'retire', v_actor, 'doctor', v_hash, v_key);
        select count(*), (array_agg(decided_by order by created_at, id))[1] into v_approvals, v_first
          from kb.red_flag_rule_decisions where rule_id = p_rule_id and decision = 'retire';
        if v_approvals < 2 then
            insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
            values (v_rule.origin, v_rule.clinic_id, v_actor, 'doctor', 'red_flag_rule.retire_recorded', 'red_flag_rule', p_rule_id::text,
                    jsonb_build_object('rule_code', v_rule.rule_code, 'version_id', v_rule.version_id, 'content_hash', v_hash), 'sys:rule-retire:' || p_rule_id::text || ':' || v_actor::text);
            return 'recorded';
        end if;
        update kb.red_flag_rules set status = 'retired', retired_at = clock_timestamp() where id = p_rule_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_rule.origin, v_rule.clinic_id, v_actor, 'doctor', 'red_flag_rule.retire', 'red_flag_rule', p_rule_id::text,
                jsonb_build_object('rule_code', v_rule.rule_code, 'version_id', v_rule.version_id, 'content_hash', v_hash, 'first_retirer', v_first::text), 'sys:rule-retire:' || p_rule_id::text || ':' || v_actor::text);
        return 'retired';
    end if;
    -- a replay: this doctor already decided this version
    select * into v_mine from kb.red_flag_rule_decisions where rule_id = p_rule_id and decided_by = v_actor and decision <> 'retire';
    if found then
        if v_mine.decision <> p_decision then
            return 'already_decided';
        end if;
        return case when p_decision = 'reject' then 'rejected' when v_rule.status = 'approved' then 'approved' else 'recorded' end;
    end if;
    if exists (select 1 from kb.red_flag_rule_decisions where idempotency_key = v_key) then
        raise exception 'red-flag rule decision refused' using errcode = 'insufficient_privilege';  -- the key was used for another rule
    end if;
    if v_rule.status <> 'draft_unreviewed' then
        return 'not_draft';
    end if;
    if v_rule.origin is distinct from ops.clinic_origin(v_rule.clinic_id) then
        return 'origin_mismatch';
    end if;
    if v_rule.authored_by is not null and v_rule.authored_by = v_actor then
        return 'author_cannot_decide';
    end if;
    if p_decision = 'approve' then
        if cardinality(v_rule.phrases) = 0 and cardinality(v_rule.patterns) = 0 and jsonb_array_length(v_rule.token_sets) = 0 and v_rule.vitals_criteria = '{}'::jsonb then
            return 'empty_rule';
        end if;
        if exists (select 1 from kb.red_flag_rule_decisions where rule_id = p_rule_id and decision = 'reject') then
            return 'rejected_earlier';
        end if;
    end if;
    v_hash := encode(sha256(convert_to(
        v_rule.rule_code || '|' || v_rule.version_id::text || '|' || array_to_string(v_rule.phrases, E'\x1f') || '|' || array_to_string(v_rule.patterns, E'\x1f') || '|' || v_rule.token_sets::text || '|' || v_rule.vitals_criteria::text, 'UTF8')), 'hex');
    insert into kb.red_flag_rule_decisions (rule_id, clinic_id, origin, decision, decided_by, decided_role, content_hash, idempotency_key)
    values (p_rule_id, v_rule.clinic_id, v_rule.origin, p_decision, v_actor, 'doctor', v_hash, v_key);
    if p_decision = 'reject' then
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_rule.origin, v_rule.clinic_id, v_actor, 'doctor', 'red_flag_rule.reject', 'red_flag_rule', p_rule_id::text,
                jsonb_build_object('rule_code', v_rule.rule_code, 'version_id', v_rule.version_id, 'content_hash', v_hash), 'sys:rule-decision:' || p_rule_id::text || ':' || v_actor::text);
        return 'rejected';
    end if;
    select count(*), (array_agg(decided_by order by created_at, id))[1] into v_approvals, v_first
      from kb.red_flag_rule_decisions where rule_id = p_rule_id and decision = 'approve';
    if v_approvals < 2 then
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_rule.origin, v_rule.clinic_id, v_actor, 'doctor', 'red_flag_rule.approve_recorded', 'red_flag_rule', p_rule_id::text,
                jsonb_build_object('rule_code', v_rule.rule_code, 'version_id', v_rule.version_id, 'content_hash', v_hash), 'sys:rule-decision:' || p_rule_id::text || ':' || v_actor::text);
        return 'recorded';
    end if;
    update kb.red_flag_rules set status = 'approved', approved_by_1 = v_first, approved_by_2 = v_actor, approved_at = clock_timestamp() where id = p_rule_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_rule.origin, v_rule.clinic_id, v_actor, 'doctor', 'red_flag_rule.approve', 'red_flag_rule', p_rule_id::text,
            jsonb_build_object('rule_code', v_rule.rule_code, 'version_id', v_rule.version_id, 'content_hash', v_hash, 'first_approver', v_first::text), 'sys:rule-decision:' || p_rule_id::text || ':' || v_actor::text);
    return 'approved';
end
$$;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, 'kb.decide_red_flag_rule(uuid,text,text)', 'execute') then
            raise exception '0078: the decision function must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'kb.decide_red_flag_rule(uuid,text,text)', 'execute') or not has_function_privilege('careflow_app', 'kb.decide_red_flag_rule(uuid,text,text)', 'execute') then
        raise exception '0078: the decision function ACL changed';
    end if;
    if pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%iam.aal2_session()%'
       or pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%token_sets%'
       or pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%author_cannot_decide%' then
        raise exception '0078: the decision function must keep its earlier checks';
    end if;
    if has_table_privilege('careflow_app', 'kb.red_flag_rules', 'update') or has_any_column_privilege('careflow_app', 'kb.red_flag_rules', 'update')
       or has_table_privilege('careflow_app', 'kb.red_flag_rule_decisions', 'insert') or has_table_privilege('careflow_app', 'kb.red_flag_rule_decisions', 'update') then
        raise exception '0078: the application role must not write rules or the ledger directly';
    end if;
    if not exists (select 1 from pg_trigger where tgrelid = 'kb.red_flag_rules'::regclass and tgname = 'red_flag_rules_no_edit' and tgenabled = 'A') then
        raise exception '0078: the rule guard must stay ENABLE ALWAYS';
    end if;
end $$;
