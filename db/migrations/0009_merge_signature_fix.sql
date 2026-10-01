-- 0009_merge_signature_fix: fix-forward for 0008's update policy on mpi.merges (CLAUDE.md rule 10).
--
-- 0008's UPDATE policy required `second_approved_by = auth.uid()` in its WITH CHECK, which is evaluated on the
-- WHOLE new row. So once a merge was active, nobody except the second approver could update it, and an
-- unmerge by any other front-desk person was refused. A row-level-security check cannot see which column is
-- being changed; a trigger can compare OLD and NEW. So:
--   * the policy now checks only the role (front desk of the clinic);
--   * a trigger requires that whoever SETS second_approved_by or unmerged_by sets it to themselves
--     (auth.uid()), and only sets it once. A person still cannot sign as somebody else.
-- The trigger function is SECURITY DEFINER because the application role cannot call auth.uid() by name (§18.1);
-- search_path is pinned. auth.uid() reads the same per-transaction claims the backend set.

drop policy merges_update_front_desk on mpi.merges;
create policy merges_update_front_desk on mpi.merges for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

create function mpi.check_merge_signatures() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    if new.second_approved_by is distinct from old.second_approved_by then
        if old.second_approved_by is not null then
            raise exception 'the second approval is already recorded and cannot be changed' using errcode = 'check_violation';
        end if;
        if new.second_approved_by is distinct from auth.uid() then
            raise exception 'you can only sign a second approval as yourself' using errcode = 'insufficient_privilege';
        end if;
    end if;
    if new.unmerged_by is distinct from old.unmerged_by then
        if old.unmerged_by is not null then
            raise exception 'the unmerge is already recorded and cannot be changed' using errcode = 'check_violation';
        end if;
        if new.unmerged_by is distinct from auth.uid() then
            raise exception 'you can only sign an unmerge as yourself' using errcode = 'insufficient_privilege';
        end if;
    end if;
    return new;
end
$$;
revoke all on function mpi.check_merge_signatures() from public;

create trigger merges_check_signatures before update on mpi.merges
    for each row execute function mpi.check_merge_signatures();
