-- 0051_review_fixes_portal_resolution_worker_wake (owner decisions of 2026-11-07; BUILD-SPEC.md 12.6, 17.2, 18.1; CLAUDE.md rules 3, 5, 8, 10). Fix forward from 0047 to 0050: no merged migration is edited.
--
-- A. Review fixes (the second reviewer pass on 0049 and 0050):
--    * the claim function is race-safe (the record row is locked; a lost race falls through to a pending request instead of returning `claimed`), and its failure limit is also serialised per phone token and clinic;
--    * the erased-patient trigger reveals nothing to a caller who holds no role at the clinic, and takes a share lock so a result or plan cannot race an erasure;
--    * a wait step can no longer be closed without evidence (`closed` is for task steps only); a step closes as met / not_met only while its plan is ACTIVE; a step left in needs_doctor is released by a doctor;
--      a plan cannot be completed while a wait step is still open; a wait can be claimed only while open, and only for a step of kind wait_result whose prefix it carries; the wait-to-step key now carries the clinic.
-- B. Decision 7: a sample must be re-checked by someone other than the person who made the decision (enforced in the guard).
-- C. Decision 8: reading clinical results, care plans, steps and waits needs a verified second factor, like writing. The queue worker (no second factor by nature) is exempt and gets narrow permissive policies, so the
--    outbox-woken Care Journey handler (decision 11) can read results and move plans, steps and waits as the worker identity; it still cannot insert any of them.
-- D. Decision 5: an audited front-desk route resolves pending portal claims. `mpi.resolve_claim_request` (front desk of the clinic, aal2): approve binds the claimant's account to a CONFIRMED, unbound, live record of
--    the same clinic and origin; reject closes the request. A record with no identity event stays unclaimable until the existing confirm-identity route creates one. Every resolution writes an audit row.
-- RESIDUALS (second review): the worker reads every patient's results of its clinic (as nurse and doctor do); an account holding BOTH worker and nurse at one clinic would pass the aal2 exemption (the 0024 exclusivity
--    trigger forbids it for new grants; the stop-gap query of 0024 finds old ones); the consent notice publication writes an audit row with no clinic, which no application role can read; a claim and a resolve
--    for the same account can in a narrow case deadlock (Postgres aborts one).
-- E. Decision 6: `iam.publish_consent_notice` publishes the next version of a consent notice (the text comes from a required application setting, owned by counsel). RESIDUAL: notices are platform-wide and any clinic
--    admin with a verified second factor can call the function with any text through SQL; the route only publishes what the setting says. A platform-level role does not exist (owner decision).

-- A1
create or replace function ops.care_barriers_not_erased() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    -- only a person who holds a role at the clinic can learn that a patient is erased; for anyone else the row-level security refuses the write for its own reasons
    if iam.has_clinic_role(new.clinic_id, array['nurse', 'doctor', 'admin', 'front_desk', 'queue_worker'])
       and exists (select 1 from mpi.patients p where p.id = new.patient_id and p.clinic_id = new.clinic_id and p.erased_at is not null for share of p) then
        raise exception 'no record can be added for an erased patient' using errcode = 'restrict_violation';
    end if;
    return new;
end
$$;

-- A2
create or replace function mpi.claim_patient_record(p_clinic_id uuid, p_phone_token bytea, p_name_dob_token bytea, p_key_version smallint) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_ids    uuid[];
    v_failed integer;
    v_latest text;
    v_n      integer;
begin
    if v_actor is null or p_clinic_id is null or p_phone_token is null or p_name_dob_token is null or p_key_version is null or p_key_version < 1
       or octet_length(p_phone_token) <> 32 or octet_length(p_name_dob_token) <> 32 then
        raise exception 'patient claim refused' using errcode = 'insufficient_privilege';
    end if;
    select origin into v_origin from iam.accounts where id = v_actor and account_type = 'patient' and status = 'active';
    if v_origin is null or not exists (select 1 from iam.clinics where id = p_clinic_id and origin = v_origin) then
        raise exception 'patient claim refused' using errcode = 'insufficient_privilege';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('claim:' || v_actor::text, 0));
    perform pg_advisory_xact_lock(hashtextextended('claim-phone:' || p_clinic_id::text || ':' || encode(p_phone_token, 'hex'), 0));  -- a burst from fresh accounts with one phone token is serialised too
    if exists (select 1 from mpi.patients where account_id = v_actor) then
        return 'already_bound';
    end if;
    select e.action into v_latest
      from iam.consent_events e join iam.consent_notices n on n.id = e.notice_id
     where e.account_id = v_actor and e.purpose = 'care_delivery' and n.version = (select max(version) from iam.consent_notices where purpose = 'care_delivery')
     order by e.occurred_at desc, e.id desc limit 1;
    if v_latest is distinct from 'granted' then
        return 'consent_required';
    end if;
    select count(*) into v_failed from mpi.claim_attempts
     where occurred_at > clock_timestamp() - interval '24 hours' and (account_id = v_actor or (clinic_id = p_clinic_id and phone_token = p_phone_token));
    if v_failed >= 5 then
        return 'locked';
    end if;
    select array_agg(q.id) into v_ids from (
        select p.id
          from mpi.patients p
         where p.clinic_id = p_clinic_id and p.origin = v_origin and p.account_id is null and p.erased_at is null
           and exists (
               select 1 from mpi.identity_confirmations c
                where c.patient_id = p.id and c.clinic_id = p.clinic_id and c.kind = 'confirm'
                  and c.seq = (select max(x.seq) from mpi.identity_confirmations x where x.patient_id = p.id))
           and exists (select 1 from mpi.identity_tokens t where t.patient_id = p.id and t.clinic_id = p.clinic_id and t.kind = 'phone' and t.key_version = p_key_version and t.token = p_phone_token)
           and exists (select 1 from mpi.identity_tokens t where t.patient_id = p.id and t.clinic_id = p.clinic_id and t.kind = 'name_dob' and t.key_version = p_key_version and t.token = p_name_dob_token)
           for update of p
    ) q;
    if coalesce(array_length(v_ids, 1), 0) = 1 then
        update mpi.patients set account_id = v_actor where id = v_ids[1] and account_id is null;
        get diagnostics v_n = row_count;
        if v_n = 1 then
            insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
            values (v_origin, p_clinic_id, v_actor, 'patient', 'patient.claim', 'patient', v_ids[1]::text, '{}'::jsonb, 'sys:patient-claim:' || v_actor::text);
            return 'claimed';
        end if;
        -- the record was taken between the read and the write: nothing was bound, so nothing is reported as claimed
    end if;
    insert into mpi.claim_attempts (account_id, clinic_id, phone_token) values (v_actor, p_clinic_id, p_phone_token);
    insert into mpi.claim_requests (clinic_id, origin, account_id) values (p_clinic_id, v_origin, v_actor) on conflict do nothing;
    return 'pending_review';
end
$$;

-- A3 the wait's key to its step carries the clinic
alter table ops.care_plan_steps add constraint care_plan_steps_plan_step_clinic unique (plan_id, step_key, clinic_id);
do $$
declare c record;
begin
    for c in select conname from pg_constraint where conrelid = 'ops.care_plan_waits'::regclass and confrelid = 'ops.care_plan_steps'::regclass and contype = 'f' loop
        execute format('alter table ops.care_plan_waits drop constraint %I', c.conname);
    end loop;
end $$;
alter table ops.care_plan_waits add constraint care_plan_waits_step_fkey foreign key (plan_id, step_key, clinic_id) references ops.care_plan_steps (plan_id, step_key, clinic_id);

-- A4 guards
create or replace function ops.care_plans_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plans rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.status in ('completed', 'rejected') then
        raise exception 'a finished care plan is final' using errcode = 'restrict_violation';
    end if;
    if new.id <> old.id or new.clinic_id <> old.clinic_id or new.origin <> old.origin or new.patient_id <> old.patient_id or new.plan_ct <> old.plan_ct
       or new.plan_key_version <> old.plan_key_version or new.created_by <> old.created_by or new.created_at <> old.created_at or new.idempotency_key <> old.idempotency_key then
        raise exception 'only the status of a care plan can change' using errcode = 'restrict_violation';
    end if;
    if new.status <> old.status then
        if not ((old.status, new.status) in (('screening', 'awaiting_confirmation'), ('screening', 'held'), ('screening', 'rejected'), ('awaiting_confirmation', 'active'), ('awaiting_confirmation', 'rejected'),
                                             ('awaiting_confirmation', 'held'), ('held', 'awaiting_confirmation'), ('held', 'active'), ('held', 'rejected'), ('active', 'completed'), ('active', 'held'))) then
            raise exception 'that care plan status change is not allowed' using errcode = 'restrict_violation';
        end if;
        if new.status in ('active', 'rejected') and not iam.has_clinic_role(new.clinic_id, array['doctor']) then
            raise exception 'only a doctor can confirm or reject a care plan' using errcode = 'insufficient_privilege';
        end if;
        if new.status = 'active' and not exists (select 1 from ops.care_plan_steps s where s.plan_id = new.id and s.clinic_id = new.clinic_id) then
            raise exception 'a care plan with no steps cannot be activated' using errcode = 'restrict_violation';
        end if;
        if new.status = 'completed' and exists (select 1 from ops.care_plan_steps s where s.plan_id = new.id and s.clinic_id = new.clinic_id and s.kind = 'wait_result' and s.status in ('open', 'needs_doctor')) then
            raise exception 'a care plan cannot be completed while a result it waits for is still open' using errcode = 'restrict_violation';
        end if;
    end if;
    new.updated_at := clock_timestamp();
    return new;
end
$$;

create or replace function ops.care_plan_steps_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plan_steps rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.status <> 'open' and old.status <> 'needs_doctor' then
        raise exception 'a closed care plan step is final' using errcode = 'restrict_violation';
    end if;
    if (to_jsonb(new) - 'status' - 'evidence_result_id') <> (to_jsonb(old) - 'status' - 'evidence_result_id') then
        raise exception 'only the status and the evidence of a step can change' using errcode = 'restrict_violation';
    end if;
    if exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status in ('completed', 'rejected')) then
        raise exception 'the care plan is finished' using errcode = 'restrict_violation';
    end if;
    if new.status = 'closed' and old.kind = 'wait_result' then
        raise exception 'a step that waits for a result closes only as met or not_met, on a result' using errcode = 'restrict_violation';
    end if;
    if old.status = 'needs_doctor' and new.status <> old.status and not iam.has_clinic_role(new.clinic_id, array['doctor']) then
        raise exception 'only a doctor releases a step held for a doctor' using errcode = 'insufficient_privilege';
    end if;
    if new.status in ('met', 'not_met') then
        if new.kind <> 'wait_result' or not exists (
            select 1 from clinical.results r join ops.care_plans p on p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status = 'active'
             where r.id = new.evidence_result_id and r.clinic_id = new.clinic_id and r.patient_id = p.patient_id and r.verified
               and left(lower(r.code), length(new.result_prefix)) = lower(new.result_prefix)
               and not exists (select 1 from clinical.results s where s.supersedes_id = r.id)
        ) then
            raise exception 'a step can close only on a verified result of this patient that matches the step, in an active plan' using errcode = 'restrict_violation';
        end if;
    end if;
    return new;
end
$$;

create or replace function ops.care_plan_waits_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plan_waits rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if (to_jsonb(new) - 'open' - 'claimed_result_id') <> (to_jsonb(old) - 'open' - 'claimed_result_id') then
        raise exception 'only the claim of a wait can change' using errcode = 'restrict_violation';
    end if;
    if old.claimed_result_id is not null and new.claimed_result_id is distinct from old.claimed_result_id then
        raise exception 'the first result to claim a wait keeps it' using errcode = 'restrict_violation';
    end if;
    if old.open is false and new.open is true then
        raise exception 'a closed wait stays closed' using errcode = 'restrict_violation';
    end if;
    if exists (select 1 from ops.care_plans p where p.id = new.plan_id and p.clinic_id = new.clinic_id and p.status in ('completed', 'rejected')) then
        raise exception 'the care plan is finished' using errcode = 'restrict_violation';
    end if;
    if new.claimed_result_id is not null and old.claimed_result_id is null then
        if old.open is not true or not exists (
            select 1 from clinical.results r where r.id = new.claimed_result_id and r.clinic_id = new.clinic_id and r.patient_id = new.patient_id
               and left(lower(r.code), length(new.prefix)) = lower(new.prefix)
        ) then
            raise exception 'an open wait can be claimed only by a result of this patient that matches its prefix' using errcode = 'restrict_violation';
        end if;
    end if;
    return new;
end
$$;

create or replace function ops.care_plan_children_insert_guard() returns trigger
    language plpgsql set search_path = ''
as $$
declare
    v_status text;
    v_step   record;
begin
    select status into v_status from ops.care_plans where id = new.plan_id and clinic_id = new.clinic_id;
    if tg_table_name = 'care_plan_steps' and v_status is distinct from 'screening' then
        raise exception 'steps can be added only while the plan is being proposed' using errcode = 'restrict_violation';
    end if;
    if tg_table_name = 'care_plan_waits' then
        if v_status is distinct from 'active' then
            raise exception 'waits can be added only to an active plan' using errcode = 'restrict_violation';
        end if;
        select kind, result_prefix into v_step from ops.care_plan_steps where plan_id = new.plan_id and step_key = new.step_key and clinic_id = new.clinic_id;
        if not found or v_step.kind <> 'wait_result' or v_step.result_prefix is distinct from new.prefix then
            raise exception 'a wait must belong to a result step and carry its prefix' using errcode = 'restrict_violation';
        end if;
    end if;
    return new;
end
$$;

-- B
create or replace function ops.care_review_samples_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_review_samples rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.rechecked_at is not null then
        raise exception 'a re-checked sample is final' using errcode = 'restrict_violation';
    end if;
    if new.id <> old.id or new.clinic_id <> old.clinic_id or new.origin <> old.origin or new.decision_id <> old.decision_id or new.sampled_at <> old.sampled_at
       or new.idempotency_key <> old.idempotency_key or new.rechecked_by is null then
        raise exception 'a sample can only be re-checked' using errcode = 'restrict_violation';
    end if;
    if exists (select 1 from agents.review_decisions d where d.id = old.decision_id and d.clinic_id = old.clinic_id and d.decided_by = new.rechecked_by) then
        raise exception 'a sample must be re-checked by someone other than the person who made the decision' using errcode = 'restrict_violation';
    end if;
    new.rechecked_at := clock_timestamp();
    return new;
end
$$;

-- C: reads need a verified second factor; the queue worker is exempt and gets narrow permissive policies
create policy results_aal2_select on clinical.results as restrictive for select to careflow_app using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy results_select_worker on clinical.results for select to careflow_app using (iam.has_clinic_role(clinic_id, array['queue_worker']));

drop policy care_plans_aal2_update on ops.care_plans;
create policy care_plans_aal2_update on ops.care_plans as restrictive for update to careflow_app
    using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker'])) with check (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plans_aal2_select on ops.care_plans as restrictive for select to careflow_app using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plans_select_worker on ops.care_plans for select to careflow_app using (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plans_update_worker on ops.care_plans for update to careflow_app
    using (status = 'active' and iam.has_clinic_role(clinic_id, array['queue_worker']))
    with check (status in ('active', 'held', 'completed') and iam.has_clinic_role(clinic_id, array['queue_worker']));  -- the worker moves an ACTIVE plan on, nothing else

drop policy care_plan_steps_aal2_update on ops.care_plan_steps;
create policy care_plan_steps_aal2_update on ops.care_plan_steps as restrictive for update to careflow_app
    using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker'])) with check (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plan_steps_aal2_select on ops.care_plan_steps as restrictive for select to careflow_app using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plan_steps_select_worker on ops.care_plan_steps for select to careflow_app using (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plan_steps_update_worker on ops.care_plan_steps for update to careflow_app
    using (status = 'open' and kind = 'wait_result' and iam.has_clinic_role(clinic_id, array['queue_worker']))
    with check (status in ('met', 'not_met', 'needs_doctor') and iam.has_clinic_role(clinic_id, array['queue_worker']));  -- an open result step may be closed on evidence or held for a doctor

drop policy care_plan_waits_aal2_update on ops.care_plan_waits;
create policy care_plan_waits_aal2_update on ops.care_plan_waits as restrictive for update to careflow_app
    using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker'])) with check (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plan_waits_aal2_select on ops.care_plan_waits as restrictive for select to careflow_app using (iam.aal2_session() or iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plan_waits_select_worker on ops.care_plan_waits for select to careflow_app using (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy care_plan_waits_update_worker on ops.care_plan_waits for update to careflow_app
    using (open and iam.has_clinic_role(clinic_id, array['queue_worker']))
    with check (claimed_result_id is not null and iam.has_clinic_role(clinic_id, array['queue_worker']));  -- the worker claims an open wait (and closes it once its step closes); it never closes one without a claim

-- D
alter table mpi.claim_requests
    add column resolved_by uuid references iam.accounts (id),
    add column resolved_at timestamptz,
    add column resolved_patient_id uuid,
    add constraint claim_requests_resolution check ((status = 'pending') = (resolved_by is null and resolved_at is null)),
    add constraint claim_requests_resolved_patient foreign key (resolved_patient_id, clinic_id) references mpi.patients (id, clinic_id);

create function mpi.resolve_claim_request(p_request_id uuid, p_decision text, p_patient_id uuid, p_idempotency_key text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_req   record;
    v_n     integer;
begin
    if v_actor is null or p_request_id is null or p_idempotency_key is null or length(p_idempotency_key) not between 1 and 200 or p_decision is null or p_decision not in ('approve', 'reject')
       or (p_decision = 'approve' and p_patient_id is null) or not iam.aal2_session() then
        raise exception 'claim resolution refused' using errcode = 'insufficient_privilege';
    end if;
    select id, clinic_id, origin, account_id, status into v_req from mpi.claim_requests where id = p_request_id for update;
    if not found or not iam.has_clinic_role(v_req.clinic_id, array['front_desk']) then
        raise exception 'claim resolution refused' using errcode = 'insufficient_privilege';
    end if;
    if v_req.status <> 'pending' then
        -- a retry of the SAME decision gets the stored outcome back; a different decision on a finished request is `already_resolved`
        return case when v_req.status = 'resolved' and p_decision = 'approve' then 'approved' when v_req.status = 'rejected' and p_decision = 'reject' then 'rejected' else 'already_resolved' end;
    end if;
    if p_decision = 'reject' then
        update mpi.claim_requests set status = 'rejected', resolved_by = v_actor, resolved_at = clock_timestamp() where id = p_request_id;
        insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
        values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'portal.claim_reject', 'claim_request', p_request_id::text, '{}'::jsonb, 'sys:portal-claim-resolve:' || p_request_id::text);
        return 'rejected';
    end if;
    if exists (select 1 from mpi.patients where account_id = v_req.account_id) then
        return 'claimant_already_bound';
    end if;
    -- consent is checked at approval too: a claimant who withdrew (or who has not accepted a newer notice) since the request is not bound
    if (select e.action from iam.consent_events e join iam.consent_notices n on n.id = e.notice_id
         where e.account_id = v_req.account_id and e.purpose = 'care_delivery' and n.version = (select max(version) from iam.consent_notices where purpose = 'care_delivery')
         order by e.occurred_at desc, e.id desc limit 1) is distinct from 'granted' then
        return 'consent_required';
    end if;
    -- a CONFIRMED (newest identity event is a confirmation), live, unbound record of this clinic and origin; an unconfirmed record stays unclaimable until the confirm-identity route confirms it
    if not exists (
        select 1 from mpi.patients p
         where p.id = p_patient_id and p.clinic_id = v_req.clinic_id and p.origin = v_req.origin and p.account_id is null and p.erased_at is null
           and exists (select 1 from mpi.identity_confirmations c where c.patient_id = p.id and c.clinic_id = p.clinic_id and c.kind = 'confirm'
                        and c.seq = (select max(x.seq) from mpi.identity_confirmations x where x.patient_id = p.id))
         for share of p
    ) then
        return 'record_not_claimable';
    end if;
    begin
        update mpi.patients set account_id = v_req.account_id where id = p_patient_id and account_id is null;
        get diagnostics v_n = row_count;
    exception when check_violation then
        return 'record_not_claimable';  -- the account-binding guard (0012) refused: the claimant is not an active patient account of this origin
    end;
    if v_n <> 1 then
        return 'record_not_claimable';
    end if;
    update mpi.claim_requests set status = 'resolved', resolved_by = v_actor, resolved_at = clock_timestamp(), resolved_patient_id = p_patient_id where id = p_request_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'portal.claim_approve', 'claim_request', p_request_id::text, jsonb_build_object('patient_id', p_patient_id::text), 'sys:portal-claim-resolve:' || p_request_id::text);
    return 'approved';
end
$$;

-- E
create function iam.publish_consent_notice(p_purpose text, p_version integer, p_body text) returns text
    language plpgsql security definer set search_path = ''
as $$
declare
    v_max  integer;
    v_same text;
begin
    if auth.uid() is null or not iam.aal2_session() or not iam.has_any_role(array['admin']) or p_purpose is null or p_purpose not in ('care_delivery', 'ai_processing', 'service_improvement')
       or p_version is null or p_version < 1 or p_body is null or length(p_body) not between 1 and 20000 then
        raise exception 'consent notice publication refused' using errcode = 'insufficient_privilege';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('consent-notice:' || p_purpose, 0));  -- two publishers of the same version are serialised: the loser reads `exists` / `conflict`, not a raw error
    select body into v_same from iam.consent_notices where purpose = p_purpose and version = p_version;
    if found then
        return case when v_same = p_body then 'exists' else 'conflict' end;
    end if;
    select coalesce(max(version), 0) into v_max from iam.consent_notices where purpose = p_purpose;
    if p_version <> v_max + 1 then
        return 'version_gap';
    end if;
    insert into iam.consent_notices (purpose, version, body) values (p_purpose, p_version, p_body);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values ('live', null, auth.uid(), 'admin', 'consent.notice_publish', 'consent_notice', p_purpose || ':' || p_version::text, jsonb_build_object('purpose', p_purpose, 'version', p_version), 'sys:consent-notice:' || p_purpose || ':' || p_version::text);
    return 'published';
end
$$;

do $$
declare
    f text;
    r text;
begin
    foreach f in array array['mpi.resolve_claim_request(uuid,text,uuid,text)', 'iam.publish_consent_notice(text,integer,text)'] loop
        execute format('revoke all on function %s from public', f);
        execute format('grant execute on function %s to careflow_app', f);
    end loop;
    foreach f in array array['mpi.resolve_claim_request(uuid,text,uuid,text)', 'iam.publish_consent_notice(text,integer,text)', 'mpi.claim_patient_record(uuid,bytea,bytea,smallint)', 'ops.clinic_origin(uuid)',
                              'ops.care_plans_guard()', 'ops.care_plan_steps_guard()', 'ops.care_plan_waits_guard()', 'ops.care_plan_children_insert_guard()', 'ops.care_review_samples_guard()',
                              'clinical.results_guard()', 'ops.care_barriers_not_erased()'] loop
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
                if has_function_privilege(r, f, 'execute') then
                    raise exception '0051: % must not be executable by %', f, r;
                end if;
            end if;
        end loop;
        if has_function_privilege('public', f, 'execute') then
            raise exception '0051: % must not be executable by PUBLIC', f;
        end if;
    end loop;
    foreach f in array array['ops.care_plans_guard()', 'ops.care_plan_steps_guard()', 'ops.care_plan_waits_guard()', 'ops.care_plan_children_insert_guard()', 'clinical.results_guard()', 'ops.care_barriers_not_erased()', 'ops.care_review_samples_guard()'] loop
        if has_function_privilege('careflow_app', f, 'execute') then
            raise exception '0051: the trigger function % must not be executable by the application role', f;
        end if;
    end loop;
end $$;
