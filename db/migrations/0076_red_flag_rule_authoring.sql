-- 0076_red_flag_rule_authoring (owner decision 2026-11-13, item 3; BUILD-SPEC.md 5.2, 12.5, 20.2; CLAUDE.md rules 1, 2, 5, 8, 10). Fix forward from 0072; no merged migration is edited.
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off
--
-- A DOCTOR of a clinic can write a DRAFT red-flag rule through the application, in a RESTRICTED language: literal phrases and token sets, matched with word boundaries. NO regular expression can be written
-- (the new door never touches the `patterns` column, which stays empty), so no pattern can run slowly: the "no execution timeout" limit in careflow/rules.py does not apply to what this door writes.
--   * a PHRASE: literal words, matched after the engine's normalisation, on word boundaries (as today).
--   * a TOKEN SET: two to ten single words that must ALL appear as whole words anywhere in the text (any order). Stored in the new column `token_sets` (jsonb: an array of arrays of words).
--   * bounded: at most 100 phrases of 1 to 200 characters, at most 50 token sets of 2 to 10 tokens of 1 to 40 characters, no control characters, at least one criterion.
-- A draft is NEVER active: kb.active_red_flag_rules admits `draft_unreviewed` only to the demo profile (0013); the two-person approval of 0072 is unchanged and now also covers token sets (the immutability guard
-- compares them, the reviewed-content hash includes them, an empty rule has none). The author cannot approve their own draft (0072). A new version of a rule code is a new row (the next number); nothing is edited.
-- Re-issued here with the new content kind: msg.sanitise_matches (a match of kind `token_set` survives), kb.active_red_flag_rules (a rule with only token sets has criteria: otherwise a clear could be recorded
-- against a rule that cannot fire), kb.red_flag_rules_guard and kb.decide_red_flag_rule. The engine change (careflow/rules.py) is the matching code.
-- NEW GRANTS (ledger): EXECUTE on kb.author_red_flag_rule_draft to careflow_app. No table grant changes: the application role still has SELECT only on kb.red_flag_rules.

alter table kb.red_flag_rules
    add column token_sets jsonb not null default '[]'::jsonb check (jsonb_typeof(token_sets) = 'array' and jsonb_array_length(token_sets) <= 50);

-- ---- match kind, active rules, guard, decision (re-issued from 0013 and 0072 with token sets) ----
create or replace function msg.sanitise_matches(p jsonb) returns jsonb
    language plpgsql immutable set search_path = ''
as $$
declare
    v_arr jsonb;
    v_elem jsonb;
    v_kept jsonb := '[]'::jsonb;
    v_n integer := 0;
    v_dropped integer := 0;
    v_truncated boolean := false;
    v_uuid constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
begin
    if p is null then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 0, 'truncated', false);
    end if;
    if jsonb_typeof(p) <> 'object' then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 1, 'truncated', false);
    end if;
    v_arr := p -> 'matches';
    if v_arr is null then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 0, 'truncated', false);
    end if;
    if jsonb_typeof(v_arr) <> 'array' then
        return jsonb_build_object('matches', '[]'::jsonb, 'dropped', 1, 'truncated', false);
    end if;
    for v_elem in select e.value from jsonb_array_elements(v_arr) as e(value) loop
        if jsonb_typeof(v_elem) <> 'object'
           or exists (select 1 from jsonb_object_keys(v_elem) k
                      where k not in ('rule_id', 'rule_code', 'version_id', 'rule_status', 'kind', 'index', 'start', 'end'))
           or coalesce(jsonb_typeof(v_elem -> 'rule_id'), '') <> 'string' or (v_elem ->> 'rule_id') !~ v_uuid
           or coalesce(jsonb_typeof(v_elem -> 'rule_code'), '') <> 'string' or (v_elem ->> 'rule_code') !~ '^[a-z][a-z0-9_]{0,63}$'
           or coalesce(jsonb_typeof(v_elem -> 'rule_status'), '') <> 'string'
           or (v_elem ->> 'rule_status') not in ('draft_unreviewed', 'approved', 'retired')
           or coalesce(jsonb_typeof(v_elem -> 'kind'), '') <> 'string' or (v_elem ->> 'kind') not in ('phrase', 'pattern', 'token_set')
           or coalesce(jsonb_typeof(v_elem -> 'version_id'), '') <> 'number' or (v_elem ->> 'version_id') !~ '^[0-9]{1,7}$'
           or coalesce(jsonb_typeof(v_elem -> 'index'), '') <> 'number' or (v_elem ->> 'index') !~ '^[0-9]{1,3}$'
           or coalesce(jsonb_typeof(v_elem -> 'start'), '') <> 'number' or (v_elem ->> 'start') !~ '^[0-9]{1,7}$'
           or coalesce(jsonb_typeof(v_elem -> 'end'), '') <> 'number' or (v_elem ->> 'end') !~ '^[0-9]{1,7}$'
        then
            v_dropped := v_dropped + 1;
            continue;
        end if;
        if (v_elem ->> 'version_id')::integer not between 1 and 1000000
           or (v_elem ->> 'index')::integer > 499
           or (v_elem ->> 'start')::integer > 1000000 or (v_elem ->> 'end')::integer > 1000000 then
            v_dropped := v_dropped + 1;
            continue;
        end if;
        if v_n >= 50 then
            v_truncated := true;
            continue;
        end if;
        v_kept := v_kept || jsonb_build_array(v_elem);
        v_n := v_n + 1;
    end loop;
    return jsonb_build_object('matches', v_kept, 'dropped', v_dropped, 'truncated', v_truncated);
end
$$;

create or replace function kb.active_red_flag_rules(p_clinic uuid, p_origin text, p_profile text)
    returns table (id uuid, rule_code text, version_id integer, status text, has_criteria boolean)
    language sql stable security definer set search_path = ''
as $$
    select d.id, d.rule_code, d.version_id, d.status, (cardinality(d.phrases) > 0 or cardinality(d.patterns) > 0 or jsonb_array_length(d.token_sets) > 0) as has_criteria
    from (
        select distinct on (coalesce(r.clinic_id, '00000000-0000-0000-0000-000000000000'::uuid), r.rule_code)
               r.id, r.rule_code, r.version_id, r.status, r.phrases, r.patterns, r.token_sets
        from kb.red_flag_rules r
        where (r.clinic_id = p_clinic or r.clinic_id is null)
          and r.origin = p_origin
          and (r.status = 'retired'
               or r.status = 'approved'
               or (r.status = 'draft_unreviewed' and p_profile is not distinct from 'demo'))
        order by coalesce(r.clinic_id, '00000000-0000-0000-0000-000000000000'::uuid), r.rule_code, r.version_id desc
    ) d
    where d.status <> 'retired'
$$;

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
    if not ((old.status = 'draft_unreviewed' and new.status in ('approved', 'retired')) or (old.status = 'approved' and new.status = 'retired')) then
        raise exception 'a red-flag rule version status moves only forward: draft to approved or retired, approved to retired' using errcode = 'restrict_violation';
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
    if v_actor is null or p_rule_id is null or p_decision is null or p_decision not in ('approve', 'reject')
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
    -- a replay: this doctor already decided this version
    select * into v_mine from kb.red_flag_rule_decisions where rule_id = p_rule_id and decided_by = v_actor;
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

-- ---- the authoring door ------------------------------------------------------------------------------------------
-- Returns the new version's id. A bad shape raises invalid_parameter_value (22023: the route answers 422); anything about WHO the caller is raises ONE generic insufficient_privilege (42501).
create function kb.author_red_flag_rule_draft(p_clinic_id uuid, p_rule_code text, p_phrases text[], p_token_sets jsonb, p_idempotency_key text) returns uuid
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_key    text;
    v_id     uuid;
    v_set    jsonb;
    v_tok    jsonb;
    v_next   integer;
    v_phrase text;
    v_sets   jsonb := coalesce(p_token_sets, '[]'::jsonb);
    v_phr    text[] := coalesce(p_phrases, '{}'::text[]);
    v_odd    constant text := E'[\\u00a0\\u00ad\\u0600-\\u0605\\u061c\\u180e\\u2000-\\u200f\\u2028-\\u202f\\u205f-\\u206f\\u3000\\ufeff\\ufff9-\\ufffb]';
begin
    if v_actor is null or p_clinic_id is null or p_rule_code is null or p_idempotency_key is null or not iam.aal2_session()
       or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or not iam.has_clinic_role(p_clinic_id, array['doctor']) then
        raise exception 'rule authoring refused' using errcode = 'insufficient_privilege';
    end if;
    if p_rule_code !~ '^[a-z][a-z0-9_]{0,62}$' or jsonb_typeof(v_sets) <> 'array' or cardinality(v_phr) > 100 or jsonb_array_length(v_sets) > 50
       or (cardinality(v_phr) = 0 and jsonb_array_length(v_sets) = 0) then
        raise exception 'rule shape refused' using errcode = 'invalid_parameter_value';
    end if;
    foreach v_phrase in array v_phr loop
        if v_phrase is null or length(btrim(v_phrase)) not between 1 and 200 or v_phrase ~ '[[:cntrl:]]' or v_phrase ~ v_odd then
            raise exception 'rule shape refused' using errcode = 'invalid_parameter_value';
        end if;
    end loop;
    for v_set in select e.value from jsonb_array_elements(v_sets) as e(value) loop
        if jsonb_typeof(v_set) <> 'array' or jsonb_array_length(v_set) not between 2 and 10
           or (select count(distinct lower(x.value #>> '{}')) from jsonb_array_elements(v_set) as x(value) where jsonb_typeof(x.value) = 'string') < 2 then  -- at least two DIFFERENT words
            raise exception 'rule shape refused' using errcode = 'invalid_parameter_value';
        end if;
        for v_tok in select t.value from jsonb_array_elements(v_set) as t(value) loop
            if jsonb_typeof(v_tok) <> 'string' or (v_tok #>> '{}') !~ '^[^[:space:][:cntrl:]]{1,40}$' or (v_tok #>> '{}') ~ v_odd then
                raise exception 'rule shape refused' using errcode = 'invalid_parameter_value';
            end if;
        end loop;
    end loop;
    v_origin := ops.clinic_origin(p_clinic_id);
    v_key := 'author:' || v_actor::text || ':' || p_idempotency_key;
    perform 1 from iam.clinics c where c.id = p_clinic_id for update;  -- serialises version numbering per clinic; a row lock the application role cannot take
    select r.id into v_id from kb.red_flag_rules r
     where r.idempotency_key = v_key and r.clinic_id = p_clinic_id and r.rule_code = p_rule_code and r.phrases = v_phr and r.token_sets = v_sets;
    if found then
        return v_id;  -- a replay: the same draft
    end if;
    if exists (select 1 from kb.red_flag_rules r where r.idempotency_key = v_key) then
        raise exception 'this key was used for a different rule draft' using errcode = 'unique_violation';  -- a different body or rule under the same key
    end if;
    select coalesce(max(r.version_id), 0) + 1 into v_next from kb.red_flag_rules r where r.clinic_id = p_clinic_id and r.rule_code = p_rule_code and r.origin = v_origin;
    if v_next > 1000 then
        raise exception 'rule shape refused' using errcode = 'invalid_parameter_value';
    end if;
    insert into kb.red_flag_rules (clinic_id, rule_code, version_id, status, phrases, patterns, token_sets, governance_metadata, authored_by, origin, idempotency_key)
    values (p_clinic_id, p_rule_code, v_next, 'draft_unreviewed', v_phr, '{}'::text[], v_sets,
            jsonb_build_object('placeholder', '# CLINICAL-PLACEHOLDER: needs clinician sign-off', 'authored_via', 'authoring_route'), v_actor, v_origin, v_key)
    returning id into v_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'doctor', 'red_flag_rule.draft_create', 'red_flag_rule', v_id::text,
            jsonb_build_object('rule_code', p_rule_code, 'version_id', v_next, 'phrases', cardinality(v_phr), 'token_sets', jsonb_array_length(v_sets)), 'sys:rule-draft:' || v_id::text);
    return v_id;
end
$$;
revoke all on function kb.author_red_flag_rule_draft(uuid, text, text[], jsonb, text) from public;
grant execute on function kb.author_red_flag_rule_draft(uuid, text, text[], jsonb, text) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, 'kb.author_red_flag_rule_draft(uuid,text,text[],jsonb,text)', 'execute') then
            raise exception '0076: the authoring function must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'kb.author_red_flag_rule_draft(uuid,text,text[],jsonb,text)', 'execute') then
        raise exception '0076: the authoring function must not be executable by PUBLIC';
    end if;
    if has_table_privilege('careflow_app', 'kb.red_flag_rules', 'insert') or has_table_privilege('careflow_app', 'kb.red_flag_rules', 'update') or has_any_column_privilege('careflow_app', 'kb.red_flag_rules', 'update') then
        raise exception '0076: the application role must not write red-flag rules directly';
    end if;
    if pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%iam.aal2_session()%' or pg_get_functiondef('kb.decide_red_flag_rule(uuid,text,text)'::regprocedure) not like '%token_sets%' then
        raise exception '0076: the decision function must keep its checks and cover token sets';
    end if;
end $$;
