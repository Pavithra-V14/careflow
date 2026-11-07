-- 0056_erase_and_rotate_care_plan_text_and_results (owner decision of 2026-11-08; BUILD-SPEC.md 9.4, 17.2, 20.1; CLAUDE.md rules 5, 6, 8, 10). Fix forward: no merged migration is edited.
--
-- WHAT THIS ADDS. 0050 stored a doctor's care-plan text ENCRYPTED (ops.care_plans.plan_ct, bound to the plan id) and clinical results as plain numbers (clinical.results.measures), both append-only, with no way to erase
-- them when a patient is erased or to re-encrypt the plan text under a new key. This migration closes both gaps without opening the ledgers to the application role:
--
-- (1) ERASURE, through the EXISTING audited two-person path. mpi.approve_patient_erasure (0032; a different front-desk person approves) now also, in the SAME transaction: sets the patient's care-plan texts to
--     NULL (with their key versions) and marks them erased; replaces the labels of their plan steps with the word `erased`; and replaces their results' measures with an empty object and marks them erased.
--     The plan text, key versions, step labels and measures are not writable by the application role (it has no update grant on them; unchanged). The guards of the three tables get EXACTLY ONE new
--     transition each (below); every other change, every delete and every truncate is still refused for everyone including the owner. An erased row is final. A new audit row `care_record.erased` (key
--     `sys:erase-care-record:<request>`) records the COUNTS. The erasure audit rows of 0032 are unchanged (their tests pin their exact shape).
-- (2) KEY ROTATION of the plan text: the guard of ops.care_plans gets the second transition, a change of plan_ct and plan_key_version only (the key version MUST change, erased_at stays null), reachable only
--     by the owner connection that the maintenance script runs on (careflow/rotation.py rotate_plan_texts, scripts/rotate_keys.py), recorded in the new ledger ops.plan_rotation_batches (owner only, no
--     application grant). Clinical results hold only NUMBERS and have no key, so there is nothing to rotate for them; their erasure is (1).
--
-- WHAT IS NOT COVERED (named): messages are still not erased by this path (0032 residual; the retention and purge design waits for the privacy owner); identity tokens are kept (keyed hashes); the result CODE,
-- verified flag, times and the step keys are kept (they name a kind of test, not a value); audit rows are never erased.

-- ---- care plans -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
alter table ops.care_plans add column erased_at timestamptz;
alter table ops.care_plans alter column plan_ct drop not null;
alter table ops.care_plans alter column plan_key_version drop not null;
alter table ops.care_plans add constraint care_plans_erased_shape check ((plan_ct is null) = (plan_key_version is null) and (plan_ct is null) = (erased_at is not null));

create or replace function ops.care_plans_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plans rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.erased_at is not null then
        raise exception 'an erased care plan is final' using errcode = 'restrict_violation';
    end if;
    -- the two transitions that touch the text, each changing NOTHING else (a column added later is immutable by default): erasure, and key rotation
    if (to_jsonb(new) - 'plan_ct' - 'plan_key_version' - 'erased_at' - 'updated_at') = (to_jsonb(old) - 'plan_ct' - 'plan_key_version' - 'erased_at' - 'updated_at') then
        if new.erased_at is not null and new.plan_ct is null and new.plan_key_version is null then
            new.updated_at := clock_timestamp();
            return new;  -- erasure
        end if;
        if new.erased_at is null and new.plan_ct is not null and new.plan_key_version is distinct from old.plan_key_version and new.plan_ct is distinct from old.plan_ct then
            new.updated_at := clock_timestamp();
            return new;  -- key rotation
        end if;
    end if;
    if old.status in ('completed', 'rejected') then
        raise exception 'a finished care plan is final' using errcode = 'restrict_violation';
    end if;
    if new.id <> old.id or new.clinic_id <> old.clinic_id or new.origin <> old.origin or new.patient_id <> old.patient_id or new.plan_ct is distinct from old.plan_ct
       or new.plan_key_version is distinct from old.plan_key_version or new.erased_at is distinct from old.erased_at or new.created_by <> old.created_by or new.created_at <> old.created_at
       or new.idempotency_key <> old.idempotency_key then
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

-- the label of a plan step is clinician text derived from the plan: erasure may replace it with the single word `erased`, and nothing else about the step changes in that statement
create or replace function ops.care_plan_steps_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_plan_steps rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if new.label = 'erased' and old.label <> 'erased' and (to_jsonb(new) - 'label') = (to_jsonb(old) - 'label') then
        return new;  -- erasure of the label
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

-- ---- results --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
alter table clinical.results add column erased_at timestamptz;
alter table clinical.results add constraint results_erased_shape check (erased_at is null or measures = '{}'::jsonb);

create or replace function clinical.results_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    -- the ONE change an update may make: erasure replaces the measures with an empty object and marks the row; every other column is compared from the row itself
    if tg_op = 'UPDATE' then
        if old.erased_at is null and new.erased_at is not null and new.measures = '{}'::jsonb
           and (to_jsonb(new) - 'measures' - 'erased_at') = (to_jsonb(old) - 'measures' - 'erased_at') then
            return new;
        end if;
    end if;
    raise exception 'clinical.results is append-only: % is not permitted (a correction is a new row that supersedes the old one)', tg_op using errcode = 'restrict_violation';
end
$$;

-- ---- the rotation ledger (owner only) -----------------------------------------------------------------------------------------------------------------------------------------------------------------------
create table ops.plan_rotation_batches (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    from_version    smallint not null check (from_version > 0),
    to_version      smallint not null check (to_version > 0 and to_version <> from_version),
    plans_rotated   integer not null check (plans_rotated > 0),
    first_plan_id   uuid not null,
    last_plan_id    uuid not null,
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null unique check (length(idempotency_key) > 0)
);
alter table ops.plan_rotation_batches enable row level security;
alter table ops.plan_rotation_batches force row level security;
revoke all on ops.plan_rotation_batches from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.plan_rotation_batches from %I', r);
        end if;
    end loop;
end $$;

-- ---- the existing audited erasure approval, extended in the SAME transaction ---------------------------------------------------------------------------------------------------------------------------------
create or replace function mpi.approve_patient_erasure(p_request_id uuid, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor   uuid := auth.uid();
    v_req     record;
    v_plans   integer;
    v_results integer;
begin
    if v_actor is null or p_request_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    select * into v_req from mpi.erasure_requests where id = p_request_id for update;
    if not found or not iam.has_clinic_role(v_req.clinic_id, array['front_desk']) then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    if v_req.status = 'erased' then
        return false;  -- a replay: already carried out
    end if;
    -- two DIFFERENT people (BUILD-SPEC 12.5): the requester cannot approve their own request
    if v_req.status <> 'requested' or v_req.requested_by = v_actor then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    perform 1 from mpi.patients where id = v_req.patient_id for update;  -- the same row lock the merge engine takes on both patients, so a merge cannot be proposed between the check and the erase
    if exists (select 1 from mpi.merges m
               where m.status in ('awaiting_second_approval', 'active') and v_req.patient_id in (m.master_patient_id, m.subordinate_patient_id)) then
        raise exception 'a patient in a live merge link cannot be erased' using errcode = 'check_violation';
    end if;
    update mpi.patients
       set name_ct = null, name_key_version = null, dob_ct = null, dob_key_version = null, phone_ct = null, phone_key_version = null, erased_at = clock_timestamp()
     where id = v_req.patient_id and erased_at is null;
    -- the care record of the same patient (0056): plan text and step labels, then results' measures. Counts, not content, go to the audit row.
    update ops.care_plan_steps s set label = 'erased'
      from ops.care_plans p where s.plan_id = p.id and s.clinic_id = p.clinic_id and p.patient_id = v_req.patient_id and p.clinic_id = v_req.clinic_id and s.label <> 'erased';
    update ops.care_plans set plan_ct = null, plan_key_version = null, erased_at = clock_timestamp()
     where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id and erased_at is null;
    get diagnostics v_plans = row_count;
    update clinical.results set measures = '{}'::jsonb, erased_at = clock_timestamp()
     where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id and erased_at is null;
    get diagnostics v_results = row_count;
    update mpi.erasure_requests set status = 'erased', approved_by = v_actor, decided_at = clock_timestamp() where id = p_request_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'patient.erase', 'patient', v_req.patient_id::text,
            jsonb_build_object('reason', v_req.reason, 'role_scope', iam.role_scope()), 'sys:erase:' || p_request_id::text);
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'care_record.erased', 'patient', v_req.patient_id::text,
            jsonb_build_object('care_plans', v_plans, 'results', v_results), 'sys:erase-care-record:' || p_request_id::text);
    return true;
end
$$;

do $$
declare r text;
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0056: the migrating role must bypass row-level security (the erasure function and the rotation script rely on it)';
    end if;
    if has_column_privilege('careflow_app', 'ops.care_plans', 'plan_ct', 'update') or has_column_privilege('careflow_app', 'ops.care_plans', 'erased_at', 'update')
       or has_column_privilege('careflow_app', 'ops.care_plans', 'plan_key_version', 'update') or has_column_privilege('careflow_app', 'clinical.results', 'measures', 'update')
       or has_column_privilege('careflow_app', 'clinical.results', 'erased_at', 'update') or has_column_privilege('careflow_app', 'ops.care_plan_steps', 'label', 'update') then
        raise exception '0056: the application role must not be able to update erased_at, the plan text, the labels or the measures';
    end if;
    if has_table_privilege('careflow_app', 'ops.plan_rotation_batches', 'select') then
        raise exception '0056: the rotation ledger is owner-only';
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, 'mpi.approve_patient_erasure(uuid,text)', 'execute') then
            raise exception '0056: the erasure approval must not be executable by %', r;
        end if;
    end loop;
    if has_function_privilege('public', 'mpi.approve_patient_erasure(uuid,text)', 'execute') then
        raise exception '0056: the erasure approval must not be executable by PUBLIC';
    end if;
    if not has_function_privilege('careflow_app', 'mpi.approve_patient_erasure(uuid,text)', 'execute') then
        raise exception '0056: the application login must still be able to execute the erasure approval';
    end if;
end $$;

-- ---- patients erased BEFORE this migration (by the 0032 function, which left the care record alone) get the same erasure now. The guards above allow exactly these transitions; the migrator bypasses row-level security.
update ops.care_plan_steps s set label = 'erased'
    from ops.care_plans c
    where s.plan_id = c.id and s.clinic_id = c.clinic_id and s.label <> 'erased'
      and c.patient_id in (select id from mpi.patients where erased_at is not null);
update ops.care_plans set plan_ct = null, plan_key_version = null, erased_at = now()
    where erased_at is null and patient_id in (select id from mpi.patients where erased_at is not null);
update clinical.results set measures = '{}'::jsonb, erased_at = now()
    where erased_at is null and patient_id in (select id from mpi.patients where erased_at is not null);
