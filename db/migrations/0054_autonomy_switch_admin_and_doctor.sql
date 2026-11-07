-- 0054_autonomy_switch_admin_and_doctor (owner decision of 2026-11-08: follow BUILD-SPEC.md 12.5; CLAUDE.md rules 8, 10). Fix forward from 0053: no merged or earlier migration is edited.
--
-- 0053 built the switch to `autonomous` as "two different admins". The spec (12.5) says an admin AND a doctor. This migration changes the rule to: the proposer and the approver are DIFFERENT people of the same
-- clinic, one an active ADMIN and the other an active DOCTOR. Either may propose; the approver must hold the OTHER role. Both steps need a verified second factor. Any admin or doctor of the clinic may reject.
-- A proposal records the role it was made as (`proposer_role`), and the approval re-checks that the proposer still holds it. Lowering the mode (shadow / assisted) stays a one-admin act. The
-- enabling setting and the clinical-profile sign-off remain application settings. An account holding both roles is still one person: the approver must be a different ACCOUNT.

alter table ops.autonomy_proposals add column proposer_role text;
update ops.autonomy_proposals set proposer_role = 'admin' where proposer_role is null;  -- every proposal made under 0053 was made by an admin
alter table ops.autonomy_proposals alter column proposer_role set not null;
alter table ops.autonomy_proposals add constraint autonomy_proposals_proposer_role check (proposer_role in ('admin', 'doctor'));

drop policy autonomy_proposals_select on ops.autonomy_proposals;
create policy autonomy_proposals_select on ops.autonomy_proposals for select to careflow_app using (iam.has_clinic_role(clinic_id, array['admin', 'doctor']));
drop policy autonomy_proposals_insert on ops.autonomy_proposals;
create policy autonomy_proposals_insert on ops.autonomy_proposals for insert to careflow_app
    with check (status = 'pending' and decided_by is null and proposed_by = auth.uid() and origin = ops.clinic_origin(clinic_id)
                and proposer_role in ('admin', 'doctor') and iam.has_clinic_role(clinic_id, array[proposer_role]));  -- the role it claims is a role the proposer holds NOW
grant insert (proposer_role) on ops.autonomy_proposals to careflow_app;

create or replace function ops.resolve_autonomy_proposal(p_proposal_id uuid, p_decision text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor    uuid := auth.uid();
    v_p        record;
    v_required text;
begin
    if v_actor is null or p_proposal_id is null or p_decision is null or p_decision not in ('approve', 'reject') or not iam.aal2_session() then
        raise exception 'autonomy proposal resolution refused' using errcode = 'insufficient_privilege';
    end if;
    select id, clinic_id, origin, proposed_by, proposer_role, status, created_at into v_p from ops.autonomy_proposals where id = p_proposal_id for update;
    if not found or not iam.has_clinic_role(v_p.clinic_id, array['admin', 'doctor']) then
        raise exception 'autonomy proposal resolution refused' using errcode = 'insufficient_privilege';
    end if;
    if v_p.status <> 'pending' then
        return case when v_p.status = 'approved' and p_decision = 'approve' then 'approved' when v_p.status = 'rejected' and p_decision = 'reject' then 'rejected' else 'not_pending' end;
    end if;
    if p_decision = 'reject' then
        update ops.autonomy_proposals set status = 'rejected', decided_by = v_actor, decided_at = clock_timestamp() where id = p_proposal_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_p.origin, v_p.clinic_id, v_actor, case when iam.has_clinic_role(v_p.clinic_id, array['admin']) then 'admin' else 'doctor' end, 'autonomy.proposal_reject', 'autonomy_proposal', p_proposal_id::text, '{}'::jsonb, 'sys:autonomy-proposal:' || p_proposal_id::text);
        return 'rejected';
    end if;
    if v_p.proposed_by = v_actor then
        return 'same_person';  -- two DIFFERENT people
    end if;
    v_required := case v_p.proposer_role when 'admin' then 'doctor' else 'admin' end;  -- the approver holds the OTHER role (12.5: an admin AND a doctor)
    if not iam.has_clinic_role(v_p.clinic_id, array[v_required]) then
        return 'wrong_role';
    end if;
    if v_p.created_at < clock_timestamp() - interval '7 days' then
        update ops.autonomy_proposals set status = 'expired', decided_by = v_actor, decided_at = clock_timestamp() where id = p_proposal_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_p.origin, v_p.clinic_id, v_actor, v_required, 'autonomy.proposal_expire', 'autonomy_proposal', p_proposal_id::text, '{}'::jsonb, 'sys:autonomy-proposal:' || p_proposal_id::text);
        return 'expired';
    end if;
    -- the proposer must still be an active holder of the role they proposed as
    if not exists (select 1 from iam.staff_roles r join iam.accounts a on a.id = r.account_id
                    where r.account_id = v_p.proposed_by and r.clinic_id = v_p.clinic_id and r.role = v_p.proposer_role and r.revoked_at is null and a.status = 'active' and a.account_type = 'staff') then
        -- the proposer no longer holds the role they proposed as: the proposal is closed (it must not sit pending and block a new one) and the approval is refused
        update ops.autonomy_proposals set status = 'expired', decided_by = v_actor, decided_at = clock_timestamp() where id = p_proposal_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_p.origin, v_p.clinic_id, v_actor, v_required, 'autonomy.proposal_expire', 'autonomy_proposal', p_proposal_id::text, jsonb_build_object('reason', 'proposer_role_lost'), 'sys:autonomy-proposal:' || p_proposal_id::text);
        return 'expired';
    end if;
    update ops.autonomy_proposals set status = 'approved', decided_by = v_actor, decided_at = clock_timestamp() where id = p_proposal_id;
    insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values (v_p.clinic_id, v_p.origin, 'autonomous', v_actor, 'autonomy-approve:' || p_proposal_id::text);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_p.origin, v_p.clinic_id, v_actor, v_required, 'autonomy.proposal_approve', 'autonomy_proposal', p_proposal_id::text,
            jsonb_build_object('proposed_by', v_p.proposed_by::text, 'proposer_role', v_p.proposer_role), 'sys:autonomy-proposal:' || p_proposal_id::text);
    return 'approved';
end
$$;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, 'ops.resolve_autonomy_proposal(uuid,text)', 'execute') then
            raise exception '0054: the resolve function must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'ops.resolve_autonomy_proposal(uuid,text)', 'execute') then
        raise exception '0054: the resolve function must not be executable by PUBLIC';
    end if;
    if not has_function_privilege('careflow_app', 'ops.resolve_autonomy_proposal(uuid,text)', 'execute') then
        raise exception '0054: the application role must be able to execute the resolve function';
    end if;
    if has_table_privilege('careflow_app', 'ops.autonomy_proposals', 'update') or has_any_column_privilege('careflow_app', 'ops.autonomy_proposals', 'update') then
        raise exception '0054: proposals are changed only by the resolve function';
    end if;
end $$;
