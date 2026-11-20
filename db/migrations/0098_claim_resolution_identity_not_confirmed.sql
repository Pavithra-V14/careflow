-- 0098_claim_resolution_identity_not_confirmed: a fix forward for a misleading answer (found reading the portal claim path for the demo e-mail sign-in, 2026-11-20).
--
-- Approving a portal claim needs the chosen record to be CONFIRMED (the newest identity event is a confirmation by front desk). When it was not, `mpi.resolve_claim_request` answered `record_not_claimable`, the same
-- word as for an erased, merged-away, already-bound or other-clinic record, so the front desk could not tell that the cure is simply to confirm the identity first. This re-issues the function with ONE change:
-- a record that is live, of this clinic and origin and unbound, but not confirmed, now answers `identity_not_confirmed`. Every other branch, check, lock, audit row and grant is exactly as in 0051 (the signature is
-- unchanged, so the grants stay). The claim itself (`mpi.claim_patient_record`) is untouched: an unconfirmed record still cannot be claimed automatically, and a claim against it still goes to review.

create or replace function mpi.resolve_claim_request(p_request_id uuid, p_decision text, p_patient_id uuid, p_idempotency_key text) returns text
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
    -- the record must be live, of this clinic and origin, and unbound; anything else is `record_not_claimable`
    if not exists (
        select 1 from mpi.patients p
         where p.id = p_patient_id and p.clinic_id = v_req.clinic_id and p.origin = v_req.origin and p.account_id is null and p.erased_at is null
         for share of p
    ) then
        return 'record_not_claimable';
    end if;
    -- and it must be CONFIRMED (the newest identity event is a confirmation). A record that is otherwise claimable but not confirmed gets its own word, so the front desk is told what to do (confirm the
    -- identity first, with the confirm-identity route) instead of a vague "not claimable". Nothing is disclosed that the front desk cannot already read.
    if not exists (
        select 1 from mpi.identity_confirmations c
         where c.patient_id = p_patient_id and c.clinic_id = v_req.clinic_id and c.kind = 'confirm'
           and c.seq = (select max(x.seq) from mpi.identity_confirmations x where x.patient_id = p_patient_id)
    ) then
        return 'identity_not_confirmed';
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
