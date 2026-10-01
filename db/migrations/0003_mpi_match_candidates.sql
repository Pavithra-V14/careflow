-- 0003_mpi_match_candidates: the human-review ledger for possible duplicate patients (§17.1, §17.2).
--
-- When ingestion finds a near-match it creates a NEW patient and records the pair here for a person to
-- decide. Nothing in this table merges anything: a merge is a separate, human-approved act (§12.1) that
-- is not built yet. Rows are never deleted; review closes them.
--
-- RLS: only front desk of the clinic reads or writes (identity review is a human front-desk task,
-- §12.1). Nurse, doctor, admin, auditor and business owner get nothing. careflow_app: select, insert,
-- update; no delete.

create table mpi.match_candidates (
    id                   uuid primary key default gen_random_uuid(),
    clinic_id            uuid not null,
    -- The record just created from the incoming demographics, and the existing record it resembles.
    patient_id           uuid not null,
    candidate_patient_id uuid not null,
    -- Which identity tokens agreed (e.g. {phone} or {name_dob}); never the values themselves.
    matched_kinds        text[] not null check (cardinality(matched_kinds) > 0),
    -- 'near_match': some but not all of the strong identifiers agreed.
    -- 'ambiguous': the incoming person matched confidently, but more than one existing record qualified.
    reason               text not null check (reason in ('near_match', 'ambiguous')),
    status               text not null default 'pending' check (status in ('pending', 'resolved')),
    resolved_at          timestamptz,
    resolved_by          uuid references iam.accounts (id),
    origin               text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at           timestamptz not null default now(),
    idempotency_key      text not null unique check (length(idempotency_key) > 0),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    foreign key (candidate_patient_id, clinic_id) references mpi.patients (id, clinic_id),
    check (patient_id <> candidate_patient_id),
    check ((status = 'resolved') = (resolved_at is not null)),
    unique (patient_id, candidate_patient_id)
);

create index match_candidates_pending_idx on mpi.match_candidates (clinic_id, created_at) where status = 'pending';

alter table mpi.match_candidates enable row level security;
alter table mpi.match_candidates force row level security;

create policy match_candidates_select_front_desk on mpi.match_candidates for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy match_candidates_insert_front_desk on mpi.match_candidates for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));
create policy match_candidates_update_front_desk on mpi.match_candidates for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk']));

revoke all on mpi.match_candidates from public;
grant select, insert, update on mpi.match_candidates to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on mpi.match_candidates from %I', r);
        end if;
    end loop;
end $$;
