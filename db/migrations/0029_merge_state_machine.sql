-- 0029_merge_state_machine: fix-forward for 0008 and 0009 (CLAUDE.md rule 10; review H1 and M2 of 2026-11-04, docs/design/review-2026-11-04.md).
--
-- THE HOLE (H1). 0008 lets front desk update `status` and the lifecycle columns of mpi.merges, and its constraints look at ONE row at a time. So one front-desk person could run
--     update mpi.merges set status = 'active', unmerged_at = null where status = 'unmerged'
-- and the row would pass `merges_active_needs_two_people` (second_approved_by and activated_at were still set from the first life of the merge). That re-activates a merge with NO second approval,
-- and the flat-link check (0008) runs on INSERT only, so chains could be rebuilt (link B under C while A->B is unmerged, then re-activate A->B). BUILD-SPEC 12.5 (two people) and 17.2 (flat links) both break.
--
-- THE FIX. A BEFORE UPDATE trigger gives the merge a real state machine:
--   * awaiting_second_approval -> active, awaiting_second_approval -> unmerged (cancel), active -> unmerged. Nothing else. `unmerged` is terminal: an unmerged row is never revived (a new merge is a
--     new row, proposed and approved by two people again).
--   * once set, activated_at, unmerged_at, second_approved_by (0009), unmerged_by (0009) and the two step keys never change.
--   * a same-status update cannot touch the lifecycle columns, and a CANCEL (awaiting -> unmerged) cannot also set an approval (it must not look as if the merge was once active).
--   * on activation the flat-link rule is checked again against the OTHER live links.
-- It is a plain (invoker) trigger: it applies to every role that can update the table, the owner included, and `ENABLE ALWAYS` keeps it firing under session_replication_role = replica.
--
-- M2. Nothing tied a merge to the review row that put the pair in front of a person. A BEFORE INSERT trigger now requires, for a PROPOSAL (status awaiting_second_approval, the only status the
-- application role may insert), that the candidate is `pending` and names exactly the same two patients as the merge (either way round). A candidate from another clinic is left to the foreign
-- key (0014), which gives the same 23503 as before. The function is SECURITY DEFINER with a pinned search_path so that a caller who cannot read match_candidates (a worker, a nurse) still reaches the
-- row-level-security refusal on mpi.merges rather than a check error that would reveal whether a candidate exists; it returns nothing but its own error.
--
-- The tie is only as strong as the review row, so the review row is made immutable too (trigger mpi.check_candidate_immutable): its clinic, pair, kinds, reason and origin never change, `resolved` is terminal, and a recorded resolver and time never change. The engine only ever sets status/resolved_at/resolved_by once.
--
-- No new table, role, grant or policy. Residuals: the owner (migrator) can still delete merge rows (test cleanup depends on it); the state machine does not stop the owner disabling the trigger.

create function mpi.check_merge_transition() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if new.status is distinct from old.status
       and not ((old.status = 'awaiting_second_approval' and new.status in ('active', 'unmerged'))
                or (old.status = 'active' and new.status = 'unmerged')) then
        raise exception 'a merge cannot go from % to %', old.status, new.status using errcode = 'check_violation';
    end if;
    if (old.activated_at is not null and new.activated_at is distinct from old.activated_at)
       or (old.unmerged_at is not null and new.unmerged_at is distinct from old.unmerged_at)
       or (old.approve_idempotency_key is not null and new.approve_idempotency_key is distinct from old.approve_idempotency_key)
       or (old.unmerge_idempotency_key is not null and new.unmerge_idempotency_key is distinct from old.unmerge_idempotency_key) then
        raise exception 'a recorded step of a merge cannot be changed' using errcode = 'check_violation';
    end if;
    if new.status = old.status
       and (new.second_approved_by, new.activated_at, new.unmerged_at, new.unmerged_by)
           is distinct from (old.second_approved_by, old.activated_at, old.unmerged_at, old.unmerged_by) then
        raise exception 'a merge changes its approvals only by changing its status' using errcode = 'check_violation';
    end if;
    if old.status = 'awaiting_second_approval' and new.status = 'unmerged' and (new.activated_at is not null or new.second_approved_by is not null) then
        raise exception 'a cancelled proposal cannot carry an approval' using errcode = 'check_violation';
    end if;
    if new.status = 'active' and old.status <> 'active' then
        if exists (select 1 from mpi.merges m
                   where m.id <> new.id and m.status in ('awaiting_second_approval', 'active')
                     and (m.subordinate_patient_id = new.master_patient_id or m.master_patient_id = new.subordinate_patient_id)) then
            raise exception 'merge links must stay flat: the master is already a subordinate, or the subordinate is already a master'
                using errcode = 'check_violation';
        end if;
    end if;
    return new;
end
$$;
revoke all on function mpi.check_merge_transition() from public;

create trigger merges_check_transition before update on mpi.merges
    for each row execute function mpi.check_merge_transition();
alter table mpi.merges enable always trigger merges_check_transition;

create function mpi.check_merge_candidate() returns trigger
    language plpgsql security definer set search_path = ''
as $$
declare
    c record;
begin
    if new.status <> 'awaiting_second_approval' then
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
revoke all on function mpi.check_merge_candidate() from public;

create trigger merges_check_candidate before insert on mpi.merges
    for each row execute function mpi.check_merge_candidate();
alter table mpi.merges enable always trigger merges_check_candidate;

create function mpi.check_candidate_immutable() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if (new.clinic_id, new.patient_id, new.candidate_patient_id, new.matched_kinds, new.reason, new.origin)
       is distinct from (old.clinic_id, old.patient_id, old.candidate_patient_id, old.matched_kinds, old.reason, old.origin) then
        raise exception 'a review row names its pair once; it cannot be changed' using errcode = 'check_violation';
    end if;
    if old.status = 'resolved' and (new.status <> 'resolved' or new.resolved_at is distinct from old.resolved_at or new.resolved_by is distinct from old.resolved_by) then
        raise exception 'a resolved review row cannot be reopened or re-attributed' using errcode = 'check_violation';
    end if;
    return new;
end
$$;
revoke all on function mpi.check_candidate_immutable() from public;

create trigger match_candidates_check_immutable before update on mpi.match_candidates
    for each row execute function mpi.check_candidate_immutable();
alter table mpi.match_candidates enable always trigger match_candidates_check_immutable;

-- Verification: the triggers exist, are enabled ALWAYS, and the functions are not executable by the application role or PUBLIC.
do $$
declare
    t text;
begin
    foreach t in array array['merges_check_transition', 'merges_check_candidate', 'match_candidates_check_immutable'] loop
        if not exists (select 1 from pg_trigger where tgrelid in ('mpi.merges'::regclass, 'mpi.match_candidates'::regclass) and tgname = t and tgenabled = 'A') then
            raise exception '0029: trigger % is missing or not ENABLE ALWAYS', t;
        end if;
    end loop;
    if has_function_privilege('careflow_app', 'mpi.check_merge_transition()', 'execute')
       or has_function_privilege('careflow_app', 'mpi.check_merge_candidate()', 'execute')
       or has_function_privilege('careflow_app', 'mpi.check_candidate_immutable()', 'execute') then
        raise exception '0029: the application role must not hold EXECUTE on the merge trigger functions';
    end if;
end $$;
