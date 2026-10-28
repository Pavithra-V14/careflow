-- 0024_worker_role_exclusive: an account that holds `queue_worker` holds no other role, and an account that holds any other role cannot be given `queue_worker`
-- (owner decision 2026-10-28; docs/design/planted-token-design.md, docs/runbooks/staff-accounts.md). CLAUDE.md rules 8, 10.
--
-- WHY. The unattended worker is a system identity. Row-level-security policies are permissive and OR together, so a worker account that ALSO holds front_desk in a clinic keeps front
-- desk's direct INSERT on mpi.identity_tokens and mpi.match_candidates, which is exactly the path migration 0022 closes for the worker alone. Making the two roles mutually exclusive
-- turns that operational rule into a database rule.
--
-- WHAT. A BEFORE INSERT OR UPDATE trigger on iam.staff_roles. Only ACTIVE grants (revoked_at is null) conflict; a revoked grant never does. A worker may hold queue_worker in several
-- clinics (it serves more than one). The trigger function is SECURITY DEFINER with search_path = '' because it must see ALL of the account's grants in every clinic, which the caller's
-- row-level security may hide. It reads and refuses; it writes nothing. The refusal uses SQLSTATE 23514 with a fixed message (no ids).
--
-- CONCURRENCY. Two sessions inserting front_desk and queue_worker for one account at the same moment cannot see each other's uncommitted row, so the check alone would let both
-- commit. The trigger therefore takes a per-account transaction advisory lock before it looks (a fresh statement snapshot under READ COMMITTED then sees the winner's committed row).
-- Writers must use READ COMMITTED (the default): under REPEATABLE READ or SERIALIZABLE the transaction snapshot predates the lock.
--
-- FAIL CLOSED. The function reads iam.staff_roles, which has FORCE row-level security, as its OWNER; the migrating role must therefore bypass RLS (superuser or BYPASSRLS) or the
-- check would see no rows and silently pass. The migration refuses to apply otherwise. The trigger is ENABLE ALWAYS so that session_replication_role = replica, pg_restore and
-- logical-replication apply do not skip it.
--
-- RESIDUALS (stated; reviewed by rls-security-reviewer 2026-10-28): the table owner or a superuser can still DISABLE or DROP the trigger (a trigger cannot defend against its owner; the
-- stop-gap query is the detective control); existing rows are not re-checked; grants of suspended or closed accounts count as conflicting (conservative); exclusivity is per ACCOUNT, so
-- one human with two accounts is out of scope; the CI project holds almost no live-origin accounts, so the CI step is nearly vacuous there and the real value is the same command in
-- the dev and production projects (docs/runbooks/staff-accounts.md); the error is 23514 like staff_roles_role_check, and careflow/mpi.py maps only 42501, so no collision.
--
-- NOT DONE HERE: existing rows are not re-checked (a trigger sees only future writes). The read-only stop-gap query in scripts/verify_app_role.py and in the CI migrate job reports any
-- existing violation (careflow/ops_checks.py). No grant is added or changed; the function needs no EXECUTE grant to fire as a trigger.

do $$
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception 'migration 0024 needs a migrating role that bypasses row-level security' using errcode = '42501';
    end if;
end $$;

create function iam.keep_queue_worker_exclusive() returns trigger
    language plpgsql security definer set search_path = ''
as $$
begin
    if new.revoked_at is not null then
        return new;  -- revoking cannot create a violation, so it takes no lock
    end if;
    -- one writer at a time per account: see CONCURRENCY in the header
    perform pg_advisory_xact_lock(hashtextextended('iam.staff_roles.worker_exclusive:' || new.account_id::text, 0));
    if new.role = 'queue_worker' then
        if exists (
            select 1 from iam.staff_roles r
             where r.account_id = new.account_id and r.revoked_at is null and r.role <> 'queue_worker' and r.id <> new.id
        ) then
            raise exception 'an account that holds another role cannot hold queue_worker' using errcode = '23514', constraint = 'staff_roles_queue_worker_exclusive';
        end if;
    else
        if exists (
            select 1 from iam.staff_roles r
             where r.account_id = new.account_id and r.revoked_at is null and r.role = 'queue_worker' and r.id <> new.id
        ) then
            raise exception 'a queue_worker account cannot hold another role' using errcode = '23514', constraint = 'staff_roles_queue_worker_exclusive';
        end if;
    end if;
    return new;
end
$$;

create trigger staff_roles_queue_worker_exclusive
    before insert or update of account_id, role, revoked_at on iam.staff_roles
    for each row execute function iam.keep_queue_worker_exclusive();

revoke all on function iam.keep_queue_worker_exclusive() from public, careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function iam.keep_queue_worker_exclusive() from %I', r);
        end if;
    end loop;
end $$;

alter table iam.staff_roles enable always trigger staff_roles_queue_worker_exclusive;
