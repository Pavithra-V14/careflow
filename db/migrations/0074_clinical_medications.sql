-- 0074_clinical_medications (owner instruction 2026-11-13; BUILD-SPEC.md 9.5; CLAUDE.md rules 1, 3, 5, 6, 8, 10).
--
-- # CLINICAL-PLACEHOLDER: needs clinician sign-off  (the medication vocabulary, and anything about dose, route or frequency)
--
-- MECHANISM ONLY. clinical.medications is a BI-TEMPORAL, APPEND-ONLY statement that a patient is on a medication, shaped like clinical.results (0050): valid_from / valid_to say when it held in the world,
-- recorded_at says when the system learned it, and a correction or a stop is a NEW row that names the one it supersedes (a row is superseded at most once; never an update or delete, for anyone, the owner
-- included; the guards are ENABLE ALWAYS). `verified` is explicit with no default: an unverified statement satisfies no rule (rule 3).
--
-- What is NOT here, on purpose: no dose, route, frequency, indication, brand or free text. Those are clinical content and need a clinician's design (and, as free text about a person, encryption).
-- A row is a CODE, a verified flag and times. Unlike a result (whose numbers erasure wipes), the CODE is the payload here, so the audited erasure DELETES a patient's rows (see the end of this file).
-- The allowed codes are the clinic's required setting CAREFLOW_MEDICATION_CODES (no default; unset = the route answers 503); the database holds only a shape check.
-- Nurse and doctor of the clinic read and write, as themselves, with a verified second factor re-checked by a restrictive policy. A record for an erased patient is refused.
-- NEW GRANTS (ledger): SELECT, and INSERT on the listed columns, to careflow_app on clinical.medications. Nothing else.

create table clinical.medications (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    patient_id      uuid not null,
    code            text not null check (code ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$'),
    verified        boolean not null,
    valid_from      timestamptz not null,
    valid_to        timestamptz,
    recorded_at     timestamptz not null default clock_timestamp(),
    recorded_by     uuid not null references iam.accounts (id),
    supersedes_id   uuid,
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint medications_key_per_clinic unique (clinic_id, idempotency_key),
    constraint medications_id_clinic_patient unique (id, clinic_id, patient_id),
    constraint medications_superseded_once unique (supersedes_id),
    constraint medications_valid_range check (valid_to is null or valid_to > valid_from),
    foreign key (patient_id, clinic_id) references mpi.patients (id, clinic_id),
    foreign key (supersedes_id, clinic_id, patient_id) references clinical.medications (id, clinic_id, patient_id)  -- a correction names a row of the SAME patient
);
create index medications_patient_idx on clinical.medications (clinic_id, patient_id, valid_from desc);

create function clinical.medications_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    if tg_op = 'DELETE' and exists (select 1 from mpi.patients p where p.id = old.patient_id and p.clinic_id = old.clinic_id and p.erased_at is not null) then
        return old;  -- the patient is erased: their medication statements go with them (the audited erasure deletes them; see below)
    end if;
    raise exception 'clinical.medications is append-only: % is not permitted (a correction or a stop is a new row that supersedes the old one)', tg_op using errcode = 'restrict_violation';
end
$$;
revoke all on function clinical.medications_guard() from public;
create trigger medications_no_update_delete before update or delete on clinical.medications for each row execute function clinical.medications_guard();
create trigger medications_no_truncate before truncate on clinical.medications for each statement execute function clinical.medications_guard();
alter table clinical.medications enable always trigger medications_no_update_delete;
alter table clinical.medications enable always trigger medications_no_truncate;
create trigger medications_not_erased before insert on clinical.medications for each row execute function ops.care_barriers_not_erased();
alter table clinical.medications enable always trigger medications_not_erased;

alter table clinical.medications enable row level security;
alter table clinical.medications force row level security;
create policy medications_select on clinical.medications for select to careflow_app using (iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy medications_insert on clinical.medications for insert to careflow_app
    with check (recorded_by = auth.uid() and origin = ops.clinic_origin(clinic_id) and iam.has_clinic_role(clinic_id, array['nurse', 'doctor']));
create policy medications_aal2 on clinical.medications as restrictive for insert to careflow_app with check (iam.aal2_session());
create policy medications_aal2_read on clinical.medications as restrictive for select to careflow_app using (iam.aal2_session());
revoke all on clinical.medications from public;
grant select on clinical.medications to careflow_app;
grant insert (clinic_id, origin, patient_id, code, verified, valid_from, valid_to, recorded_by, supersedes_id, idempotency_key) on clinical.medications to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on clinical.medications from %I', r);
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'clinical.medications', 'update') or has_table_privilege('careflow_app', 'clinical.medications', 'delete') or has_any_column_privilege('careflow_app', 'clinical.medications', 'update') then
        raise exception '0074: medications are append-only for the application role';
    end if;
end $$;
comment on table clinical.medications is 'Append-only, bi-temporal medication statements (a code, a verified flag, times). No dose or free text. Mechanism only: the vocabulary is the clinic''s required setting.';

-- ERASURE (reviewer finding F1): a medication CODE is the payload here (there is no dose or text), so the audited two-person erasure must remove a patient's rows. The guard above allows a DELETE only for a patient who is
-- already erased (the 0067 pattern); every other change, every truncate and every delete for a live patient stays refused for everyone, the owner included. The approval function is patched IN PLACE from its stored definition
-- (it carries the second-factor check of 0065 and the 0067 deletes, which a re-issue from older text would lose); the patch asserts it changed things. A one-time backfill removes the rows of patients erased before this migration.
do $$
declare f regprocedure := 'mpi.approve_patient_erasure(uuid,text)'::regprocedure; d text; n text;
begin
    d := pg_get_functiondef(f);
    n := replace(d, E'    update mpi.erasure_requests set status = ''erased''',
                    E'    -- the medication statements of the patient (0074): the guard allows the delete now that the patient row is erased\n'
                    || E'    delete from clinical.medications where patient_id = v_req.patient_id and clinic_id = v_req.clinic_id;\n'
                    || E'    update mpi.erasure_requests set status = ''erased''');
    if n = d then raise exception '0074: the request-update anchor was not found'; end if;
    execute n;
end $$;
delete from clinical.medications m using mpi.patients p where m.patient_id = p.id and m.clinic_id = p.clinic_id and p.erased_at is not null;

do $$
begin
    if pg_get_functiondef('mpi.approve_patient_erasure(uuid,text)'::regprocedure) not like '%delete from clinical.medications%'
       or pg_get_functiondef('mpi.approve_patient_erasure(uuid,text)'::regprocedure) not like '%iam.aal2_session()%'
       or pg_get_functiondef('mpi.approve_patient_erasure(uuid,text)'::regprocedure) not like '%ops.visit_facts%' then
        raise exception '0074: the erasure function must delete medications and keep its earlier checks and deletes';
    end if;
end $$;
