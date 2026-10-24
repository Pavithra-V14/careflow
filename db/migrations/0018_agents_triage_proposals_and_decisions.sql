-- 0018_agents_triage_proposals_and_decisions: the triage PROPOSAL the Front Door writes, and the HUMAN decision on it (BUILD-SPEC.md §12.1, §12.3, §12.6;
-- CLAUDE.md rules 5, 6, 8, 9, 10). Package P2-9.
--
-- * agents.triage_proposals: what the graph proposes for one referral document: the patient it resolved to, the protocol-card candidates as
--   (card id, version) pairs (ids only, never card content or patient text), whether retrieval abstained, the approval mode in force, and the review task
--   a human must act on. APPEND-ONLY for everyone, the owner included (trigger). It carries NO triage priority and NO clinical judgment: a priority is the
--   card's or the clinician's, and no column here can hold one (priorities are blocked on clinicians).
-- * agents.review_decisions: the decision on a proposal. EVERY row is a HUMAN decision: `decided_by` is NOT NULL and an insert policy requires it to be the
--   signed-in account, a nurse or doctor of the clinic. There is no automatic or timeout path: "nothing clinical is ever approved by timeout" (§12.3) and
--   "auto-approved outputs never become training labels" (§12.6, rule 9) hold structurally, because the only rows that can exist are human ones. The
--   assisted-mode auto-approval of one confident low-risk card needs a calibrated threshold and clinician-authored card attributes that do not exist yet;
--   when it does, a LATER migration adds a distinct, labelled source for it. One decision per proposal (UNIQUE). APPEND-ONLY.
-- * Both tables: forced RLS from this migration, origin tag with no default (§0), idempotency key unique PER CLINIC, composite foreign keys that pin the
--   clinic (and, for the patient and the task, the origin), no UPDATE or DELETE privilege, no TRUNCATE.
-- * Who: the proposal is inserted by the queue worker (the graph runner) or a nurse or doctor; read by nurse, doctor and queue worker (front desk and admin do
--   not see case-level triage, as 0013 hides message-sourced review tasks from them). A decision is inserted by a nurse or doctor only, as themselves.
--
-- Also: agents.review_tasks gets a unique (id, clinic_id) so these tables can pin the clinic with a composite key. (0013 already added (id, clinic_id, origin).)
--
-- RESIDUAL RISKS, stated: (1) the database cannot check that a proposal's candidate ids name real cards (no card table exists yet); the retriever's
-- version-checked join is the guard. (2) The decision insert and the review-task resolve are two statements in the caller's transaction; a nurse who
-- resolves a task by another path leaves a proposal with no decision (visible: the proposal has no review_decisions row). (3) No audit.log row is written here.

create table agents.triage_proposals (
    id              uuid primary key,
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    document_id     uuid not null,
    patient_id      uuid not null,
    candidates      jsonb not null check (jsonb_typeof(candidates) = 'array' and jsonb_array_length(candidates) <= 20),
    abstained       boolean not null,
    mode            text not null check (mode in ('shadow', 'assisted', 'autonomous')),
    review_task_id  uuid not null,
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) > 0),
    constraint triage_proposals_key_per_clinic unique (clinic_id, idempotency_key),
    constraint triage_proposals_id_clinic_key unique (id, clinic_id),
    constraint triage_proposals_one_per_document unique (clinic_id, document_id),
    constraint triage_proposals_abstained_has_no_candidates check (not abstained or jsonb_array_length(candidates) = 0),
    foreign key (document_id, clinic_id) references docs.documents (id, clinic_id),
    foreign key (patient_id, clinic_id, origin) references mpi.patients (id, clinic_id, origin),
    foreign key (review_task_id, clinic_id, origin) references agents.review_tasks (id, clinic_id, origin)
);

alter table agents.review_tasks add constraint review_tasks_id_clinic_key unique (id, clinic_id);

create table agents.review_decisions (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    proposal_id     uuid not null,
    decision        text not null check (decision in ('approve', 'edit', 'reject')),
    decided_by      uuid not null references iam.accounts (id),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) > 0),
    constraint review_decisions_key_per_clinic unique (clinic_id, idempotency_key),
    constraint review_decisions_one_per_proposal unique (proposal_id),
    foreign key (proposal_id, clinic_id) references agents.triage_proposals (id, clinic_id)
);

create function agents.append_only() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception '% is append-only: % is not permitted', tg_table_name, tg_op using errcode = 'restrict_violation';
end
$$;

create trigger triage_proposals_no_update_delete before update or delete on agents.triage_proposals
    for each row execute function agents.append_only();
create trigger triage_proposals_no_truncate before truncate on agents.triage_proposals
    for each statement execute function agents.append_only();
create trigger review_decisions_no_update_delete before update or delete on agents.review_decisions
    for each row execute function agents.append_only();
create trigger review_decisions_no_truncate before truncate on agents.review_decisions
    for each statement execute function agents.append_only();

alter table agents.triage_proposals enable row level security;
alter table agents.triage_proposals force row level security;
alter table agents.review_decisions enable row level security;
alter table agents.review_decisions force row level security;

create policy triage_proposals_insert on agents.triage_proposals for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy triage_proposals_select on agents.triage_proposals for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));
create policy review_decisions_insert on agents.review_decisions for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']) and decided_by = auth.uid());
create policy review_decisions_select on agents.review_decisions for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['queue_worker', 'nurse', 'doctor']));

revoke all on agents.triage_proposals from public;
revoke all on agents.review_decisions from public;
grant select on agents.triage_proposals to careflow_app;
grant select on agents.review_decisions to careflow_app;
-- Column-level INSERT: created_at always comes from the database; review_decisions.id from its default.
grant insert (id, clinic_id, origin, document_id, patient_id, candidates, abstained, mode, review_task_id, idempotency_key)
    on agents.triage_proposals to careflow_app;
grant insert (clinic_id, origin, proposal_id, decision, decided_by, idempotency_key) on agents.review_decisions to careflow_app;

revoke all on function agents.append_only() from public;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on agents.triage_proposals, agents.review_decisions from %I', r);
            execute format('revoke all on function agents.append_only() from %I', r);
        end if;
    end loop;
end $$;

comment on table agents.triage_proposals is
    'What the Front Door proposes for one referral: ids only, no priority, no clinical judgment. Append-only. BUILD-SPEC 12.';
comment on table agents.review_decisions is
    'The HUMAN decision on a proposal. decided_by is NOT NULL: no automatic or timeout row can exist (12.3, 12.6). Append-only.';
