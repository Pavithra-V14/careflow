-- 0067_erase_visit_readiness_rows (owner decision of 2026-11-08; BUILD-SPEC.md 20.1; CLAUDE.md rules 8, 10). Fix forward.
-- NOTE: the first version of this file failed on a syntax error and was ROLLED BACK by the runner on the CI project (it was never recorded in any ledger); it was corrected in place before any database had it.
--
-- ops.visit_facts, ops.visit_readiness_answers and ops.visit_briefs (0052) hold closed words about a patient and a visit (a fact name such as has_pacemaker, a checklist answer, brief lines). They were append-only
-- with no way out, so the audited erasure left them. Now the audited two-person erasure DELETES a patient's rows from the three tables in the same transaction (counts only in the audit row, key
-- `visit_readiness`), and the guard allows a DELETE only for a patient who is already erased; every other change, every truncate and every delete for a live patient is still refused for everyone, the owner
-- included. A one-time backfill removes the rows of patients erased before this migration.
--
-- The approval function is patched IN PLACE from its stored definition (it carries the verified-second-factor check of 0065, which a re-issue from an older text would lose); the patch asserts it changed things.

create or replace function ops.visit_readiness_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'DELETE' and exists (select 1 from mpi.patients p where p.id = old.patient_id and p.clinic_id = old.clinic_id and p.erased_at is not null) then
        return old;  -- the patient is erased (0067): their visit-readiness rows go with them
    end if;
    raise exception '% is append-only: % is not permitted', tg_table_name, tg_op using errcode = 'restrict_violation';
end
$$;

do $$
declare f regprocedure := 'mpi.approve_patient_erasure(uuid,text)'::regprocedure; d text; n text;
begin
    d := pg_get_functiondef(f);
    n := replace(d, E'    v_notes   integer;\n', E'    v_notes   integer;\n    v_visit   integer;\n    v_part    integer;\n');
    if n = d then raise exception '0067: the declaration anchor was not found'; end if;
    d := n;
    n := replace(d, E'    update mpi.erasure_requests set status = ''erased''',
                    E'    -- the visit-readiness rows of the patient (0067): the guard allows the delete now that the patient row is erased
'
                    || E'    v_visit := 0;
'
                    || E'    delete from ops.visit_facts where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id;
'
                    || E'    get diagnostics v_part = row_count; v_visit := v_visit + v_part;
'
                    || E'    delete from ops.visit_readiness_answers where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id;
'
                    || E'    get diagnostics v_part = row_count; v_visit := v_visit + v_part;
'
                    || E'    delete from ops.visit_briefs where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id;
'
                    || E'    get diagnostics v_part = row_count; v_visit := v_visit + v_part;
'
                    || E'    update mpi.erasure_requests set status = ''erased''');
    if n = d then raise exception '0067: the request-update anchor was not found'; end if;
    d := n;
    n := replace(d, '''notes'', v_notes)', '''notes'', v_notes, ''visit_readiness'', v_visit)');
    if n = d then raise exception '0067: the audit details anchor was not found'; end if;
    execute n;
end $$;

-- backfill: patients erased before this migration
delete from ops.visit_facts f using mpi.patients p where f.patient_id = p.id and f.clinic_id = p.clinic_id and p.erased_at is not null;
delete from ops.visit_readiness_answers a using mpi.patients p where a.patient_id = p.id and a.clinic_id = p.clinic_id and p.erased_at is not null;
delete from ops.visit_briefs b using mpi.patients p where b.patient_id = p.id and b.clinic_id = p.clinic_id and p.erased_at is not null;

do $$
declare r text; f text := 'mpi.approve_patient_erasure(uuid,text)';
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0067: the migrating role must bypass row-level security';
    end if;
    if position('visit_readiness' in pg_get_functiondef(f::regprocedure)) = 0 or position('not iam.aal2_session()' in pg_get_functiondef(f::regprocedure)) = 0 then
        raise exception '0067: the erasure function lost a part';
    end if;
    if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
        raise exception '0067: % must be executable by the application login and not by PUBLIC', f;
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
            raise exception '0067: % must not be executable by %', f, r;
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'ops.visit_facts', 'delete') or has_table_privilege('careflow_app', 'ops.visit_readiness_answers', 'delete') or has_table_privilege('careflow_app', 'ops.visit_briefs', 'delete') then
        raise exception '0067: the application role must not delete visit-readiness rows directly';
    end if;
end $$;
