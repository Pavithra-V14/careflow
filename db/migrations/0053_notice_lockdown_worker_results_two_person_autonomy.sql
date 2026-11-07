-- 0053_notice_lockdown_worker_results_two_person_autonomy (owner decisions of 2026-11-07/08; BUILD-SPEC.md 12.1, 12.5, 18.1, 20.1; CLAUDE.md rules 3, 5, 8, 10). Fix forward: no merged migration is edited.
--
-- A. Consent notices are published ONLY by migration or by the owner-run seed script (scripts/seed_consent_notices.py, which connects as the migrator and writes the audit row). iam.publish_consent_notice (0051)
--    is DROPPED: no application role, and so no clinic admin, can publish platform-wide text any more. The application can only READ notices.
-- B. The queue worker reads a result only when something POINTS to it: a CLAIMED care_plan_result job naming it (the job the worker is running; the outbox event itself is permanent and is not enough), or an open /
--    claimed wait of a plan of the same patient whose prefix the result's code carries. It no longer reads every result of the clinic. RESIDUAL: the step guard's "not superseded" test runs as the caller and so cannot see a superseding result the worker may
--    not read; the doctor-side confirmation path and the verified / prefix / patient checks still apply.
-- C. The two-person switch to AUTONOMOUS (12.5), as the owner asked: two DIFFERENT admins of the clinic. One proposes (ops.autonomy_proposals); a different admin approves, and only the approval function can write an
--    `autonomous` row into the autonomy history (the direct insert policy still allows shadow and assisted only, so a single admin can never set it). A proposal expires after 7 days; any admin may reject; lowering
--    the mode to shadow or assisted stays a one-admin act. Both steps need a verified second factor. The ENABLING SETTING (CAREFLOW_AUTONOMOUS_MODE_ENABLED, unset by default) and the clinical-profile sign-off
--    (CAREFLOW_CLINICAL_AUTONOMY_SIGNOFF) are checked by the application; the database cannot see settings. The approval-matrix rows that are locked toward a human or toward automatic are code, not data, and are
--    untouched by any stored mode.

-- A
drop function iam.publish_consent_notice(text, integer, text);

-- B
drop policy results_select_worker on clinical.results;
create policy results_select_worker on clinical.results for select to careflow_app
    using (
        iam.has_clinic_role(clinic_id, array['queue_worker'])
        and (
            exists (select 1 from ops.queue q where q.clinic_id = results.clinic_id and q.task_type = 'care_plan_result' and q.state = 'claimed' and q.payload ->> 'result_id' = results.id::text)
            or exists (
                select 1 from ops.care_plan_waits w
                 where w.clinic_id = results.clinic_id and w.patient_id = results.patient_id and (w.open or w.claimed_result_id = results.id)
                   and left(lower(results.code), length(w.prefix)) = lower(w.prefix)
            )
        )
    );

create index queue_care_plan_result_idx on ops.queue ((payload ->> 'result_id')) where task_type = 'care_plan_result';

-- C
alter table ops.autonomy_changes drop constraint autonomy_changes_mode_check;
alter table ops.autonomy_changes add constraint autonomy_changes_mode_check check (mode in ('shadow', 'assisted', 'autonomous'));
drop policy autonomy_insert on ops.autonomy_changes;
create policy autonomy_insert on ops.autonomy_changes for insert to careflow_app
    with check (mode in ('shadow', 'assisted') and changed_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['admin']));  -- `autonomous` only through the approval function

create table ops.autonomy_proposals (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    proposed_by     uuid not null references iam.accounts (id),
    status          text not null default 'pending' check (status in ('pending', 'approved', 'rejected', 'expired')),
    created_at      timestamptz not null default clock_timestamp(),
    decided_by      uuid references iam.accounts (id),
    decided_at      timestamptz,
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint autonomy_proposals_key_per_clinic unique (clinic_id, idempotency_key),
    constraint autonomy_proposals_decision_pair check ((status = 'pending') = (decided_by is null and decided_at is null)),
    constraint autonomy_proposals_two_people check (status <> 'approved' or decided_by <> proposed_by)
);
create unique index autonomy_proposals_one_pending on ops.autonomy_proposals (clinic_id) where status = 'pending';

create function ops.autonomy_proposals_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        return new;
    end if;
    raise exception 'ops.autonomy_proposals is changed only by ops.resolve_autonomy_proposal' using errcode = 'restrict_violation';
end
$$;
revoke all on function ops.autonomy_proposals_guard() from public;
create trigger autonomy_proposals_no_delete before delete on ops.autonomy_proposals for each row execute function ops.autonomy_proposals_guard();
create trigger autonomy_proposals_no_truncate before truncate on ops.autonomy_proposals for each statement execute function ops.autonomy_proposals_guard();

alter table ops.autonomy_proposals enable row level security;
alter table ops.autonomy_proposals force row level security;
create policy autonomy_proposals_select on ops.autonomy_proposals for select to careflow_app using (iam.has_clinic_role(clinic_id, array['admin']));
create policy autonomy_proposals_insert on ops.autonomy_proposals for insert to careflow_app
    with check (status = 'pending' and decided_by is null and proposed_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['admin']));
create policy autonomy_proposals_aal2_select on ops.autonomy_proposals as restrictive for select to careflow_app using (iam.aal2_session());
create policy autonomy_proposals_aal2_insert on ops.autonomy_proposals as restrictive for insert to careflow_app with check (iam.aal2_session());
revoke all on ops.autonomy_proposals from public;
grant select on ops.autonomy_proposals to careflow_app;
grant insert (clinic_id, origin, proposed_by, idempotency_key) on ops.autonomy_proposals to careflow_app;

create function ops.resolve_autonomy_proposal(p_proposal_id uuid, p_decision text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_p     record;
begin
    if v_actor is null or p_proposal_id is null or p_decision is null or p_decision not in ('approve', 'reject') or not iam.aal2_session() then
        raise exception 'autonomy proposal resolution refused' using errcode = 'insufficient_privilege';
    end if;
    select id, clinic_id, origin, proposed_by, status, created_at into v_p from ops.autonomy_proposals where id = p_proposal_id for update;
    if not found or not iam.has_clinic_role(v_p.clinic_id, array['admin']) then
        raise exception 'autonomy proposal resolution refused' using errcode = 'insufficient_privilege';
    end if;
    if v_p.status <> 'pending' then
        return case when v_p.status = 'approved' and p_decision = 'approve' then 'approved' when v_p.status = 'rejected' and p_decision = 'reject' then 'rejected' else 'not_pending' end;
    end if;
    if p_decision = 'reject' then
        update ops.autonomy_proposals set status = 'rejected', decided_by = v_actor, decided_at = clock_timestamp() where id = p_proposal_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_p.origin, v_p.clinic_id, v_actor, 'admin', 'autonomy.proposal_reject', 'autonomy_proposal', p_proposal_id::text, '{}'::jsonb, 'sys:autonomy-proposal:' || p_proposal_id::text);
        return 'rejected';
    end if;
    if v_p.proposed_by = v_actor then
        return 'same_person';  -- two DIFFERENT admins
    end if;
    if v_p.created_at < clock_timestamp() - interval '7 days' then
        update ops.autonomy_proposals set status = 'expired', decided_by = v_actor, decided_at = clock_timestamp() where id = p_proposal_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_p.origin, v_p.clinic_id, v_actor, 'admin', 'autonomy.proposal_expire', 'autonomy_proposal', p_proposal_id::text, '{}'::jsonb, 'sys:autonomy-proposal:' || p_proposal_id::text);
        return 'expired';
    end if;
    -- the proposer must still be an active admin of the clinic: a demoted or removed proposer's proposal is not approved
    if not exists (select 1 from iam.staff_roles r join iam.accounts a on a.id = r.account_id
                    where r.account_id = v_p.proposed_by and r.clinic_id = v_p.clinic_id and r.role = 'admin' and r.revoked_at is null and a.status = 'active') then
        return 'not_pending';
    end if;
    update ops.autonomy_proposals set status = 'approved', decided_by = v_actor, decided_at = clock_timestamp() where id = p_proposal_id;
    insert into ops.autonomy_changes (clinic_id, origin, mode, changed_by, idempotency_key) values (v_p.clinic_id, v_p.origin, 'autonomous', v_actor, 'autonomy-approve:' || p_proposal_id::text);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_p.origin, v_p.clinic_id, v_actor, 'admin', 'autonomy.proposal_approve', 'autonomy_proposal', p_proposal_id::text, jsonb_build_object('proposed_by', v_p.proposed_by::text), 'sys:autonomy-proposal:' || p_proposal_id::text);
    return 'approved';
end
$$;
revoke all on function ops.resolve_autonomy_proposal(uuid, text) from public;
grant execute on function ops.resolve_autonomy_proposal(uuid, text) to careflow_app;

do $$
declare
    f text;
    r text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0053: the migrating role must bypass row-level security (the approval function relies on it)';
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.autonomy_proposals from %I', r);
            execute format('revoke all on function ops.resolve_autonomy_proposal(uuid, text) from %I', r);
            execute format('revoke all on function ops.autonomy_proposals_guard() from %I', r);
        end if;
    end loop;
    foreach f in array array['ops.resolve_autonomy_proposal(uuid,text)', 'ops.autonomy_proposals_guard()'] loop
        if has_function_privilege('public', f, 'execute') then
            raise exception '0053: % must not be executable by PUBLIC', f;
        end if;
    end loop;
    if has_function_privilege('careflow_app', 'ops.autonomy_proposals_guard()', 'execute') then
        raise exception '0053: the guard must not be executable by the application role';
    end if;
    if has_table_privilege('careflow_app', 'ops.autonomy_proposals', 'update') or has_any_column_privilege('careflow_app', 'ops.autonomy_proposals', 'update')
       or has_table_privilege('careflow_app', 'ops.autonomy_proposals', 'delete') or has_table_privilege('careflow_app', 'ops.autonomy_proposals', 'truncate') then
        raise exception '0053: proposals are changed only by the resolve function';
    end if;
    if not (select c.relrowsecurity and c.relforcerowsecurity from pg_class c where c.oid = 'ops.autonomy_proposals'::regclass) then
        raise exception '0053: ops.autonomy_proposals must have forced row-level security';
    end if;
end $$;
