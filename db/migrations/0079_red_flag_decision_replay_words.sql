-- 0079_red_flag_decision_replay_words (owner decision 2026-11-13; fix forward from 0078; CLAUDE.md rules 3, 10). The whole-set review found that a doctor who recorded an approve on a draft that was later WITHDRAWN (or an
-- approve on a version later retired) got the answer `recorded` when replaying it, which reads as "waiting for a second approver". The replay now answers `withdrawn` or `retired`. Nothing else changes: the function is
-- re-issued from 0078 with that one return expression; its checks, writes and ACL are the same (create or replace keeps the grants), and the closing block asserts the important ones.

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
        return case when v_rule.status = 'withdrawn' then 'withdrawn' when v_rule.status = 'retired' then 'retired' when p_decision = 'reject' then 'rejected'
                    when v_rule.status = 'approved' then 'approved' else 'recorded' end;  -- a replay after the version left the draft stage says so (0079)
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
begin
    if pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%iam.aal2_session()%'
       or pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%author_cannot_decide%'
       or pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%token_sets%'
       or pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%red_flag_rule.retire%' then
        raise exception '0079: the decision function must keep its earlier checks';
    end if;
    if has_function_privilege('public', 'kb.decide_red_flag_rule(uuid,text,text)', 'execute') or not has_function_privilege('careflow_app', 'kb.decide_red_flag_rule(uuid,text,text)', 'execute') then
        raise exception '0079: the decision function ACL changed';
    end if;
end $$;
