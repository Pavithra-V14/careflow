-- 0033_expired_hold_sweep: let the worker's scheduled tick tidy expired holds (pre-approved by the owner 2026-11-05; docs/design/expired-hold-sweep.md; referral intake stays manual).
--
-- Correctness never depended on this: every operation that touches a slot already treats an active hold past its time as expired (under a row lock) and frees the slot, so a late tick cannot make a slot
-- unbookable. What was missing is the TIDY-UP: hold rows nobody touches stay 'active' and their slots stay 'held' in the lists. The explicit sweep (SlotManager.release_expired_holds) needs UPDATE on
-- sched.holds and sched.slots, which the worker's database identity deliberately does not have (it is a narrow identity and stays one).
--
-- THE FIX is one SECURITY DEFINER function, not a table grant: sched.release_expired_holds() expires every active hold past its time (judged on the DATABASE clock) and reopens the slots left 'held' with no
-- active hold, ONLY in clinics where the caller holds a live queue_worker grant, and nothing else. A caller who is not a queue worker of any clinic gets one generic refusal. It writes no audit row (it is a
-- system tidy-up, not a decision) and touches no patient data.
--
-- NEW GRANT (grants ledger): EXECUTE on sched.release_expired_holds() to careflow_app (the shared application login; the function itself checks the caller is a queue worker).

create function sched.release_expired_holds() returns integer
    language plpgsql security definer set search_path = ''
as $$
declare
    v_worker uuid := auth.uid();
    v_clinics uuid[];
    v_expired integer;
begin
    if v_worker is null then
        raise exception 'sweep refused' using errcode = '42501';
    end if;
    select coalesce(array_agg(c.id), '{}') into v_clinics from iam.clinics c where iam.has_clinic_role(c.id, array['queue_worker']);
    if cardinality(v_clinics) = 0 then
        raise exception 'sweep refused' using errcode = '42501';
    end if;
    -- Lock the held slots FIRST (the same row lock every slot operation takes), as its own statement, so the statements below see every hold committed by an operation that was holding the lock
    -- (reviewer finding 2026-11-05: without it a concurrent hold could be missed and its slot reopened).
    perform 1 from sched.slots where status = 'held' and clinic_id = any(v_clinics) order by id for update;
    with gone as (
        update sched.holds set status = 'expired'
         where status = 'active' and expires_at <= clock_timestamp() and clinic_id = any(v_clinics)
        returning slot_id
    )
    select count(*) into v_expired from gone;
    update sched.slots s set status = 'open'
     where s.status = 'held' and s.clinic_id = any(v_clinics)
       and not exists (select 1 from sched.holds h where h.slot_id = s.id and h.status = 'active' and h.expires_at > clock_timestamp());
    return v_expired;
end
$$;
revoke all on function sched.release_expired_holds() from public;
grant execute on function sched.release_expired_holds() to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function sched.release_expired_holds() from %I', r);
        end if;
    end loop;
end $$;

do $$
begin
    if not has_function_privilege('careflow_app', 'sched.release_expired_holds()', 'execute') or has_function_privilege('public', 'sched.release_expired_holds()', 'execute') then
        raise exception '0033: release_expired_holds must be executable by the application role only';
    end if;
end $$;
