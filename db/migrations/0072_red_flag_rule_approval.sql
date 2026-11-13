-- 0072_red_flag_rule_approval (owner instruction 2026-11-13; BUILD-SPEC.md 5.2, 12.5, 20.2; CLAUDE.md rules 1, 2, 3, 5, 8, 10; closes the MECHANISM half of pre-launch item 19).
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off
--
-- This migration contains NO clinical content: no phrase, pattern, boundary or rule. It adds the way a clinician-authored DRAFT rule version becomes `approved` under the two-person rule (20.2).
--
--  * NOTHING is approved by default. A rule version is created `draft_unreviewed` (by the migrator today: authoring stays outside the application, because patterns have no execution timeout yet; see careflow/rules.py).
--    A draft is never in force in the `clinical` profile (kb.active_red_flag_rules, 0013, unchanged).
--  * kb.red_flag_rule_decisions: an APPEND-ONLY ledger, one row per (rule version, doctor): the decision, who, when, and a hash of the exact content that was reviewed.
--  * kb.decide_red_flag_rule(rule, decision, key): the only door (the guard below also refuses `approved` without the two matching ledger rows, for the owner too). A DOCTOR of the rule's clinic, with a verified second factor, as themselves. The version becomes `approved` only on the SECOND approval,
--    from a DIFFERENT doctor (also never the version's author). One reject closes the version: it can never be approved; a new version is needed. An empty rule (no criteria) cannot be approved. A rule
--    whose origin is not its clinic's origin cannot be approved (a test rule never goes live). A platform-wide rule (no clinic) is refused here: who may approve a rule that applies to every clinic is an
--    open owner decision. Admins read the ledger; they do not decide.
--  * kb.red_flag_rules becomes CONTENT-IMMUTABLE for everyone, the owner included: delete and truncate are refused; an update may only move the status draft -> approved, draft -> retired or
--    approved -> retired, setting the approver and time columns, and never touches the content (phrases, patterns, vitals, code, version, clinic, origin, authors, metadata). The guards are ENABLE ALWAYS
--    (0068 convention).
--  * Every decision is an audit row written by the function (`sys:` key), carrying ids and the content hash only.
--
-- NEW GRANTS (grants ledger): SELECT on the ledger to careflow_app through a doctor/admin policy; EXECUTE on the function to careflow_app. No insert, update or delete on either table for any application role.

create table kb.red_flag_rule_decisions (
    id              uuid primary key default gen_random_uuid(),
    rule_id         uuid not null references kb.red_flag_rules (id),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    decision        text not null check (decision in ('approve', 'reject')),
    decided_by      uuid not null references iam.accounts (id),
    decided_role    text not null check (decided_role = 'doctor'),
    content_hash    text not null check (content_hash ~ '^[0-9a-f]{64}$'),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null unique check (length(idempotency_key) between 1 and 300),
    constraint red_flag_rule_decisions_one_per_person unique (rule_id, decided_by)
);
create index red_flag_rule_decisions_rule_idx on kb.red_flag_rule_decisions (rule_id, created_at);

alter table kb.red_flag_rule_decisions enable row level security;
alter table kb.red_flag_rule_decisions force row level security;
create policy red_flag_rule_decisions_select on kb.red_flag_rule_decisions for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['doctor', 'admin']));
revoke all on kb.red_flag_rule_decisions from public;
grant select on kb.red_flag_rule_decisions to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on kb.red_flag_rule_decisions from %I', r);
        end if;
    end loop;
end $$;

create function kb.red_flag_rule_decisions_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'kb.red_flag_rule_decisions is append-only' using errcode = 'restrict_violation';
end
$$;
create trigger red_flag_rule_decisions_no_change before update or delete on kb.red_flag_rule_decisions for each row execute function kb.red_flag_rule_decisions_guard();
create trigger red_flag_rule_decisions_no_truncate before truncate on kb.red_flag_rule_decisions for each statement execute function kb.red_flag_rule_decisions_guard();
alter table kb.red_flag_rule_decisions enable always trigger red_flag_rule_decisions_no_change;
alter table kb.red_flag_rule_decisions enable always trigger red_flag_rule_decisions_no_truncate;

-- ---- the rule versions: content is immutable; only the status may move, forward ------------------------------

create function kb.red_flag_rules_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'DELETE' or tg_op = 'TRUNCATE' then
        raise exception 'kb.red_flag_rules rows are never deleted; a new version is a new row' using errcode = 'restrict_violation';
    end if;
    if (old.id, old.clinic_id, old.rule_code, old.version_id, old.phrases, old.patterns, old.vitals_criteria, old.governance_metadata, old.authored_by, old.origin, old.created_at, old.idempotency_key)
       is distinct from
       (new.id, new.clinic_id, new.rule_code, new.version_id, new.phrases, new.patterns, new.vitals_criteria, new.governance_metadata, new.authored_by, new.origin, new.created_at, new.idempotency_key) then
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
create trigger red_flag_rules_no_edit before update or delete on kb.red_flag_rules for each row execute function kb.red_flag_rules_guard();
create trigger red_flag_rules_no_truncate before truncate on kb.red_flag_rules for each statement execute function kb.red_flag_rules_guard();
alter table kb.red_flag_rules enable always trigger red_flag_rules_no_edit;
alter table kb.red_flag_rules enable always trigger red_flag_rules_no_truncate;

-- ---- the decision --------------------------------------------------------------------------------------------
-- Returns one word: 'recorded' (a first approval; waiting for a second doctor), 'approved' (this approval made the version approved), 'rejected', 'already_decided' (you decided the other way before),
-- 'not_draft', 'origin_mismatch', 'author_cannot_decide', 'empty_rule', 'rejected_earlier', 'platform_rule'. A replay of the same decision returns the same word. Anything that would reveal more than the
-- caller may know (no second factor, not a doctor of the clinic, no such version) raises ONE generic insufficient_privilege.

create function kb.decide_red_flag_rule(p_rule_id uuid, p_decision text, p_idempotency_key text) returns text
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
        if cardinality(v_rule.phrases) = 0 and cardinality(v_rule.patterns) = 0 and v_rule.vitals_criteria = '{}'::jsonb then
            return 'empty_rule';
        end if;
        if exists (select 1 from kb.red_flag_rule_decisions where rule_id = p_rule_id and decision = 'reject') then
            return 'rejected_earlier';
        end if;
    end if;
    v_hash := encode(sha256(convert_to(
        v_rule.rule_code || '|' || v_rule.version_id::text || '|' || array_to_string(v_rule.phrases, E'\x1f') || '|' || array_to_string(v_rule.patterns, E'\x1f') || '|' || v_rule.vitals_criteria::text, 'UTF8')), 'hex');
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
revoke all on function kb.decide_red_flag_rule(uuid, text, text) from public;
grant execute on function kb.decide_red_flag_rule(uuid, text, text) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, 'kb.decide_red_flag_rule(uuid,text,text)', 'execute') then
            raise exception '0072: the decision function must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'kb.decide_red_flag_rule(uuid,text,text)', 'execute') then
        raise exception '0072: the decision function must not be executable by PUBLIC';
    end if;
    if has_table_privilege('careflow_app', 'kb.red_flag_rules', 'insert') or has_table_privilege('careflow_app', 'kb.red_flag_rules', 'update') or has_any_column_privilege('careflow_app', 'kb.red_flag_rules', 'update')
       or has_table_privilege('careflow_app', 'kb.red_flag_rules', 'delete') then
        raise exception '0072: the application role must not write red-flag rules directly';
    end if;
    if has_table_privilege('careflow_app', 'kb.red_flag_rule_decisions', 'insert') or has_table_privilege('careflow_app', 'kb.red_flag_rule_decisions', 'update') or has_table_privilege('careflow_app', 'kb.red_flag_rule_decisions', 'delete') then
        raise exception '0072: the decision ledger is written only by the decision function';
    end if;
    if exists (select 1 from kb.red_flag_rules where status = 'approved' and origin = 'live') then
        raise notice '0072: approved live rules already exist (loaded by the migrator before this door existed)';
    end if;
end $$;

comment on table kb.red_flag_rule_decisions is 'Append-only ledger of the two-person approval of red-flag rule versions (20.2): one row per doctor per version, with a hash of the reviewed content. Written only by kb.decide_red_flag_rule.';
