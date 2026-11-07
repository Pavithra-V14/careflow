-- 0065_hardening_from_the_set_review (owner request 2026-11-08; CLAUDE.md rules 5, 8, 10). A review of migrations 0029 to 0062 read AS ONE SET (interactions between migrations, not file by file) found the
-- items below. Each is fixed forward here; nothing merged is edited. Items it found and this migration does NOT change are listed in docs/gap-audit.md and docs/design/threat-model.md.
--
-- (1) AAL2 on the irreversible and the privilege-changing functions. The audited erasure functions (0032, re-issued in 0056, 0057, 0059, 0061) and the five staff-account functions (0034) had no verified-second-
--     factor check; `iam.activate_own_account` used a weaker one than iam.aal2_session(). The guards are patched IN PLACE from the stored definitions (`pg_get_functiondef`, so owner, grants and bodies stay
--     as they are) and each patch asserts that it changed something.
-- (2) iam.resolve_staff_recovery (0060): an operator-precedence slip made the account-wide-factor check apply to every action (a cancel of an approved request was answered `target_gone`). Re-issued with the
--     intended grouping: only approve and complete are stopped.
-- (3) Restrictive aal2 on READS added after 0047 that had it only on writes: ops.care_review_samples and mpi.claim_requests (the queue worker and the claimant keep their reads). Known limit: in the demo with the API's
--     second-factor check switched off these listings read as empty rather than refusing; production always has the check.
-- (4) The origin tag is pinned to the clinic's own origin on the three user-writable tables that missed it (care barriers, voice preferences, voice sessions): a user cannot hide a row as `test`.
-- (5) Guards: ops.autonomy_proposals had no UPDATE guard (only the resolve function may move a proposal from pending); mpi.claim_requests had no guard (pending to resolved or rejected, once).
-- (6) A planted `autonomy-approve:<id>` key could block the approval insert: the direct-insert policy now refuses that key prefix.
-- (7) Two alert trigger functions lose their PUBLIC execute (0046 missed them).

do $$
declare f regprocedure; d text; n text;
begin
    foreach f in array array[
        'mpi.request_patient_erasure(uuid,text,text)'::regprocedure,
        'mpi.approve_patient_erasure(uuid,text)'::regprocedure,
        'mpi.cancel_patient_erasure(uuid)'::regprocedure,
        'iam.invite_staff_account(uuid,uuid,text,text)'::regprocedure,
        'iam.grant_staff_role(uuid,uuid,text,text)'::regprocedure,
        'iam.revoke_staff_role(uuid,uuid,text,text)'::regprocedure,
        'iam.set_staff_account_status(uuid,text,text)'::regprocedure
    ] loop
        d := pg_get_functiondef(f);
        n := regexp_replace(d, 'if v_actor is null or ', 'if v_actor is null or not iam.aal2_session() or ');  -- the first occurrence only: the opening guard
        if n = d then
            raise exception '0065: the opening guard was not found in %', f;
        end if;
        execute n;
    end loop;
    d := pg_get_functiondef('iam.activate_own_account()'::regprocedure);
    n := replace(d, 'v_claims ->> ''aal'' is distinct from ''aal2''', 'not iam.aal2_session()');
    if n = d then
        raise exception '0065: the activate_own_account guard was not found';
    end if;
    execute n;
end $$;

create or replace function iam.resolve_staff_recovery(p_request_id uuid, p_action text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_r     record;
begin
    if v_actor is null or p_request_id is null or p_action is null or p_action not in ('approve', 'complete', 'cancel') or not iam.aal2_session() then
        raise exception 'recovery refused' using errcode = '42501';
    end if;
    select r.id, r.clinic_id, r.origin, r.target_account_id, r.requested_by, r.approved_by, r.status into v_r from iam.recovery_requests r where r.id = p_request_id for update;
    if not found or not iam.has_clinic_role(v_r.clinic_id, array['admin']) then
        raise exception 'recovery refused' using errcode = '42501';
    end if;
    if p_action in ('approve', 'complete') and v_r.status in ('requested', 'approved')
       and (not exists (select 1 from iam.accounts a join iam.staff_roles sr on sr.account_id = a.id
                        where a.id = v_r.target_account_id and a.account_type = 'staff' and a.status = 'active' and sr.clinic_id = v_r.clinic_id and sr.revoked_at is null)
            or exists (select 1 from iam.staff_roles o where o.account_id = v_r.target_account_id and o.clinic_id <> v_r.clinic_id and o.revoked_at is null)) then  -- the account-wide factor: a role gained elsewhere since the request stops it too
        return 'target_gone';  -- the account was suspended or lost its role since the request
    end if;
    if p_action = 'approve' then
        if v_r.status = 'approved' and v_r.approved_by = v_actor then return 'approved'; end if;  -- a replay by the approver
        if v_r.status <> 'requested' then return 'not_requested'; end if;
        if v_actor = v_r.requested_by or v_actor = v_r.target_account_id then return 'same_person'; end if;  -- a SECOND admin: neither the requester nor the account being recovered
        update iam.recovery_requests set status = 'approved', approved_by = v_actor, decided_at = clock_timestamp() where id = p_request_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_r.origin, v_r.clinic_id, v_actor, 'admin', 'staff.recovery_approve', 'account', v_r.target_account_id::text, jsonb_build_object('request_id', p_request_id::text), 'sys:staff-recovery-approve:' || p_request_id::text);
        return 'approved';
    elsif p_action = 'complete' then
        if v_r.status = 'completed' then return 'completed'; end if;
        if v_r.status <> 'approved' then return 'not_approved'; end if;
        if v_actor = v_r.target_account_id then return 'same_person'; end if;
        update iam.recovery_requests set status = 'completed' where id = p_request_id;
        -- the caller resets the factors through the Auth admin port ONLY on this answer, in this same transaction: if the port fails the request is rolled back to approved
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_r.origin, v_r.clinic_id, v_actor, 'admin', 'staff.recovery_complete', 'account', v_r.target_account_id::text, jsonb_build_object('request_id', p_request_id::text), 'sys:staff-recovery-complete:' || p_request_id::text);
        return 'completed_now';
    else
        if v_r.status = 'cancelled' then return 'cancelled'; end if;
        if v_r.status not in ('requested', 'approved') then return 'not_open'; end if;
        if v_actor = v_r.target_account_id then return 'same_person'; end if;
        update iam.recovery_requests set status = 'cancelled', decided_at = clock_timestamp() where id = p_request_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_r.origin, v_r.clinic_id, v_actor, 'admin', 'staff.recovery_cancel', 'account', v_r.target_account_id::text, jsonb_build_object('request_id', p_request_id::text), 'sys:staff-recovery-cancel:' || p_request_id::text);
        return 'cancelled';
    end if;
end
$$;

-- (3) restrictive aal2 on reads
-- (ops.autonomy_changes is NOT given a restrictive read: the stored mode may only TIGHTEN the configured policy, and a read hidden from a session without aal2 would make the policy fall back to the weaker
-- configured one. The history route is gated by the API's second-factor check; the table stays readable to its roles.)
create policy care_samples_aal2_select on ops.care_review_samples as restrictive for select to careflow_app
    using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy claim_requests_aal2_select on mpi.claim_requests as restrictive for select to careflow_app
    using (iam.aal2_session() or account_id = auth.uid());

-- (4) origin pinned to the clinic
alter policy care_barriers_insert on ops.care_barriers
    with check (status = 'open' and resolved_by is null and resolved_at is null and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin']));
alter policy voice_sessions_insert_own on msg.voice_sessions
    with check (
        account_id = auth.uid() and status = 'open' and ended_at is null and origin = ops.clinic_origin(clinic_id)
        and exists (select 1 from iam.accounts a where a.id = auth.uid() and a.account_type = 'patient' and a.status = 'active')
        and exists (select 1 from msg.voice_preferences p where p.account_id = auth.uid() and p.voice_enabled)
        and exists (select 1 from mpi.patients pt where pt.account_id = auth.uid() and pt.clinic_id = voice_sessions.clinic_id and pt.erased_at is null)
    );
alter policy voice_prefs_insert_own on msg.voice_preferences
    with check (account_id = auth.uid() and exists (select 1 from iam.accounts a where a.id = account_id and a.account_type = 'patient' and a.status = 'active' and a.origin = voice_preferences.origin));

-- (5) guards
create function ops.autonomy_proposals_update_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if old.status = 'pending' and new.status in ('approved', 'rejected', 'expired')
       and (to_jsonb(new) - 'status' - 'decided_by' - 'decided_at') = (to_jsonb(old) - 'status' - 'decided_by' - 'decided_at') then
        return new;
    end if;
    raise exception 'ops.autonomy_proposals is changed only by ops.resolve_autonomy_proposal' using errcode = 'restrict_violation';
end
$$;
revoke all on function ops.autonomy_proposals_update_guard() from public;
create trigger autonomy_proposals_no_update before update on ops.autonomy_proposals for each row execute function ops.autonomy_proposals_update_guard();

create function mpi.claim_requests_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' and old.status = 'pending' and new.status in ('resolved', 'rejected')
       and (to_jsonb(new) - 'status' - 'resolved_by' - 'resolved_at' - 'resolved_patient_id') = (to_jsonb(old) - 'status' - 'resolved_by' - 'resolved_at' - 'resolved_patient_id') then
        return new;
    end if;
    raise exception 'mpi.claim_requests moves from pending to resolved or rejected, once' using errcode = 'restrict_violation';
end
$$;
revoke all on function mpi.claim_requests_guard() from public;
create trigger claim_requests_guard before update or delete on mpi.claim_requests for each row execute function mpi.claim_requests_guard();
create trigger claim_requests_no_truncate before truncate on mpi.claim_requests for each statement execute function mpi.claim_requests_guard();
alter table ops.autonomy_proposals enable always trigger autonomy_proposals_no_update;
alter table mpi.claim_requests enable always trigger claim_requests_guard;
alter table mpi.claim_requests enable always trigger claim_requests_no_truncate;

-- (6) a planted approval key
alter policy autonomy_insert on ops.autonomy_changes
    with check (mode in ('shadow', 'assisted') and changed_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['admin']) and idempotency_key not like 'autonomy-approve:%');

-- (7) trigger functions
revoke all on function ops.alerts_no_delete(), ops.alerts_ack_once() from public;

do $$
declare r text; f text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0065: the migrating role must bypass row-level security';
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function ops.alerts_no_delete(), ops.alerts_ack_once(), ops.autonomy_proposals_update_guard(), mpi.claim_requests_guard() from %I', r);
        end if;
    end loop;
    foreach f in array array['mpi.approve_patient_erasure(uuid,text)', 'mpi.request_patient_erasure(uuid,text,text)', 'mpi.cancel_patient_erasure(uuid)', 'iam.invite_staff_account(uuid,uuid,text,text)',
                             'iam.grant_staff_role(uuid,uuid,text,text)', 'iam.revoke_staff_role(uuid,uuid,text,text)', 'iam.set_staff_account_status(uuid,text,text)', 'iam.activate_own_account()', 'iam.resolve_staff_recovery(uuid,text)'] loop
        if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0065: % must stay executable by the application login only', f;
        end if;
        if f <> 'iam.resolve_staff_recovery(uuid,text)'
           and (position('not iam.aal2_session()' in pg_get_functiondef(f::regprocedure)) = 0
                or (position('has_clinic_role' in pg_get_functiondef(f::regprocedure)) > 0 and position('not iam.aal2_session()' in pg_get_functiondef(f::regprocedure)) > position('has_clinic_role' in pg_get_functiondef(f::regprocedure)))) then
            raise exception '0065: % has no aal2 check before its role check', f;
        end if;
    end loop;
    foreach f in array array['ops.alerts_no_delete()', 'ops.alerts_ack_once()', 'ops.autonomy_proposals_update_guard()', 'mpi.claim_requests_guard()'] loop
        if has_function_privilege('public', f, 'execute') then
            raise exception '0065: trigger function % must not be executable by PUBLIC', f;
        end if;
    end loop;
end $$;
