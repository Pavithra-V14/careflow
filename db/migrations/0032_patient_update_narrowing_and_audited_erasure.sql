-- 0032_patient_update_narrowing_and_audited_erasure: review finding M1 (docs/design/review-2026-11-04.md; docs/design/patient-erasure-and-update-narrowing.md; pre-approved by the owner 2026-11-05).
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
--   4. ERASURE becomes a deliberate, audited act: mpi.erase_patient(patient, reason, idempotency key), SECURITY DEFINER with a pinned search_path, executable by the application role. It checks the caller is
--      front desk of the patient's clinic NOW (the active-role aware helper), refuses a patient in a live merge link, clears the three ciphertexts and key versions and sets erased_at in ONE statement, and writes
--      the audit.log row (action patient.erase, reason code, the role scope) in the same transaction. Every refusal that could reveal whether a patient exists raises the same generic error. Messages are
--      NOT touched (their erasure has its own design), and identity tokens are kept (they are keyed hashes and matching ignores an erased record); both are named residuals.
--
-- NEW GRANT (grants ledger): EXECUTE on mpi.erase_patient(uuid, text, text) to careflow_app. Everything else here only removes or narrows permissions.

alter table mpi.patients add column lock_version integer not null default 0;

revoke update (name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, erased_at) on mpi.patients from careflow_app;
grant update (lock_version) on mpi.patients to careflow_app;

revoke update on mpi.identity_tokens from careflow_app;

revoke update on mpi.match_candidates from careflow_app;
grant update (status, resolved_at, resolved_by) on mpi.match_candidates to careflow_app;

create function mpi.erase_patient(p_patient_id uuid, p_reason text, p_idempotency_key text) returns boolean
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_row record;
begin
    if v_actor is null or p_patient_id is null or p_reason is null or p_reason !~ '^[a-z][a-z0-9_]{0,39}$'
       or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    select clinic_id, origin, erased_at into v_row from mpi.patients where id = p_patient_id for update;
    -- one generic refusal whether the patient is missing or the caller may not erase: no existence oracle
    if not found or not iam.has_clinic_role(v_row.clinic_id, array['front_desk']) then
        raise exception 'patient erasure refused' using errcode = '42501';
    end if;
    if v_row.erased_at is not null then
        return false;  -- already erased: a replay writes nothing more
    end if;
    if exists (select 1 from mpi.merges m
               where m.status in ('awaiting_second_approval', 'active') and p_patient_id in (m.master_patient_id, m.subordinate_patient_id)) then
        raise exception 'a patient in a live merge link cannot be erased' using errcode = 'check_violation';
    end if;
    update mpi.patients
       set name_ct = null, name_key_version = null, dob_ct = null, dob_key_version = null, phone_ct = null, phone_key_version = null, erased_at = clock_timestamp()
     where id = p_patient_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_row.origin, v_row.clinic_id, v_actor, 'front_desk', 'patient.erase', 'patient', p_patient_id::text,
            jsonb_build_object('reason', p_reason, 'role_scope', iam.role_scope()), 'audit:patient-erase:' || p_patient_id::text || ':' || p_idempotency_key)
    on conflict (idempotency_key) do nothing;
    return true;
end
$$;
revoke all on function mpi.erase_patient(uuid, text, text) from public;
grant execute on function mpi.erase_patient(uuid, text, text) to careflow_app;

-- Verification: the old column grants are gone, lock_version is the one UPDATE column left on patients, tokens are not updatable, candidates narrowed, the function is executable by the app role only.
do $$
declare
    c text;
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
    if not has_function_privilege('careflow_app', 'mpi.erase_patient(uuid, text, text)', 'execute') or has_function_privilege('public', 'mpi.erase_patient(uuid, text, text)', 'execute') then
        raise exception '0032: erase_patient must be executable by the application role only';
    end if;
end $$;
