-- 0036_merge_candidate_trigger_self_merge: fix-forward for 0029 (CLAUDE.md rule 10; found by CI on main, 2026-11-05).
--
-- 0029's BEFORE INSERT trigger mpi.check_merge_candidate() ties a proposal to the pending review row of the SAME two patients. For a merge of a record into ITSELF that comparison is meaningless, and the trigger
-- fired before the table's own CHECK constraint (merges_not_self), so the refusal carried a different message than the one 0008 designed. A merge into itself is now left to merges_not_self (and to the flat-link
-- trigger), exactly as before 0029. Nothing else changes; no grant.

create or replace function mpi.check_merge_candidate() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    c record;
begin
    if new.status <> 'awaiting_second_approval' or new.master_patient_id = new.subordinate_patient_id then
        return new;
    end if;
    -- Only a front-desk proposer of THIS clinic gets a check error from here; anyone else falls through to the row-level-security refusal, so this definer function cannot be used to probe whether a candidate id exists.
    if not iam.has_clinic_role(new.clinic_id, array['front_desk']) then
        return new;
    end if;
    select status, patient_id, candidate_patient_id into c
      from mpi.match_candidates where id = new.candidate_id and clinic_id = new.clinic_id;
    if not found then
        return new;  -- not a candidate of this clinic: the composite foreign key (0014) refuses it
    end if;
    if c.status <> 'pending' then
        raise exception 'a merge must start from a pending review row' using errcode = 'check_violation';
    end if;
    if not ((c.patient_id = new.master_patient_id and c.candidate_patient_id = new.subordinate_patient_id)
            or (c.patient_id = new.subordinate_patient_id and c.candidate_patient_id = new.master_patient_id)) then
        raise exception 'the review row does not name these two patients' using errcode = 'check_violation';
    end if;
    return new;
end
$$;

do $$
begin
    if has_function_privilege('careflow_app', 'mpi.check_merge_candidate()', 'execute') or has_function_privilege('public', 'mpi.check_merge_candidate()', 'execute') then
        raise exception '0036: the trigger function must not be executable by the application role or PUBLIC';
    end if;
end $$;
