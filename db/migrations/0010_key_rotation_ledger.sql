-- 0010_key_rotation_ledger: the record of each batch of a manual key rotation (BUILD-SPEC.md §17.2; CLAUDE.md
-- rules 5, 6, 8).
--
-- A rotation (careflow/rotation.py, scripts/rotate_keys.py) re-encrypts demographics and re-derives identity
-- tokens in batches. The writes to mpi.patients and mpi.identity_tokens are idempotent by construction (a row
-- is only picked up while it still carries the old key_version; a token's natural key is unique), but rule 5
-- wants every write to carry a key, so each batch also records ONE row here whose idempotency_key is derived
-- from (from_version, to_version, clinic, origin, first patient id, last patient id) and is unique. The row is
-- written in the same savepoint as the batch it describes, so it exists if and only if the batch committed.
--
-- * Operational state only (rule 6): ids and counts, never a demographic, a ciphertext or a token.
-- * Scoped to a clinic and an origin, so the same forced RLS as every clinic table applies: the application
--   role may read and write a row only as front desk of that clinic (the role that owns demographics).
--   The maintenance script itself runs under the migrator credential, which bypasses RLS by design.
-- * careflow_app: select and insert. No update, no delete: a ledger row is never edited.

create table ops.key_rotation_batches (
    id               uuid primary key default gen_random_uuid(),
    clinic_id        uuid not null references iam.clinics (id),
    origin           text not null check (origin in ('live', 'test', 'eval', 'canary')),
    from_version     smallint not null check (from_version > 0),
    to_version       smallint not null check (to_version > 0 and to_version <> from_version),
    patients_rotated integer not null check (patients_rotated > 0),
    tokens_written   integer not null check (tokens_written >= 0),
    first_patient_id uuid not null,
    last_patient_id  uuid not null,
    created_at       timestamptz not null default clock_timestamp(),
    idempotency_key  text not null unique check (length(idempotency_key) > 0)
);

create index key_rotation_batches_clinic_idx on ops.key_rotation_batches (clinic_id, from_version, to_version);

alter table ops.key_rotation_batches enable row level security;
alter table ops.key_rotation_batches force row level security;

create policy key_rotation_batches_select on ops.key_rotation_batches for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy key_rotation_batches_insert on ops.key_rotation_batches for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

revoke all on ops.key_rotation_batches from public;
grant select, insert on ops.key_rotation_batches to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.key_rotation_batches from %I', r);
        end if;
    end loop;
end $$;
