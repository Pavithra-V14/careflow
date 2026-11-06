-- 0040_reviewer_stats_function: per-reviewer approval rate and time-to-decide, for the admin only (Phase 4; BUILD-SPEC.md 12.4; CLAUDE.md rules 6, 8, 10).
--
-- Catching rubber-stamping needs, per reviewer, how often they approve and how fast. Admin has no SELECT on agents.review_decisions (only the queue worker, nurse and doctor do), and none is added: this
-- SECURITY DEFINER function returns AGGREGATES ONLY (counts and a median), one row per reviewer account, never a proposal, a patient or a task.
--
--   agents.reviewer_stats(p_clinic_id uuid, p_since timestamptz) returns table (reviewer uuid, decisions int, approvals int, edits int, rejections int, canaries_seen int, canaries_approved int, median_seconds numeric)
--
--   * the caller must hold admin in THE CLINIC NAMED (the one fixed refusal otherwise; it reveals nothing about the clinic);
--   * only origins `live` and `canary` count (test and eval decisions are never mixed into a live figure, 13.6); canary decisions are counted in their own two columns, not in `decisions`;
--   * time-to-decide is the decision time minus when the task was acknowledged (the reviewer opened it), else when the proposal was created; the median is of seconds;
--   * the window is bounded: p_since may not be older than 400 days, so the call cannot be used as an unbounded scan.
-- median_seconds is NULL for a reviewer with only canary decisions (show "no data", not 0); an acknowledgement made by another account or after the decision can only make the time shorter, never negative (floored at 0).
-- Execute: careflow_app only. The reviewer id is the real account id (the admin needs it to talk to the reviewer); the learning-loop export pseudonymizes it separately (12.6).

create function agents.reviewer_stats(p_clinic_id uuid, p_since timestamptz)
    returns table (reviewer uuid, decisions integer, approvals integer, edits integer, rejections integer, canaries_seen integer, canaries_approved integer, median_seconds numeric)
    language plpgsql security definer set search_path = ''
as $$
begin
    if auth.uid() is null or p_clinic_id is null or p_since is null or not iam.has_clinic_role(p_clinic_id, array['admin'])
       or p_since < clock_timestamp() - interval '400 days' then
        raise exception 'reviewer statistics refused' using errcode = 'insufficient_privilege';
    end if;
    return query
    select d.decided_by,
           (count(*) filter (where d.origin = 'live'))::integer,
           (count(*) filter (where d.origin = 'live' and d.decision = 'approve'))::integer,
           (count(*) filter (where d.origin = 'live' and d.decision = 'edit'))::integer,
           (count(*) filter (where d.origin = 'live' and d.decision = 'reject'))::integer,
           (count(*) filter (where d.origin = 'canary'))::integer,
           (count(*) filter (where d.origin = 'canary' and d.decision = 'approve'))::integer,
           (percentile_cont(0.5) within group (order by greatest(extract(epoch from (d.created_at - coalesce(t.acknowledged_at, p.created_at))), 0)) filter (where d.origin = 'live'))::numeric
      from agents.review_decisions d
      join agents.triage_proposals p on p.id = d.proposal_id and p.clinic_id = d.clinic_id
      join agents.review_tasks t on t.id = p.review_task_id and t.clinic_id = p.clinic_id
     where d.clinic_id = p_clinic_id and d.origin in ('live', 'canary') and d.created_at >= p_since
     group by d.decided_by
     order by d.decided_by;
end
$$;

revoke all on function agents.reviewer_stats(uuid, timestamptz) from public;
grant execute on function agents.reviewer_stats(uuid, timestamptz) to careflow_app;

do $$
declare r text;
begin
    if not exists (select 1 from pg_roles where rolname = current_user and (rolsuper or rolbypassrls)) then
        raise exception '0040: the migrating role must bypass row-level security, or this function would silently return nothing';
    end if;
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function agents.reviewer_stats(uuid, timestamptz) from %I', r);
        end if;
    end loop;
    if has_function_privilege('public', 'agents.reviewer_stats(uuid, timestamptz)', 'execute') or not has_function_privilege('careflow_app', 'agents.reviewer_stats(uuid, timestamptz)', 'execute') then
        raise exception '0040: execute must be granted to the application role only';
    end if;
    if exists (select 1 from pg_roles x where x.rolname in ('anon', 'authenticated', 'service_role') and has_function_privilege(x.rolname, 'agents.reviewer_stats(uuid, timestamptz)', 'execute')) then
        raise exception '0040: anon, authenticated and service_role must not execute the function';
    end if;
end $$;
