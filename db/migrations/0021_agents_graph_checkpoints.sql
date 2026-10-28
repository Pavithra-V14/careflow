-- 0021_agents_graph_checkpoints: durable state for the agent graphs (BUILD-SPEC.md §7.2, §17.1 `agents`; CLAUDE.md rules 5, 6, 8, 10). Package P2-8 (checkpointer).
--
-- These three tables back careflow/checkpointer.py, our own asyncpg implementation of LangGraph's BaseCheckpointSaver (owner decision of 2026-10-24: no psycopg, no
-- tables created outside the checksummed ledger). "Every step checkpoints to Postgres, keyed by a stable thread_id. A crashed or restarted run resumes from its last
-- completed step" (§7.2).
--
--   * agents.graph_checkpoints        one row per checkpoint: the serialized checkpoint (without channel values) and its metadata, the parent checkpoint id (the lineage).
--   * agents.graph_checkpoint_blobs   the channel values, one row per (channel, version): immutable once written.
--   * agents.graph_checkpoint_writes  the pending writes of a step (a node's output before the step completes, and the special error / interrupt / resume entries).
--
-- APPEND-ONLY WHERE THE SPEC REQUIRES IT. The checkpoint HISTORY is the lineage the spec relies on (§7.2: "the lineage benefits ... are already delivered by the checkpoint
-- history"), so graph_checkpoints and graph_checkpoint_blobs are append-only for everyone, the owner included (trigger): no UPDATE, no DELETE, no TRUNCATE.
-- graph_checkpoint_writes is operational state: LangGraph REPLACES the special entries (negative idx: error, scheduled, interrupt, resume), so UPDATE is allowed, but only
-- of those rows (policy) and only of the four value columns (column grant); DELETE and TRUNCATE are blocked. Because nothing can be deleted, LangGraph's `delete_thread`
-- is refused by the saver: erasure of a thread (DPDP) is a separate, audited procedure that does not exist yet and is recorded as an open item.
--
-- Rule 5: every row carries an idempotency key (`cp:` / `blob:` / `w:` plus the natural key); the primary keys make a replayed put a no-op.
-- Rule 8: forced RLS from this migration, origin tag with no default, clinic_id on every row and in every primary key. Who: the queue worker (the system identity that runs the
-- graphs), a nurse or a doctor (a reviewer's resume writes a step) may INSERT and SELECT, in their own clinic. Nobody else (front desk, admin, business owner, auditor,
-- patient, another clinic's staff) can read or write a checkpoint.
--
-- WHAT IS IN A ROW: the Front Door graph keeps only ids, fixed-word statuses and (card id, version) pairs in its state (careflow/front_door.py), never letter text or an
-- extracted value, and a test greps the stored bytes. The saver itself is GENERIC: a future graph whose state held patient text would put it here in the clear, so such a graph
-- must pass an encrypting serializer (langgraph's EncryptedSerializer) to the saver BEFORE it runs. This migration cannot enforce that.
--
-- "Append-only for everyone, the owner included" is a TRIGGER guarantee: the owner or a superuser could still DISABLE the triggers or set session_replication_role.
-- RESIDUAL RISKS, stated: (1) the checkpoint bytes are msgpack, not inspected by the database: it cannot verify they hold no patient text. (2) A queue worker, nurse or doctor of
-- the clinic can write any thread_id of that clinic, including another run's: the thread id is not tied to a document or run by a foreign key (it is `front_door:<document id>`
-- by convention), so a compromised worker could poison a thread. (3) `checkpoint_id` orders by `collate "C"` (LangGraph ids are time-ordered uuid6 strings, compared as text).
-- (4) No audit.log row is written. (5) Rows accumulate: there is no pruning (deletion is blocked); a retention procedure is a later, audited package. (6) CROSS-CLINIC: a queue worker holds its role in each clinic it serves, so row-level security alone would let it see two clinics' rows under the
-- same thread id; the saver therefore filters EVERY read by the clinic in the run config, but a nurse of clinic A who knew clinic B's thread id could still WRITE a row under it in clinic A
-- (harmless to B's reads, which are clinic-scoped). (7) LangGraph copies every string in the run config's `configurable` into the stored metadata, in the clear: put nothing sensitive there.
-- (8) Atomicity of a put rests on the saver doing both inserts in one transaction; it is exercised against the real tables inside one rolled-back transaction, not across a commit.

create table agents.graph_checkpoints (
    clinic_id            uuid not null references iam.clinics (id),
    origin               text not null check (origin in ('live', 'test', 'eval', 'canary')),
    thread_id            text not null check (length(thread_id) between 1 and 300),
    checkpoint_ns        text not null check (length(checkpoint_ns) <= 300),
    checkpoint_id        text collate "C" not null check (length(checkpoint_id) between 1 and 100),
    parent_checkpoint_id text collate "C" check (parent_checkpoint_id is null or length(parent_checkpoint_id) between 1 and 100),
    checkpoint_type      text not null check (length(checkpoint_type) between 1 and 50),
    checkpoint           bytea not null check (length(checkpoint) <= 8388608),
    metadata_type        text not null check (length(metadata_type) between 1 and 50),
    metadata             bytea not null check (length(metadata) <= 1048576),
    created_at           timestamptz not null default clock_timestamp(),
    idempotency_key      text not null check (length(idempotency_key) > 0),
    primary key (clinic_id, thread_id, checkpoint_ns, checkpoint_id),
    constraint graph_checkpoints_key_per_clinic unique (clinic_id, idempotency_key)
);

create index graph_checkpoints_thread_idx on agents.graph_checkpoints (clinic_id, thread_id, checkpoint_ns, checkpoint_id desc);

create table agents.graph_checkpoint_blobs (
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    thread_id       text not null check (length(thread_id) between 1 and 300),
    checkpoint_ns   text not null check (length(checkpoint_ns) <= 300),
    channel         text not null check (length(channel) between 1 and 300),
    version         text not null check (length(version) between 1 and 100),
    blob_type       text not null check (length(blob_type) between 1 and 50),
    blob            bytea not null check (length(blob) <= 8388608),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) > 0),
    primary key (clinic_id, thread_id, checkpoint_ns, channel, version),
    constraint graph_checkpoint_blobs_key_per_clinic unique (clinic_id, idempotency_key)
);

create table agents.graph_checkpoint_writes (
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    thread_id       text not null check (length(thread_id) between 1 and 300),
    checkpoint_ns   text not null check (length(checkpoint_ns) <= 300),
    checkpoint_id   text collate "C" not null check (length(checkpoint_id) between 1 and 100),
    task_id         text not null check (length(task_id) between 1 and 300),
    idx             integer not null,
    channel         text not null check (length(channel) between 1 and 300),
    write_type      text not null check (length(write_type) between 1 and 50),
    write_blob      bytea not null check (length(write_blob) <= 8388608),
    task_path       text not null default '' check (length(task_path) <= 1000),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) > 0),
    primary key (clinic_id, thread_id, checkpoint_ns, checkpoint_id, task_id, idx),
    constraint graph_checkpoint_writes_key_per_clinic unique (clinic_id, idempotency_key)
);

create trigger graph_checkpoints_no_update_delete before update or delete on agents.graph_checkpoints
    for each row execute function agents.append_only();
create trigger graph_checkpoints_no_truncate before truncate on agents.graph_checkpoints
    for each statement execute function agents.append_only();
create trigger graph_checkpoint_blobs_no_update_delete before update or delete on agents.graph_checkpoint_blobs
    for each row execute function agents.append_only();
create trigger graph_checkpoint_blobs_no_truncate before truncate on agents.graph_checkpoint_blobs
    for each statement execute function agents.append_only();
-- A REGULAR write (idx >= 0) is never changed by anyone, the owner included; only the special entries (idx < 0) can be replaced.
create trigger graph_checkpoint_writes_regular_are_final before update on agents.graph_checkpoint_writes
    for each row when (old.idx >= 0) execute function agents.append_only();
create trigger graph_checkpoint_writes_no_delete before delete on agents.graph_checkpoint_writes
    for each row execute function agents.append_only();
create trigger graph_checkpoint_writes_no_truncate before truncate on agents.graph_checkpoint_writes
    for each statement execute function agents.append_only();

alter table agents.graph_checkpoints enable row level security;
alter table agents.graph_checkpoints force row level security;
alter table agents.graph_checkpoint_blobs enable row level security;
alter table agents.graph_checkpoint_blobs force row level security;
alter table agents.graph_checkpoint_writes enable row level security;
alter table agents.graph_checkpoint_writes force row level security;

create policy graph_checkpoints_insert on agents.graph_checkpoints for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy graph_checkpoints_select on agents.graph_checkpoints for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy graph_checkpoint_blobs_insert on agents.graph_checkpoint_blobs for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy graph_checkpoint_blobs_select on agents.graph_checkpoint_blobs for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy graph_checkpoint_writes_insert on agents.graph_checkpoint_writes for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy graph_checkpoint_writes_select on agents.graph_checkpoint_writes for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
-- Only the special entries (error, scheduled, interrupt, resume: negative idx) are ever replaced.
create policy graph_checkpoint_writes_update on agents.graph_checkpoint_writes for update to careflow_app
    using (idx < 0 and iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']))
    with check (idx < 0 and iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));

revoke all on agents.graph_checkpoints, agents.graph_checkpoint_blobs, agents.graph_checkpoint_writes from public;
grant select, insert on agents.graph_checkpoints, agents.graph_checkpoint_blobs, agents.graph_checkpoint_writes to careflow_app;
grant update (channel, write_type, write_blob, task_path) on agents.graph_checkpoint_writes to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on agents.graph_checkpoints, agents.graph_checkpoint_blobs, agents.graph_checkpoint_writes from %I', r);
        end if;
    end loop;
end $$;

comment on table agents.graph_checkpoints is 'LangGraph checkpoints (lineage). Append-only. Ids and fixed-word state only for the Front Door; see the migration header. BUILD-SPEC 7.2.';
comment on table agents.graph_checkpoint_blobs is 'LangGraph channel values, one per (channel, version). Append-only.';
comment on table agents.graph_checkpoint_writes is 'LangGraph pending writes of a step. Delete-proof; only the special (negative idx) entries may be replaced.';
