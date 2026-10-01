-- 0006_outbox_and_queue: the transactional outbox and the Postgres-backed queue (BUILD-SPEC.md §7.3, §7.4,
-- §17.1 `ops`; CLAUDE.md rules 5, 6, 8).
--
-- * ops.outbox: an event is written in the SAME transaction as the business change that caused it, so it is
--   never lost and never emitted for a change that rolled back. A relay later moves it into the queue.
-- * ops.queue: jobs with a visibility timeout, an attempt counter and a dead-letter state.
--   Delivery is exclusive at any moment (FOR UPDATE SKIP LOCKED) and at-least-once across crashes: a job
--   whose worker died becomes visible again when its timeout passes. So handlers MUST be idempotent (§7.2).
-- * The 5-attempt ceiling (§7.3) is enforced by the engine too (CHECK attempt_count <= 5), not just by code.
-- * Payloads carry identifiers and small facts, NEVER patient text or demographics (rules 6 and 7); that is
--   a rule for writers, with only a size cap enforced here.
-- * Identity: workers are ordinary iam accounts holding the new `queue_worker` role in each clinic they serve,
--   so the same auth.uid() RLS applies and a worker for clinic A can never touch clinic B's jobs. This adds a
--   role to the iam model (below). Who mints the claims for a worker process is NOT settled here; §18.4's
--   short-lived per-job signed tokens are the intended mechanism and belong to the phase that builds workers.
-- * careflow_app: select, insert, update (update only on the columns a worker legitimately changes). No delete.

-- ---- iam: one new role --------------------------------------------------------------------------

alter table iam.staff_roles drop constraint staff_roles_role_check;
alter table iam.staff_roles add constraint staff_roles_role_check
    check (role in ('front_desk', 'nurse', 'doctor', 'admin', 'business_owner', 'auditor', 'queue_worker'));

-- ---- ops.queue ----------------------------------------------------------------------------------

create table ops.queue (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    -- Origin tag (§0): no default, so test jobs can never be silently tagged `live`.
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    task_type       text not null check (task_type ~ '^[a-z][a-z0-9_.]*$'),
    payload         jsonb not null check (jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 65536),
    state           text not null default 'unclaimed' check (state in ('unclaimed', 'claimed', 'completed', 'dead_lettered')),
    -- Incremented on every claim. The ceiling is part of the table, not only of the worker code.
    attempt_count   integer not null default 0 check (attempt_count between 0 and 5),
    -- A claimed job is hidden until this time; if its worker has not finished by then it is claimable again.
    -- clock_timestamp(), not now(): now() is the transaction's start and would stall a long transaction.
    visible_after   timestamptz not null default clock_timestamp(),
    claimed_at      timestamptz,
    claimed_by      text check (claimed_by is null or length(claimed_by) between 1 and 200),
    completed_at    timestamptz,
    dead_lettered_at timestamptz,
    -- The failure's traceback or message, as text. Capped; never put patient text in it.
    last_error      text check (last_error is null or length(last_error) <= 8000),
    created_at      timestamptz not null default clock_timestamp(),
    -- Rule 5: a retried enqueue cannot create a second job.
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    check (state <> 'claimed' or (claimed_at is not null and claimed_by is not null)),
    check (state <> 'completed' or completed_at is not null),
    check (state <> 'dead_lettered' or (dead_lettered_at is not null and last_error is not null)),
    check (state <> 'unclaimed' or (attempt_count = 0 and claimed_by is null))
);

-- The claim query's path: eligible jobs in arrival order.
create index queue_eligible_idx on ops.queue (created_at, id) where state in ('unclaimed', 'claimed');
create index queue_clinic_state_idx on ops.queue (clinic_id, state);

-- ---- ops.outbox ---------------------------------------------------------------------------------

create table ops.outbox (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    event_type      text not null check (event_type ~ '^[a-z][a-z0-9_.]*$'),
    payload         jsonb not null check (jsonb_typeof(payload) = 'object' and octet_length(payload::text) <= 65536),
    created_at      timestamptz not null default clock_timestamp(),
    -- Set by the relay, in the transaction that inserts the job: the event has become exactly one job.
    dispatched_at   timestamptz,
    job_id          uuid references ops.queue (id),
    idempotency_key text not null unique check (length(idempotency_key) > 0),
    check ((dispatched_at is null) = (job_id is null))
);

create index outbox_pending_idx on ops.outbox (created_at, id) where dispatched_at is null;

-- ---- Row-level security -------------------------------------------------------------------------

alter table ops.queue enable row level security;
alter table ops.queue force row level security;
alter table ops.outbox enable row level security;
alter table ops.outbox force row level security;

-- Producers are the staff roles that cause business events; a worker is a queue_worker of the clinic.
-- Nobody else (auditor, business owner, patients, another clinic) sees or writes anything.
create policy queue_select on ops.queue for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']));
create policy queue_insert on ops.queue for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']));
-- Only a worker claims, completes or dead-letters.
create policy queue_update_worker on ops.queue for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker']))
    with check (iam.has_clinic_role(clinic_id, array['queue_worker']));

create policy outbox_select on ops.outbox for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin', 'queue_worker']));
create policy outbox_insert on ops.outbox for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor', 'admin']));
-- Only the relay (a worker) marks an event dispatched.
create policy outbox_update_worker on ops.outbox for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker']))
    with check (iam.has_clinic_role(clinic_id, array['queue_worker']));

-- ---- Privileges ---------------------------------------------------------------------------------

revoke all on ops.queue, ops.outbox from public;
grant usage on schema ops to careflow_app;
grant select, insert on ops.queue, ops.outbox to careflow_app;
-- Column-level UPDATE: a worker may change a job's lifecycle columns and nothing that defines the job.
grant update (state, attempt_count, visible_after, claimed_at, claimed_by, completed_at, dead_lettered_at, last_error)
    on ops.queue to careflow_app;
grant update (dispatched_at, job_id) on ops.outbox to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.queue, ops.outbox from %I', r);
            execute format('revoke all on schema ops from %I', r);
        end if;
    end loop;
end $$;
