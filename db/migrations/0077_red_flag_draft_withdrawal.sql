-- 0077_red_flag_draft_withdrawal (owner decision 2026-11-13, item 6; BUILD-SPEC.md 20.2; CLAUDE.md rules 1, 5, 8, 10). Fix forward from 0072 and 0076; no merged migration is edited.
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off
--
-- A DOCTOR of the clinic can WITHDRAW a DRAFT red-flag rule version. Withdrawal is its own status, `withdrawn`, NOT `retired`: a retired newest version withdraws the whole rule code, including an older APPROVED version
-- (kb.active_red_flag_rules walks newest first), so retiring a draft would silently switch off an approved rule. A withdrawn draft is simply out of the way: it is never in force in any profile, it can never be approved
-- (the decision function answers `not_draft`), and the next draft is the next version number. Only a draft can be withdrawn; an APPROVED version is never touched by this door.
-- The rule's row is kept (append-only in spirit: content never changes, nothing is deleted); the guard allows the one new transition draft -> withdrawn, records who withdrew it and when, and refuses to change anything else.
-- NEW GRANTS (ledger): EXECUTE on kb.withdraw_red_flag_draft to careflow_app. No table grant changes.

alter table kb.red_flag_rules drop constraint red_flag_rules_status_check;
alter table kb.red_flag_rules add constraint red_flag_rules_status_check check (status in ('draft_unreviewed', 'approved', 'retired', 'withdrawn'));
alter table kb.red_flag_rules add column withdrawn_by uuid references iam.accounts (id), add column withdrawn_at timestamptz;
alter table kb.red_flag_rules add constraint red_flag_rules_withdrawn_whole check ((status = 'withdrawn') = (withdrawn_by is not null and withdrawn_at is not null));

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
    if not ((old.status = 'draft_unreviewed' and new.status in ('approved', 'retired', 'withdrawn')) or (old.status = 'approved' and new.status = 'retired')) then
        raise exception 'a red-flag rule version status moves only forward: draft to approved, retired or withdrawn, approved to retired' using errcode = 'restrict_violation';
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
    return new;
end
$$;

-- Returns 'withdrawn' (now, or already by this doctor: a replay) or 'not_draft'. Anything about who the caller is raises ONE generic insufficient_privilege.
create function kb.withdraw_red_flag_draft(p_rule_id uuid, p_idempotency_key text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_rule  record;
begin
    if v_actor is null or p_rule_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 or not iam.aal2_session() then
        raise exception 'rule withdrawal refused' using errcode = 'insufficient_privilege';
    end if;
    select * into v_rule from kb.red_flag_rules where id = p_rule_id for update;
    if not found or v_rule.clinic_id is null or not iam.has_clinic_role(v_rule.clinic_id, array['doctor']) then
        raise exception 'rule withdrawal refused' using errcode = 'insufficient_privilege';
    end if;
    if v_rule.status = 'withdrawn' then
        if v_rule.withdrawn_by = v_actor then
            return 'withdrawn';  -- a replay by the same doctor
        end if;
        return 'not_draft';
    end if;
    if v_rule.status <> 'draft_unreviewed' then
        return 'not_draft';  -- an approved or retired version is never touched here
    end if;
    update kb.red_flag_rules set status = 'withdrawn', withdrawn_by = v_actor, withdrawn_at = clock_timestamp() where id = p_rule_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_rule.origin, v_rule.clinic_id, v_actor, 'doctor', 'red_flag_rule.draft_withdraw', 'red_flag_rule', p_rule_id::text,
            jsonb_build_object('rule_code', v_rule.rule_code, 'version_id', v_rule.version_id), 'sys:rule-withdraw:' || p_rule_id::text);
    return 'withdrawn';
end
$$;
revoke all on function kb.withdraw_red_flag_draft(uuid, text) from public;
grant execute on function kb.withdraw_red_flag_draft(uuid, text) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, 'kb.withdraw_red_flag_draft(uuid,text)', 'execute') then
            raise exception '0077: the withdrawal function must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'kb.withdraw_red_flag_draft(uuid,text)', 'execute') then
        raise exception '0077: the withdrawal function must not be executable by PUBLIC';
    end if;
    if has_table_privilege('careflow_app', 'kb.red_flag_rules', 'update') or has_any_column_privilege('careflow_app', 'kb.red_flag_rules', 'update') then
        raise exception '0077: the application role must not write red-flag rules directly';
    end if;
    if not exists (select 1 from pg_trigger where tgrelid = 'kb.red_flag_rules'::regclass and tgname = 'red_flag_rules_no_edit' and tgenabled = 'A') then
        raise exception '0077: the rule guard must stay ENABLE ALWAYS';
    end if;
end $$;
