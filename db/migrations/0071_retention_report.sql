-- 0071_retention_report (owner decision 2026-11-10: a report-only retention job; BUILD-SPEC.md 9.4; CLAUDE.md rules 3, 5, 8, 10; BUILD-SPEC.md 20.2).
--
-- BUILD-SPEC 9.4 asks for a scheduled, audited job that enforces the retention periods. Almost everything it would delete is append-only BY DESIGN (agent checkpoints, 0021: "append-only for everyone, the owner
-- included"; clinical results, notes, documents and messages: never deleted; the audit log, consent records and break-glass grants: permanent), and the owner ruled that the job must never touch an append-only
-- ledger. So this job only COUNTS. The sanctioned way to remove a clinical record stays the two-person audited erasure (0032, 0056, 0057, 0067); no guard is changed here.
--
-- ops.retention_report_as_worker(clinic, checkpoint_cutoff, encounter_cutoff): callable ONLY by the queue worker of the clinic (not an admin act: no second factor, like 0063). It writes NOTHING except ONE
-- audit row per clinic per UTC day (action `retention.report`, key `sys:retention-report:<clinic>:<day>`, a repeat the same day is a no-op) and returns:
--   * checkpoint_threads_due   agent-checkpoint threads whose newest checkpoint is older than the cutoff (the "90 days" row of 9.4: the cutoff comes from a required setting, there is no default);
--   * patients_past_period     live (not erased) patients whose newest started, confirmed booking is older than the cutoff;
--   * patients_no_encounter    live patients with no started, confirmed booking at all. UNKNOWN IS NOT A VALUE: they are counted apart and are never counted as past the period.
-- CLINICAL-PLACEHOLDER: needs clinician sign-off. What counts as an "encounter" (here: a confirmed booking whose slot has started; a cancelled booking is not one) and the minor and pregnancy periods of 9.4
-- (the database holds no age or pregnancy fact it could use) are clinical/legal decisions. The report does not assess them and says so in its audit row.
-- Prompt and response bodies: nothing in the database stores them, so there is nothing to count or delete (the audit row says `prompt_response_bodies: not_stored`).
-- The counts are numbers only: no patient id, no thread id leaves the function.

create function ops.retention_report_as_worker(p_clinic_id uuid, p_checkpoint_cutoff timestamptz, p_encounter_cutoff timestamptz)
    returns table (checkpoint_threads_due bigint, patients_past_period bigint, patients_no_encounter bigint, created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor   uuid := auth.uid();
    v_origin  text;
    v_threads bigint := null;
    v_past    bigint := null;
    v_none    bigint := null;
    v_made    integer;
begin
    if p_clinic_id is null or v_actor is null or not iam.has_clinic_role(p_clinic_id, array['queue_worker']) then
        raise exception 'retention report refused' using errcode = 'insufficient_privilege';
    end if;
    if p_checkpoint_cutoff is not null and p_checkpoint_cutoff > clock_timestamp() or p_encounter_cutoff is not null and p_encounter_cutoff > clock_timestamp() then
        raise exception 'a retention cutoff cannot be in the future' using errcode = 'insufficient_privilege';
    end if;
    if p_checkpoint_cutoff is null and p_encounter_cutoff is null then
        raise exception 'a retention report needs at least one cutoff' using errcode = 'insufficient_privilege';  -- an empty call must not use up the day's audit row
    end if;
    select c.origin into v_origin from iam.clinics c where c.id = p_clinic_id;
    if p_checkpoint_cutoff is not null then
        select count(*) into v_threads from (
            select cp.thread_id from agents.graph_checkpoints cp where cp.clinic_id = p_clinic_id group by cp.thread_id having max(cp.created_at) < p_checkpoint_cutoff
        ) t;
    end if;
    if p_encounter_cutoff is not null then
        select count(*) filter (where e.last_start < p_encounter_cutoff), count(*) filter (where e.last_start is null) into v_past, v_none
          from (
            select pt.id, (select max(s.starts_at) from sched.bookings b join sched.slots s on s.id = b.slot_id and s.clinic_id = b.clinic_id
                            where b.clinic_id = pt.clinic_id and b.patient_id = pt.id and b.status = 'confirmed' and s.starts_at <= clock_timestamp()) as last_start
              from mpi.patients pt where pt.clinic_id = p_clinic_id and pt.erased_at is null
          ) e;
    end if;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_origin, p_clinic_id, v_actor, 'queue_worker', 'retention.report', 'retention', null,
            jsonb_build_object('checkpoint_threads_due', v_threads, 'patients_past_period', v_past, 'patients_no_encounter', v_none, 'by', 'schedule',
                               'not_assessed', jsonb_build_array('minor_record', 'pregnancy_record'), 'prompt_response_bodies', 'not_stored', 'deleted', 0),
            'sys:retention-report:' || p_clinic_id::text || ':' || to_char(clock_timestamp() at time zone 'utc', 'YYYY-MM-DD'))
    on conflict do nothing;
    get diagnostics v_made = row_count;
    return query select v_threads, v_past, v_none, v_made = 1;
end
$$;

revoke all on function ops.retention_report_as_worker(uuid, timestamptz, timestamptz) from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function ops.retention_report_as_worker(uuid, timestamptz, timestamptz) from %I', r);
        end if;
    end loop;
end $$;
grant execute on function ops.retention_report_as_worker(uuid, timestamptz, timestamptz) to careflow_app;

do $$
declare r text; f text := 'ops.retention_report_as_worker(uuid,timestamptz,timestamptz)';
begin
    if not (select rolsuper or rolbypassrls from pg_roles where rolname = current_user) then
        raise exception '0071: the migrating role must bypass row-level security';
    end if;
    if has_function_privilege('public', f, 'execute') or not has_function_privilege('careflow_app', f, 'execute') then
        raise exception '0071: % must be executable by the application login and not by PUBLIC', f;
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) and has_function_privilege(r, f, 'execute') then
            raise exception '0071: % must not be executable by %', f, r;
        end if;
    end loop;
    if not exists (select 1 from pg_proc p where p.oid = f::regprocedure and p.prosecdef and p.proconfig @> array['search_path=""']) then
        raise exception '0071: % must be SECURITY DEFINER with an empty search_path', f;
    end if;
end $$;
