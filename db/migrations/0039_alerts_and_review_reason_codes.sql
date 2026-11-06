-- 0039_alerts_and_review_reason_codes: Phase 4 (BUILD-SPEC.md 12.2, 12.3, 12.4, 13.3, 13.4; CLAUDE.md rules 5, 6, 8, 10).
--
-- PART A. ops.alerts: the in-app alert read model (13.4). One row is "a class of thing needs attention", aimed at a ROLE of a clinic. The row holds a closed class word, a count and ONE opaque reference
-- (a task id, a decision id): never a name, a diagnosis or a value, so the same row can be echoed to Slack or e-mail (which are third parties) as class + count + opaque id + sign-in link and nothing else.
--   * Who writes: the queue worker (the in-app watchdog, 13.3, runs there), and a nurse or doctor ONLY for the one silent canary alert (12.4): class `canary_approved`, aimed at admin, and only when the
--     reference names a decision that THE SAME ACCOUNT recorded as an approval of a canary proposal. A reviewer therefore cannot raise any other alert, or one for someone else's decision.
--   * Who reads: the role the alert is aimed at, in that clinic (`audience_role`). A canary alert is aimed at admin only, so the reviewer who approved the canary can never read it (12.4: "the reviewer
--     is never told"); the insert path for the reviewer needs no SELECT (no RETURNING, ON CONFLICT DO NOTHING).
--   * Who changes: the audience, once, to acknowledge (acknowledged_by = themselves, acknowledged_at set from null): column grant + policy. Nothing is ever deleted (no DELETE grant, a trigger blocks it).
--   * Rule 5: a key, unique per clinic, so a watchdog tick that runs twice raises an alert once (the key carries the class, the reference and the escalation level).
--   * Rule 8: forced RLS, origin tag with no default.
--
-- PART B. agents.review_decisions.reason_code (12.2: a PICKLIST of reason codes, not free text). A nullable text column that the database checks only for SHAPE (lower-case words); the picklist itself is a
-- required setting of the clinic (clinical content, rule 1) and is enforced by the application. The optional free-text note is NOT stored (it could hold patient text; where it would live is an open decision).
-- The column joins the application role INSERT grant of 0018; the table stays append-only.
--
-- RESIDUALS. (1) A queue worker can raise any class of alert for any role of its clinic (it is the trusted watchdog); spam to admin is possible from a compromised worker, as it is for every worker write.
-- (2) The reason code is trusted as to which picklist it came from.

create table ops.alerts (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    class           text not null check (class in ('red_flag_overdue', 'task_overdue', 'task_escalated', 'dead_letters', 'queue_age', 'canary_approved', 'spend_runaway', 'delivery_failed', 'fallback_spike', 'tracing_cap')),
    audience_role   text not null check (audience_role in ('nurse', 'doctor', 'admin', 'front_desk')),
    alert_count     integer not null check (alert_count between 1 and 1000000),
    ref             text check (ref is null or length(ref) between 1 and 100),
    created_at      timestamptz not null default clock_timestamp(),
    acknowledged_by uuid references iam.accounts (id),
    acknowledged_at timestamptz,
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint alerts_key_per_clinic unique (clinic_id, idempotency_key),
    constraint alerts_ack_pair check ((acknowledged_by is null) = (acknowledged_at is null)),
    constraint alerts_canary_is_for_admin check (class <> 'canary_approved' or (audience_role = 'admin' and ref is not null))
);

create index alerts_open_idx on ops.alerts (clinic_id, audience_role, created_at) where acknowledged_at is null;

create function ops.alerts_no_delete() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'ops.alerts rows are never deleted' using errcode = 'restrict_violation';
end
$$;

create trigger alerts_no_delete before delete on ops.alerts for each row execute function ops.alerts_no_delete();
create trigger alerts_no_truncate before truncate on ops.alerts for each statement execute function ops.alerts_no_delete();

-- Once acknowledged, an alert is final; nothing but the acknowledgement pair may ever change.
create function ops.alerts_ack_once() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if old.acknowledged_at is not null then
        raise exception 'an acknowledged alert is final' using errcode = 'restrict_violation';
    end if;
    new.acknowledged_at := clock_timestamp();  -- the time is the database's, never the caller's
    return new;
end
$$;

create trigger alerts_ack_once before update on ops.alerts for each row execute function ops.alerts_ack_once();

alter table ops.alerts enable row level security;
alter table ops.alerts force row level security;

create policy alerts_insert_worker on ops.alerts for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy alerts_insert_canary_approval on ops.alerts for insert to careflow_app
    with check (
        class = 'canary_approved' and audience_role = 'admin' and alert_count = 1 and idempotency_key = 'canary-approved:' || ref
        and iam.has_clinic_role(clinic_id, array['nurse', 'doctor'])
        and exists (
            select 1 from agents.review_decisions d
            where d.id::text = alerts.ref and d.clinic_id = alerts.clinic_id and d.origin = 'canary' and d.origin = alerts.origin
              and d.decision = 'approve' and d.decided_by = auth.uid()
        )
    );
create policy alerts_select_audience on ops.alerts for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array[audience_role]));
create policy alerts_acknowledge on ops.alerts for update to careflow_app
    using (acknowledged_at is null and iam.has_clinic_role(clinic_id, array[audience_role]))
    with check (acknowledged_by = auth.uid() and acknowledged_at is not null and iam.has_clinic_role(clinic_id, array[audience_role]));

revoke all on ops.alerts from public;
grant select on ops.alerts to careflow_app;
grant insert (clinic_id, origin, class, audience_role, alert_count, ref, idempotency_key) on ops.alerts to careflow_app;
grant update (acknowledged_by, acknowledged_at) on ops.alerts to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.alerts from %I', r);
        end if;
    end loop;
end $$;

comment on table ops.alerts is 'In-app alerts (13.4): a closed class, a count and one opaque reference, aimed at a role of a clinic. Never a name, diagnosis or value. Acknowledge once; never deleted.';

-- PART B
alter table agents.review_decisions add column reason_code text check (reason_code is null or (reason_code ~ '^[a-z][a-z0-9_]*$' and length(reason_code) <= 60));
grant insert (reason_code) on agents.review_decisions to careflow_app;

do $$
begin
    if (select count(*) from pg_policies where schemaname = 'ops' and tablename = 'alerts') <> 4 then
        raise exception '0039: ops.alerts must have exactly four policies';
    end if;
    if has_table_privilege('careflow_app', 'ops.alerts', 'delete') or has_table_privilege('public', 'ops.alerts', 'select') then
        raise exception '0039: ops.alerts has a grant it must not have';
    end if;
end $$;
