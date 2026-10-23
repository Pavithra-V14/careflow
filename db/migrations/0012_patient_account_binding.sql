-- 0012_patient_account_binding: close the hole that let any front-desk user bind ANY account to ANY patient record
-- (BUILD-SPEC.md §17.2, §18.4; CLAUDE.md rules 8 and 10). Fix-forward for 0002. Design: docs/design/msg-screening-binding-design.md.
--
-- NUMBERING NOTE: the design called this migration 0013 (and screenings 0012). 0012 is not written yet and 0013 was asked
-- to go first, and migrations must apply in numeric order without gaps being reordered later, so this is the next free
-- number, 0012. The screenings migration will take the next number after it. No behaviour depends on the number.
--
-- The defect. 0002's policies patients_insert_front_desk / patients_update_front_desk let a front-desk user write ANY column of
-- mpi.patients, including account_id. Setting account_id binds a sign-in account to a patient record, and that account then
-- passes patients_select_own and (later) the patient-side message policies: so one front-desk user could hand any account
-- another person's record. BUILD-SPEC 17.2 requires a claim to prove BOTH a verified contact AND a matching date of birth;
-- that rule exists only in application code, and nothing in the application sets account_id today.
--
-- What this migration does (and does not do).
--   * The application role loses INSERT and UPDATE on the account_id COLUMN. INSERT stays on every other column; UPDATE
--     stays only on the seven columns key rotation and erasure actually update today (ingestion only INSERTs). Column privileges (unlike an
--     RLS WITH CHECK) can see "this column is being changed", and they also stop INSERT ... SELECT, ON CONFLICT DO UPDATE
--     SET account_id and COPY. Columns added to mpi.patients later receive NO grant until one is added deliberately.
--   * A trigger guards the column for EVERY role including the owner: an existing binding can never be changed or cleared;
--     NULL -> account is allowed only for an ACTIVE account of type 'patient' with the same origin as the patient, and never
--     for an erased patient. Every refusal raises ONE generic error, so the trigger tells nobody whether an arbitrary uuid is an
--     active patient account. Only the migrator (owner) can reach the allowed path today: it is how tests seed a bound patient.
--   * It does NOT build the claim function. The function that enforces the 17.2 contact + date-of-birth rule IN THE DATABASE
--     belongs to the patient-portal phase (docs/design/deferred-items.md). Until it exists, the application role cannot bind
--     any account at all, which is the safe default.
--   * Only account_type 'patient' is bindable. 'guardian' waits for the guardian/dependent design (counsel). Relaxing that
--     later is a new migration and fails closed until then.
--
-- A non-null binding is permanent, even for the owner. A wrong or compromised claim is corrected by a migrator-only break-glass:
-- a NEW migration (or a reviewed one-off) that disables this trigger for one statement and writes an audit.log row. A bound
-- patient who is later erased keeps patients_select_own on a row that holds no personal data (noted, accepted).
--
-- Residual risk (BUILD-SPEC.md §18.1): the owner can disable triggers or set session_replication_role, so this trigger binds the
-- application role and every ordinary session, not a migrator-level actor; the migrator is trusted, as for audit.log.

-- ---- column privileges ------------------------------------------------------------------------------------------------

revoke insert, update on mpi.patients from careflow_app;

grant insert (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version,
              erased_at, origin, created_at, idempotency_key)
    on mpi.patients to careflow_app;
grant update (name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, erased_at)
    on mpi.patients to careflow_app;

-- ---- the guard ----------------------------------------------------------------------------------------------------------

-- SECURITY DEFINER because it must read iam.accounts, which RLS hides from front desk (0001); search_path is pinned.
-- A trigger function needs no EXECUTE grant to fire, so nothing is granted to any role.
create function mpi.guard_patient_account() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    if tg_op = 'UPDATE' then
        -- A no-op write (set account_id = account_id, or an update that does not touch it) is fine.
        if new.account_id is not distinct from old.account_id then
            return new;
        end if;
        -- An existing binding is never changed or cleared.
        if old.account_id is not null then
            raise exception 'patient account binding refused' using errcode = 'check_violation';
        end if;
    end if;
    if new.account_id is null then
        return new;
    end if;
    -- NULL -> account (or an INSERT that names one): the patient must be live and the account an active 'patient' account
    -- of the same origin. The same error for every reason, so nothing about iam.accounts can be probed through here.
    if new.erased_at is not null or not exists (
        select 1 from iam.accounts a
         where a.id = new.account_id
           and a.status = 'active'
           and a.account_type = 'patient'
           and a.origin = new.origin
    ) then
        raise exception 'patient account binding refused' using errcode = 'check_violation';
    end if;
    return new;
end
$$;

create trigger patients_guard_account before insert or update of account_id on mpi.patients
    for each row execute function mpi.guard_patient_account();

revoke all on function mpi.guard_patient_account() from public, careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function mpi.guard_patient_account() from %I', r);
        end if;
    end loop;
end $$;
