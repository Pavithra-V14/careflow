-- 0038_backfill_review_task_function: a durable review task for a Scheduling run that needs a person (Phase 3 follow-up, owner decision of 2026-11-06 (b); BUILD-SPEC.md 7.2, 9; CLAUDE.md rules 5, 6, 8, 10).
--
-- Until now such a run ended in a visible status and a deterministic id with no row (careflow/scheduling_ports.StateOnlyTasks), because a front-desk session may not insert a task of this kind and the owner
-- did not want a table-wide insert policy. This migration adds NO policy and NO grant on agents.review_tasks. It adds one SECURITY DEFINER function that is the only way front desk can raise this task:
--
--   agents.raise_backfill_task(p_clinic_id, p_slot_id, p_status, p_priority, p_assigned_role, p_idempotency_key) returns uuid
--
--   * the caller must be signed in (auth.uid()) and hold front_desk in THE CLINIC NAMED: otherwise the one fixed refusal, so the function cannot be used to learn whether a slot or task exists;
--   * the slot must exist in that clinic (the origin of the task is taken from the clinic row, never from an argument);
--   * the status is one of the graph's three fixed words, the priority one of the table's two, the role one of nurse / doctor (never front_desk: front desk cannot see or resolve a backfill task under the table policies, so a task assigned to it would be silently orphaned);
--   * the idempotency key must be exactly 'sched-hold:<slot id>:<status>' (the key the graph already uses), so one run raises one task per status; a replay returns the same task and writes nothing more;
--   * the task is kind `backfill_needs_human`, status open, `source_message_id` 'slot:<slot id>', provenance only {"slot_id", "status"}: ids and a fixed word, never patient text;
--   * one audit.log row is written by the function itself (key prefix `sys:`, reserved for the database functions since 0035), naming the real caller.
--
-- Nothing here touches the red-flag path: a red flag is raised by the screening code with its own immediate/nurse task; this kind can never be a red flag (the table check forbids a quiet red_flag).
-- Execute is granted to careflow_app only (revoked from PUBLIC); the function runs as its owner, so row-level security on review_tasks is bypassed BY DESIGN for this one insert, and every condition above
-- is the function own.
--
-- A replay with a different priority or role returns the ORIGINAL task unchanged.
-- RESIDUAL. The caller chooses the priority and role from the allowed words (a front-desk person could raise a standard task to a doctor); the real caller is the Scheduling runner, which passes the clinic
-- configured values, and every call is audited under the caller name.

create function agents.raise_backfill_task(p_clinic_id uuid, p_slot_id uuid, p_status text, p_priority text, p_assigned_role text, p_idempotency_key text) returns uuid
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
    v_id     uuid;
    v_slot   int;
begin
    if v_actor is null or p_clinic_id is null or p_slot_id is null or not iam.has_clinic_role(p_clinic_id, array['front_desk']) then
        raise exception 'backfill task refused' using errcode = 'insufficient_privilege';
    end if;
    if p_status is null or p_status not in ('needs_human', 'booking_needs_human', 'held_unassessable')
       or p_priority is null or p_priority not in ('immediate', 'standard')
       or p_assigned_role is null or p_assigned_role not in ('nurse', 'doctor')
       or p_idempotency_key is distinct from 'sched-hold:' || p_slot_id::text || ':' || p_status then
        raise exception 'backfill task refused' using errcode = 'insufficient_privilege';
    end if;
    select origin into v_origin from iam.clinics where id = p_clinic_id;
    select count(*) into v_slot from sched.slots where id = p_slot_id and clinic_id = p_clinic_id;
    if v_origin is null or v_slot = 0 then
        raise exception 'backfill task refused' using errcode = 'insufficient_privilege';
    end if;
    select id into v_id from agents.review_tasks where idempotency_key = p_idempotency_key and clinic_id = p_clinic_id and kind = 'backfill_needs_human';
    if found then
        return v_id;  -- a replay: the same task, nothing more written
    end if;
    insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, status, source_message_id, provenance, idempotency_key)
    values (p_clinic_id, v_origin, 'backfill_needs_human', p_priority, p_assigned_role, 'open', 'slot:' || p_slot_id::text,
            jsonb_build_object('slot_id', p_slot_id::text, 'status', p_status), p_idempotency_key)
    on conflict (idempotency_key) do nothing
    returning id into v_id;
    if v_id is null then
        -- A concurrent first call with the same key won the insert: return its task. Otherwise the key belongs to another clinic or another kind of task.
        select id into v_id from agents.review_tasks where idempotency_key = p_idempotency_key and clinic_id = p_clinic_id and kind = 'backfill_needs_human';
        if found then
            return v_id;
        end if;
        raise exception 'backfill task refused' using errcode = 'insufficient_privilege';
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'front_desk', 'backfill.task_raised', 'slot', p_slot_id::text,
            jsonb_build_object('status', p_status, 'task_id', v_id::text, 'role_scope', iam.role_scope()), 'sys:backfill-task:' || v_id::text);
    return v_id;
end
$$;

revoke all on function agents.raise_backfill_task(uuid, uuid, text, text, text, text) from public;
grant execute on function agents.raise_backfill_task(uuid, uuid, text, text, text, text) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function agents.raise_backfill_task(uuid, uuid, text, text, text, text) from %I', r);
        end if;
    end loop;
end $$;

do $$
begin
    if has_function_privilege('public', 'agents.raise_backfill_task(uuid, uuid, text, text, text, text)', 'execute') then
        raise exception '0038: the function must not be executable by PUBLIC';
    end if;
    if not has_function_privilege('careflow_app', 'agents.raise_backfill_task(uuid, uuid, text, text, text, text)', 'execute') then
        raise exception '0038: the application role must be able to execute the function';
    end if;
    if exists (select 1 from pg_roles r where r.rolname in ('anon', 'authenticated', 'service_role')
                and has_function_privilege(r.rolname, 'agents.raise_backfill_task(uuid, uuid, text, text, text, text)', 'execute')) then
        raise exception '0038: anon, authenticated and service_role must not execute the function';
    end if;
end $$;
