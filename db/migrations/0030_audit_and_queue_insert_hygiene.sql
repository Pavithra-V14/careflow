-- 0030_audit_and_queue_insert_hygiene: tighten two INSERT policies (review M3 and the write half of M4, 2026-11-04; docs/design/review-2026-11-04.md). Fix-forward for 0001 and 0006 (CLAUDE.md rule 10).
-- Both changes only REMOVE ways to write; no table, role, function or grant is added or widened.
--
-- M3. 0001's audit.log INSERT policy checked only `actor_id is not distinct from auth.uid()`. So any application-role session could write an audit row for ANY clinic (clinic_id was free), with any
-- action and details, and a session with no claims could write "system" rows. A clinic-B auditor then read forged rows as clinic B's audit trail. The policy now requires all of:
--   * a named actor who is the signed-in caller (actor_id = auth.uid(), not null),
--   * a clinic (clinic_id not null), and
--   * that the caller holds a LIVE front_desk, nurse, doctor or admin grant at THAT clinic (iam.has_clinic_role, which the active-role header narrows, so a row written under the active role is still allowed).
--     These are exactly the roles whose requests write audit rows (careflow/audit.py, careflow/messages.py). The worker never writes one with the application role (its screening audit goes through the
--     SECURITY DEFINER msg.record_screening), and an auditor must not be able to write into the trail it reads; reviewer pass 2026-11-04.
-- The migrator (BYPASSRLS) still writes system rows; the application never did (careflow/audit.py and careflow/messages.py always pass a clinic and the signed-in actor).
--
-- M4 (writes). 0006 let front desk, nurse, doctor and admin insert any ops.queue row, including ones that pretend to be claimed, completed or dead-lettered, or carry an error text. A producer may now
-- insert only a CLEAN new job: state unclaimed, no attempts, no claim, no completion, no dead-letter time, no error. The worker's lifecycle updates are unchanged (column-level UPDATE, worker policy).
-- NOT in this migration: hiding `payload` and `last_error` from staff. The application role is shared by staff and the worker and row-level security cannot hide a column, so that needs a different
-- shape (a view or definer function = a new grant) and is a design note awaiting the owner (docs/design/review-2026-11-04.md).

drop policy log_insert_as_self on audit.log;
create policy log_insert_own_clinic on audit.log for insert to careflow_app
    with check (
        actor_id is not null
        and actor_id = auth.uid()
        and clinic_id is not null
        and iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin'])
    );

drop policy queue_insert on ops.queue;
create policy queue_insert on ops.queue for insert to careflow_app
    with check (
        iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker'])
        and state = 'unclaimed'
        and attempt_count = 0
        and claimed_at is null
        and claimed_by is null
        and completed_at is null
        and dead_lettered_at is null
        and last_error is null
    );

-- Verification: the new policies exist, the old audit one is gone.
do $$
begin
    if exists (select 1 from pg_policies where schemaname = 'audit' and tablename = 'log' and policyname = 'log_insert_as_self') then
        raise exception '0030: the old audit insert policy is still present';
    end if;
    if (select count(*) from pg_policies where schemaname = 'audit' and tablename = 'log' and cmd = 'INSERT') <> 1 then
        raise exception '0030: audit.log must have exactly one INSERT policy';
    end if;
    if (select count(*) from pg_policies where schemaname = 'ops' and tablename = 'queue' and cmd = 'INSERT') <> 1 then
        raise exception '0030: ops.queue must have exactly one INSERT policy';
    end if;
end $$;
