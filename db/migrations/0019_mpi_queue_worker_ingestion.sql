-- 0019_mpi_queue_worker_ingestion: let the queue worker (the system identity that runs the Front Door graph) register a patient from a verified referral
-- (BUILD-SPEC.md §17.2, §12.1; CLAUDE.md rules 6, 8, 10). Owner decision of 2026-10-24: the Front Door graph runs as the `queue_worker` system identity and
-- humans decide through review tasks.
--
-- WHAT IS ADDED, and nothing else:
--   * INSERT policies for queue_worker on mpi.patients, mpi.identity_tokens and mpi.match_candidates (a new record, its tokens, and the possible-duplicate rows).
--   * SELECT policies for queue_worker on the same three tables and on mpi.merges. The owner asked for INSERT-only; ingestion cannot be INSERT-only, because it
--     MATCHES before it creates: it looks the tokens up (identity_tokens joined to patients and to active merges), and its replay check reads the record it made
--     (patients, identity_tokens, match_candidates). Without those reads every referral would create a duplicate. The reads cover the worker's own clinic only. They are the
--     rows clinical staff already see on patients, identity_tokens and merges (0002/0008); on match_candidates they are BROADER than any clinical role (0003 gives
--     that table to front desk alone).
--   * Nothing else: NO UPDATE and NO DELETE policy (and no DELETE privilege exists), so the worker cannot correct, erase, re-bind or move a record, and cannot
--     confirm, propose or break a merge. MERGES STAY HUMAN AND TWO-PERSON (0008/0009 are untouched: the worker has no INSERT or UPDATE policy on mpi.merges).
--     No grant is added: the table privileges for careflow_app already exist (0002, 0003, 0008, and the column-level ones of 0012 that keep account_id out of
--     reach of INSERT and UPDATE), and row-level security is what decides.
--
-- OUTCOMES (unchanged code, mpi.PatientIngestionPipeline): one confident match links to the existing record and writes nothing; no match creates a record; a
-- near-match creates a new record AND a possible-duplicate row for a human to review, never a merge (§17.2). The worker cannot RESOLVE that review: its insert
-- policy on match_candidates requires status = 'pending' with no resolved_at and no resolved_by (0003 grants INSERT on every column, so without that check a
-- worker could write an already-resolved row and close the review without a person), and it has no UPDATE policy; only front desk changes the status or merges.
--
-- RESIDUAL RISKS, stated. (1) The worker can read the clinic's patient rows, which carry demographic CIPHERTEXT (name, date of birth, phone). It also holds the
-- vault key in its process, so it can decrypt them: it is a trusted system identity, and a compromise of the worker is a compromise of that clinic's demographics.
-- A narrower design (a SECURITY DEFINER matching function that returns only ids) was not built; say if you want it. (2) Anything that can obtain a queue_worker
-- token for a clinic can register patients there. The HTTP route POST /v1/staff/patients still requires front desk in code (and a worker cannot even see iam.clinics),
-- so the worker's reach is through the graph, not that route. (3) A worker-created record is indistinguishable in the table from a front-desk-created one; the
-- origin tag and the idempotency key (`referral:<document id>`) are the provenance. No audit.log row is written here. (4) INSERT carries a re-binding
-- capability a predicate cannot close: the foreign keys pin only the clinic, so a worker can attach an identity token (any kind) to ANY existing patient of its
-- clinic, or forge a pending possible-duplicate between two patients; a token attached to the wrong patient would make a later referral link to the wrong person.
-- Closing it needs a SECURITY DEFINER ingestion function that only touches the record it just created; not built (the worker is a trusted identity). (5) A
-- worker that also holds front_desk in the same clinic gets the union of both roles' policies (permissive policies OR). (6) The worker chooses `origin` and `id`
-- on the rows it inserts, like front desk does.

create policy patients_select_worker on mpi.patients for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy patients_insert_worker on mpi.patients for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker']));

create policy tokens_select_worker on mpi.identity_tokens for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy tokens_insert_worker on mpi.identity_tokens for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker']));

create policy match_candidates_select_worker on mpi.match_candidates for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker']));
create policy match_candidates_insert_worker on mpi.match_candidates for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker']) and status = 'pending' and resolved_at is null and resolved_by is null);

create policy merges_select_worker on mpi.merges for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker']));
