-- 0069_clinic_origin_only_for_members (owner decision of 2026-11-08; CLAUDE.md rules 8, 10). Fix forward from 0048: no merged migration is edited.
--
-- ops.clinic_origin(clinic_id) (0048) is a SECURITY DEFINER helper the row-level policies use to pin a row's origin to its clinic's. Any signed-in application session could call it with any clinic id and learn
-- whether that clinic EXISTS and what its origin is: an existence oracle across clinics. It now answers only a caller who belongs to the clinic: a holder of a live staff role there (any role, the queue worker
-- included; the active-role narrowing of 0028 applies) or a patient account bound to a patient record of that clinic. Everyone else, a session with no claims included, gets NULL, the same answer as for a
-- clinic that does not exist. Every existing use is by one of those callers (the policies that call it already require a staff role of the clinic, or the account's own bound record), so no caller changes.
--
-- The matching change for client-chosen idempotency keys is in the application (careflow/app.py key_scope): the keys that several tables keep unique across all clinics are stored behind the caller's own
-- account prefix, so a refusal can never tell a caller that a key exists in another clinic. Migrations 0001 to 0068 keep their constraints as they are.

create or replace function ops.clinic_origin(p_clinic_id uuid) returns text
    language sql stable security definer set search_path = ''
as $$
    select c.origin from iam.clinics c
     where c.id = p_clinic_id and auth.uid() is not null
       and (iam.has_clinic_role(c.id, array['front_desk', 'nurse', 'doctor', 'admin', 'business_owner', 'auditor', 'queue_worker'])
            or exists (select 1 from mpi.patients pt where pt.clinic_id = c.id and pt.account_id = auth.uid()))
$$;

do $$
declare r text; f text := 'ops.clinic_origin(uuid)';
begin
    if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
        raise exception '0069: % must stay executable by the application login only', f;
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
            raise exception '0069: % must not be executable by %', f, r;
        end if;
    end loop;
end $$;
