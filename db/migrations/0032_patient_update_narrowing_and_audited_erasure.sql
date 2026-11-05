-- 0032_patient_update_narrowing_and_audited_erasure: review finding M1 (docs/design/review-2026-11-04.md; docs/design/patient-erasure-and-update-narrowing.md; pre-approved by the owner 2026-11-05).
-- Reviewer pass 2026-11-05 applied (planted-audit-key suppression, two-person rule of BUILD-SPEC 12.5, anon/authenticated/service_role revokes).
--
-- THE HOLE. Front desk's database permission let one statement change a patient's encrypted name, date of birth and phone, their key-version numbers, and the erased flag (0012, a column list), change any
-- column of an identity token (0002, table-wide UPDATE), and rewrite a match candidate (0003, table-wide UPDATE), with no audit row. No route does any of this; the permission was just too wide.
--
-- THE FIX.
--   1. mpi.patients: the application role loses UPDATE on every column it had (the three ciphertexts, the three key versions, erased_at). Nothing in the application updates them; key rotation runs as the
--      migrator (docs/runbooks/key_rotation.md). A row LOCK (SELECT ... FOR UPDATE, which identity.py uses) needs UPDATE on at least one column, so one new harmless column, `lock_version`, takes the only
--      remaining UPDATE grant. (The harm of changing it is nil: it is a counter nothing reads.)
--   2. mpi.identity_tokens: UPDATE is removed altogether (tokens are written once; rotation runs as the migrator).
--   3. mpi.match_candidates: UPDATE narrows to status, resolved_at, resolved_by (all the engine ever sets; 0029 already makes the pair immutable).
--   4. ERASURE is a deliberate, audited, TWO-PERSON act (BUILD-SPEC 12.5: "processing an erasure request"): a new ledger mpi.erasure_requests holds a request made by one front-desk person and the approval
--      by a DIFFERENT one. The application role cannot write the ledger directly: three SECURITY DEFINER functions (pinned search_path) do it, each checking the caller inside through the active-role aware
--      helper, each writing its audit row in the same transaction with a SERVER-MADE key (prefix `sys:`, which nobody else may use, see 0035, so a planted key cannot suppress it):
--        mpi.request_patient_erasure(patient, reason, idempotency key)   front desk of the clinic; the patient exists, is not erased, has no OPEN request.
--        mpi.approve_patient_erasure(request id, idempotency key)         a DIFFERENT front desk of the clinic; refuses a patient in a live merge link; then, in the same transaction, clears the three
--                                                                         ciphertexts and key versions and sets erased_at in ONE statement.
--        mpi.cancel_patient_erasure(request id)                          any front desk of the clinic (the requester or anyone else); only while the request is open.
--      Every refusal that could reveal whether a patient or a request exists raises the same generic error. Messages are NOT touched (their erasure has its own design) and identity tokens are kept (they
--      are keyed hashes; matching ignores an erased record): both are named residuals.
--
-- NEW GRANTS (grants ledger): EXECUTE on the three functions to careflow_app; SELECT on the new table for front desk through a policy. Everything else here only removes or narrows permissions.

alter table mpi.patients add column lock_version integer not null default 0;

revoke update (name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, erased_at) on mpi.patients from careflow_app;
grant update (lock_version) on mpi.patients to careflow_app;

revoke update on mpi.identity_tokens from careflow_app;

revoke update on mpi.match_candidates from careflow_app;
grant update (status, resolved_at, resolved_by) on mpi.match_candidates to careflow_app;

-- ---- the erasure ledger -------------------------------------------------------------------------------------------------------------------

create table mpi.erasure_requests (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    patient_id      uuid not null,
    reason          text not null check (reason ~ '^[a-z][a-z0-9_]{0,39}$'),
    status          text not null default 'requested' check (status in ('requested', 'erased', 'cancelled')),
    requested_by    uuid not null references iam.accounts (id),
    approved_by     uuid references iam.accounts (id),
    created_at      timestamptz not null default clock_timestamp(),
    decided_at      timestamptz,
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    constraint erasure_two_distinct_people check (approved_by is null or approved_by <> requested_by),
    constraint erasure_shape check (
        (status = 'requested' and approved_by is null and decided_at is null)
        or (status = 'erased' and approved_by is not null and decided_at is not null)
        or (status = 'cancelled' and approved_by is null and decided_at is not null)
    )
);
create unique index erasure_one_open_request_per_patient on mpi.erasure_requests (patient_id) where status = 'requested';

alter table mpi.erasure_requests enable row level security;
alter table mpi.erasure_requests force row level security;
create policy erasure_requests_select on mpi.erasure_requests for select to careflow_app using (iam.has_clinic_role(clinic_id, array['front_desk']));
revoke all on mpi.erasure_requests from public;
grant select on mpi.erasure_requests to careflow_app;

create function mpi.erasure_requests_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'mpi.erasure_requests rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.status <> 'requested' or new.status not in ('erased', 'cancelled')
       or (new.clinic_id, new.patient_id, new.reason, new.requested_by, new.created_at, new.origin, new.idempotency_key)
          is distinct from (old.clinic_id, old.patient_id, old.reason, old.requested_by, old.created_at, old.origin, old.idempotency_key) then
        raise exception 'an erasure request moves from requested to erased or cancelled, once' using errcode = 'check_violation';
    end if;
    return new;
end
$$;
revoke all on function mpi.erasure_requests_guard() from public;
create trigger erasure_requests_guard before update or delete on mpi.erasure_requests for each row execute function mpi.erasure_requests_guard();
create trigger erasure_requests_no_truncate before truncate on mpi.erasure_requests for each statement execute function mpi.erasure_requests_guard();
alter table mpi.erasure_requests enable always trigger erasure_requests_guard;
alter table mpi.erasure_requests enable always trigger erasure_requests_no_truncate;

-- ---- the three functions ------------------------------------------------------------------------------------------------------------------

create function mpi.request_patient_erasure(p_patient_id uuid, p_reason text, p_idempotency_key text) returns uuid
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_row record;
    v_id uuid;
begin
    if v_actor is null or p_patient_id is null or p_reason is null or p_reason !~ '^[a-z][a-z0-9_]{0,39}$'
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    select clinic_id, origin, erased_at into v_row from mpi.patients where id = p_patient_id for update;
    if not found or v_row.erased_at is not null or not iam.has_clinic_role(v_row.clinic_id, array['front_desk']) then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    -- a replay (the same caller, the same key) answers the original request
    select id into v_id from mpi.erasure_requests where idempotency_key = 'req:' || p_patient_id::text || ':' || v_actor::text || ':' || p_idempotency_key;
    if v_id is not null then
        return v_id;
    end if;
    insert into mpi.erasure_requests (clinic_id, patient_id, reason, requested_by, origin, idempotency_key)
    values (v_row.clinic_id, p_patient_id, p_reason, v_actor, v_row.origin, 'req:' || p_patient_id::text || ':' || v_actor::text || ':' || p_idempotency_key)
    returning id into v_id;  -- (an already open request for the patient raises a unique violation, mapped by the route)
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_row.origin, v_row.clinic_id, v_actor, 'front_desk', 'patient.erase_request', 'patient', p_patient_id::text,
            jsonb_build_object('reason', p_reason, 'role_scope', iam.role_scope()), 'sys:erase-request:' || v_id::text);
    return v_id;
end
$$;

create function mpi.approve_patient_erasure(p_request_id uuid, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_req record;
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
    if exists (select 1 from mpi.merges m
               where m.status in ('awaiting_second_approval', 'active') and v_req.patient_id in (m.master_patient_id, m.subordinate_patient_id)) then
        raise exception 'a patient in a live merge link cannot be erased' using errcode = 'check_violation';
    end if;
    update mpi.patients
       set name_ct = null, name_key_version = null, dob_ct = null, dob_key_version = null, phone_ct = null, phone_key_version = null, erased_at = clock_timestamp()
     where id = v_req.patient_id and erased_at is null;
    update mpi.erasure_requests set status = 'erased', approved_by = v_actor, decided_at = clock_timestamp() where id = p_request_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'patient.erase', 'patient', v_req.patient_id::text,
            jsonb_build_object('reason', v_req.reason, 'role_scope', iam.role_scope()), 'sys:erase:' || p_request_id::text);
    return true;
end
$$;

create function mpi.cancel_patient_erasure(p_request_id uuid) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_req record;
begin
    if v_actor is null or p_request_id is null then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    select * into v_req from mpi.erasure_requests where id = p_request_id for update;
    if not found or not iam.has_clinic_role(v_req.clinic_id, array['front_desk']) then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    if v_req.status = 'cancelled' then
        return false;
    end if;
    if v_req.status <> 'requested' then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    update mpi.erasure_requests set status = 'cancelled', decided_at = clock_timestamp() where id = p_request_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_req.origin, v_req.clinic_id, v_actor, 'front_desk', 'patient.erase_cancel', 'patient', v_req.patient_id::text,
            jsonb_build_object('role_scope', iam.role_scope()), 'sys:erase-cancel:' || p_request_id::text);
    return true;
end
$$;

do $$
declare
    f text;
    r text;
begin
    foreach f in array array['mpi.request_patient_erasure(uuid, text, text)', 'mpi.approve_patient_erasure(uuid, text)', 'mpi.cancel_patient_erasure(uuid)'] loop
        execute format('revoke all on function %s from public', f);
        execute format('grant execute on function %s to careflow_app', f);
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) then
                execute format('revoke all on function %s from %I', f, r);
            end if;
        end loop;
    end loop;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on mpi.erasure_requests from %I', r);
        end if;
    end loop;
end $$;

-- Verification.
do $$
declare
    c text;
    f text;
    r text;
begin
    foreach c in array array['name_ct', 'name_key_version', 'dob_ct', 'dob_key_version', 'phone_ct', 'phone_key_version', 'erased_at', 'account_id', 'clinic_id', 'origin', 'idempotency_key'] loop
        if has_column_privilege('careflow_app', 'mpi.patients', c, 'update') then
            raise exception '0032: the application role must not UPDATE mpi.patients.%', c;
        end if;
    end loop;
    if not has_column_privilege('careflow_app', 'mpi.patients', 'lock_version', 'update') then
        raise exception '0032: the row-lock column must stay updatable';
    end if;
    if has_table_privilege('careflow_app', 'mpi.identity_tokens', 'update') or has_column_privilege('careflow_app', 'mpi.identity_tokens', 'token', 'update') then
        raise exception '0032: identity tokens must not be updatable by the application role';
    end if;
    foreach c in array array['patient_id', 'candidate_patient_id', 'matched_kinds', 'reason', 'clinic_id', 'origin'] loop
        if has_column_privilege('careflow_app', 'mpi.match_candidates', c, 'update') then
            raise exception '0032: the application role must not UPDATE mpi.match_candidates.%', c;
        end if;
    end loop;
    if not has_column_privilege('careflow_app', 'mpi.match_candidates', 'status', 'update') then
        raise exception '0032: a candidate must stay resolvable';
    end if;
    if has_table_privilege('careflow_app', 'mpi.erasure_requests', 'insert') or has_table_privilege('careflow_app', 'mpi.erasure_requests', 'update')
       or has_table_privilege('careflow_app', 'mpi.erasure_requests', 'delete') or not has_table_privilege('careflow_app', 'mpi.erasure_requests', 'select') then
        raise exception '0032: the application role may only SELECT the erasure ledger';
    end if;
    foreach f in array array['mpi.request_patient_erasure(uuid, text, text)', 'mpi.approve_patient_erasure(uuid, text)', 'mpi.cancel_patient_erasure(uuid)'] loop
        if not has_function_privilege('careflow_app', f, 'execute') or has_function_privilege('public', f, 'execute') then
            raise exception '0032: % must be executable by the application role only', f;
        end if;
        foreach r in array array['anon', 'authenticated', 'service_role'] loop
            if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
                raise exception '0032: % must not be executable by %', f, r;
            end if;
        end loop;
    end loop;
end $$;
