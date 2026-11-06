-- 0048_autonomy_setting_care_gap_producers_sampler (owner decisions of 2026-11-06, item C; BUILD-SPEC.md 9.3, 12.1, 12.5, 12.6, 24; CLAUDE.md rules 5, 8, 9, 10). Fix forward from 0044: no merged migration is edited.
--
-- (1) ops.autonomy_changes: the clinic's autonomy mode as an APPEND-ONLY history. The current mode is the newest row; NO ROW MEANS `shadow` (the most human mode). Only an ADMIN of the clinic with a verified second
--     factor (checked again by a restrictive policy on iam.aal2_session()) may insert, as themselves, and only `shadow` or `assisted`: switching to `autonomous` is the two-person act of 12.5 and is NOT built, so the
--     check constraint refuses it here. Front desk, nurse, doctor, admin and the queue worker may READ the current mode (the runners need it).
-- (2) Care-gap producers (24): nurse and doctor of the clinic, with a verified second factor, may CREATE an episode, attach a recurrence rule to it and record that a rule was done (`last_done_on`, which can only move
--     forward and never into the future, by a guard trigger). The rule DEFINITIONS are clinician-authored required settings; the application inserts a rule row from the setting when first needed. A rule is unique per
--     (clinic, rule_code). Nothing else on these tables is writable. A nurse or doctor may also mark a review sample as re-checked, as themselves (the time is the database's).
-- (3) The sampler (12.6): the queue worker may INSERT a review sample (a reference to a decision of the same clinic: the composite key from 0047 pins it; the origin must be the clinic's own) and READ the decisions
--     it samples from (it already can). One sample per decision (existing unique).
-- (4) ops.care_gap_rules_for_worker(clinic): the NARROW worker read the owner asked for, in place of a table policy: for each open episode rule of the clinic it returns ONLY the rule's id, last-done date, cycle
--     numbers and today's date, no patient id, no code word. The overdue alert is raised from these rows by the worker's tick. Callable by a queue worker of that clinic only.

-- A small helper for policies: the origin of a clinic, readable by any caller (a policy subquery on iam.clinics runs under the calling role's own row-level security, and the queue worker may not read that
-- table). It returns one word for a clinic id and nothing else; it pins the origin tag of staff and worker inserts to the clinic's own (a nurse cannot tag live rows as 'test' to hide them).
create function ops.clinic_origin(p_clinic_id uuid) returns text
    language sql stable security definer set search_path = ''
as $$ select origin from iam.clinics where id = p_clinic_id $$;
revoke all on function ops.clinic_origin(uuid) from public;
grant execute on function ops.clinic_origin(uuid) to careflow_app;

-- (1)
create table ops.autonomy_changes (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    mode            text not null check (mode in ('shadow', 'assisted')),
    changed_by      uuid not null references iam.accounts (id),
    changed_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint autonomy_changes_key_per_clinic unique (clinic_id, idempotency_key)
);
create index autonomy_changes_current_idx on ops.autonomy_changes (clinic_id, changed_at desc);

create function ops.autonomy_changes_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'ops.autonomy_changes is append-only: % is not permitted', tg_op using errcode = 'restrict_violation';
end
$$;
revoke all on function ops.autonomy_changes_guard() from public;
create trigger autonomy_changes_no_update_delete before update or delete on ops.autonomy_changes for each row execute function ops.autonomy_changes_guard();
create trigger autonomy_changes_no_truncate before truncate on ops.autonomy_changes for each statement execute function ops.autonomy_changes_guard();

alter table ops.autonomy_changes enable row level security;
alter table ops.autonomy_changes force row level security;
create policy autonomy_select on ops.autonomy_changes for select to careflow_app using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']));
create policy autonomy_insert on ops.autonomy_changes for insert to careflow_app with check (changed_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['admin']));
create policy autonomy_aal2 on ops.autonomy_changes as restrictive for insert to careflow_app with check (iam.aal2_session());
revoke all on ops.autonomy_changes from public;
grant select on ops.autonomy_changes to careflow_app;
grant insert (clinic_id, origin, mode, changed_by, idempotency_key) on ops.autonomy_changes to careflow_app;

-- (2)
alter table ops.care_recurrence_rules add constraint care_rules_code_per_clinic unique (clinic_id, rule_code);
alter table ops.care_recurrence_rules add constraint care_rules_sane_numbers check ((interval_days is null or interval_days <= 3650) and overdue_after_days <= 3650);
-- who recorded the last-done date (taken from the verified claims: the application role cannot call auth.uid() by name). Every recording is ALSO an audit row written by the route.
alter table ops.care_episode_rules add column last_done_by uuid references iam.accounts (id);

create function ops.care_episode_rules_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_episode_rules rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if new.id <> old.id or new.clinic_id <> old.clinic_id or new.origin <> old.origin or new.episode_id <> old.episode_id or new.rule_id <> old.rule_id
       or new.created_at <> old.created_at or new.idempotency_key <> old.idempotency_key then
        raise exception 'only the last-done date of an episode rule can change' using errcode = 'restrict_violation';
    end if;
    if new.last_done_on is null or new.last_done_on > current_date or (old.last_done_on is not null and new.last_done_on < old.last_done_on) then
        raise exception 'the last-done date can only move forward and never into the future' using errcode = 'restrict_violation';
    end if;
    new.last_done_by := nullif(nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub', '')::uuid;
    return new;
end
$$;
revoke all on function ops.care_episode_rules_guard() from public;
create trigger care_episode_rules_guard before update or delete on ops.care_episode_rules for each row execute function ops.care_episode_rules_guard();
create trigger care_episode_rules_no_truncate before truncate on ops.care_episode_rules for each statement execute function ops.care_episode_rules_guard();

create policy care_rules_insert on ops.care_recurrence_rules for insert to careflow_app with check (origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_rules_aal2 on ops.care_recurrence_rules as restrictive for insert to careflow_app with check (iam.aal2_session());
create policy care_episodes_insert on ops.care_episodes for insert to careflow_app with check (origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create trigger care_episodes_not_erased before insert on ops.care_episodes for each row execute function ops.care_barriers_not_erased();  -- the 0047 guard (it reads patient_id and clinic_id, which both tables have)
create policy care_episodes_aal2 on ops.care_episodes as restrictive for insert to careflow_app with check (iam.aal2_session());
create policy care_episode_rules_insert on ops.care_episode_rules for insert to careflow_app with check (origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_episode_rules_update on ops.care_episode_rules for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])) with check (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_episode_rules_aal2_insert on ops.care_episode_rules as restrictive for insert to careflow_app with check (iam.aal2_session());
create policy care_episode_rules_aal2_update on ops.care_episode_rules as restrictive for update to careflow_app using (iam.aal2_session()) with check (iam.aal2_session());

grant insert (clinic_id, origin, rule_code, interval_days, annual_month, overdue_after_days, idempotency_key) on ops.care_recurrence_rules to careflow_app;
grant insert (clinic_id, origin, patient_id, episode_code, started_on, idempotency_key) on ops.care_episodes to careflow_app;
grant insert (clinic_id, origin, episode_id, rule_id, last_done_on, idempotency_key) on ops.care_episode_rules to careflow_app;
grant update (last_done_on) on ops.care_episode_rules to careflow_app;

-- a sample is re-checked once, by a nurse or doctor as themselves, the time taken from the database clock
create function ops.care_review_samples_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op <> 'UPDATE' then
        raise exception 'ops.care_review_samples rows are never deleted' using errcode = 'restrict_violation';
    end if;
    if old.rechecked_at is not null then
        raise exception 'a re-checked sample is final' using errcode = 'restrict_violation';
    end if;
    if new.id <> old.id or new.clinic_id <> old.clinic_id or new.origin <> old.origin or new.decision_id <> old.decision_id or new.sampled_at <> old.sampled_at
       or new.idempotency_key <> old.idempotency_key or new.rechecked_by is null then
        raise exception 'a sample can only be re-checked' using errcode = 'restrict_violation';
    end if;
    new.rechecked_at := clock_timestamp();
    return new;
end
$$;
revoke all on function ops.care_review_samples_guard() from public;
create trigger care_review_samples_guard before update or delete on ops.care_review_samples for each row execute function ops.care_review_samples_guard();
create trigger care_review_samples_no_truncate before truncate on ops.care_review_samples for each statement execute function ops.care_review_samples_guard();

create policy care_samples_recheck on ops.care_review_samples for update to careflow_app
    using (rechecked_at is null and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']))
    with check (rechecked_by = auth.uid() and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy care_samples_aal2_update on ops.care_review_samples as restrictive for update to careflow_app using (iam.aal2_session()) with check (iam.aal2_session());
grant update (rechecked_by) on ops.care_review_samples to careflow_app;

-- (3)
create policy care_samples_insert_worker on ops.care_review_samples for insert to careflow_app
    with check (rechecked_by is null and rechecked_at is null and iam.has_clinic_role(clinic_id, array['queue_worker'])
                and origin = ops.clinic_origin(clinic_id));
grant insert (clinic_id, origin, decision_id, idempotency_key) on ops.care_review_samples to careflow_app;
-- the worker also needs to READ samples to see which decisions are already sampled (it never sees more than the nurse does)
drop policy care_samples_select on ops.care_review_samples;
create policy care_samples_select on ops.care_review_samples for select to careflow_app using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor', 'admin', 'queue_worker']));

-- (4)
create function ops.care_gap_rules_for_worker(p_clinic_id uuid) returns table (episode_rule_id uuid, last_done_on date, interval_days integer, annual_month smallint, overdue_after_days integer, today date)
    language plpgsql security definer set search_path = ''
as $$
begin
    if p_clinic_id is null or auth.uid() is null or not iam.has_clinic_role(p_clinic_id, array['queue_worker']) then
        raise exception 'care gap read refused' using errcode = 'insufficient_privilege';
    end if;
    return query
        select er.id, er.last_done_on, r.interval_days, r.annual_month, r.overdue_after_days, current_date
          from ops.care_episode_rules er
          join ops.care_recurrence_rules r on r.id = er.rule_id and r.clinic_id = er.clinic_id
          join ops.care_episodes e on e.id = er.episode_id and e.clinic_id = er.clinic_id
         where er.clinic_id = p_clinic_id and e.closed_on is null
         order by er.id limit 2000;
end
$$;
revoke all on function ops.care_gap_rules_for_worker(uuid) from public;
grant execute on function ops.care_gap_rules_for_worker(uuid) to careflow_app;

do $$
declare
    r text;
    t text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.autonomy_changes from %I', r);
            execute format('revoke all on function ops.care_gap_rules_for_worker(uuid) from %I', r);
            execute format('revoke all on function ops.clinic_origin(uuid) from %I', r);
            execute format('revoke all on function ops.autonomy_changes_guard() from %I', r);
            execute format('revoke all on function ops.care_episode_rules_guard() from %I', r);
            execute format('revoke all on function ops.care_review_samples_guard() from %I', r);
        end if;
    end loop;
    foreach t in array array['ops.autonomy_changes_guard()', 'ops.care_episode_rules_guard()', 'ops.care_review_samples_guard()', 'ops.care_gap_rules_for_worker(uuid)', 'ops.clinic_origin(uuid)'] loop
        if has_function_privilege('public', t, 'execute') then
            raise exception '0048: % must not be executable by PUBLIC', t;
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'ops.autonomy_changes', 'update') or has_table_privilege('careflow_app', 'ops.autonomy_changes', 'delete') then
        raise exception '0048: autonomy_changes must be append-only for the application role';
    end if;
    foreach t in array array['ops.care_recurrence_rules', 'ops.care_episodes', 'ops.care_episode_rules', 'ops.care_review_samples', 'ops.autonomy_changes'] loop
        if has_table_privilege('careflow_app', t, 'delete') or has_table_privilege('careflow_app', t, 'truncate') then
            raise exception '0048: the application role must not delete from %', t;
        end if;
    end loop;
    if has_column_privilege('careflow_app', 'ops.care_episode_rules', 'rule_id', 'update') or has_column_privilege('careflow_app', 'ops.care_episodes', 'closed_on', 'update') then
        raise exception '0048: only last_done_on may be updatable on episode rules and nothing on episodes';
    end if;
end $$;
