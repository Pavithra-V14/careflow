-- 0008_mpi_merge_ledger: logical patient merges, never destructive (BUILD-SPEC.md §12.1, §12.5, §17.1, §17.2;
-- CLAUDE.md rules 5 and 8).
--
-- * A merge is a ROW that links a subordinate record to a surviving master. No patient row is changed or
--   deleted, and no ciphertext is touched; routing is derived from this table (the single source of truth,
--   so there is no second copy of "who points where" to drift).
-- * §12.5 makes patient merges a TWO-PERSON action, and §12.1 locks them to a human. So a merge is proposed
--   by one front-desk person (`approved_by`) and only becomes `active` when a DIFFERENT front-desk person
--   confirms it (`second_approved_by`). The database refuses an active merge with fewer than two distinct
--   named people, and RLS makes each approver the person actually signed in (no naming someone else).
-- * A subordinate can be in at most one LIVE link (proposed or active) at a time, a record can never link to
--   itself, and links are flat: a master cannot itself be a subordinate, and a subordinate cannot be a master.
-- * Unmerge breaks the link by marking it `unmerged` (the row stays, forever). It is one person, not two:
--   undoing a possibly wrong merge must be quick. The engine raises a human review task for every decision
--   made inside the link window (§17.2).
-- * careflow_app: select, insert, update on the lifecycle columns only. No delete, anywhere.

create table mpi.merges (
    id                        uuid primary key default gen_random_uuid(),
    clinic_id                 uuid not null,
    master_patient_id         uuid not null,   -- the survivor
    subordinate_patient_id    uuid not null,   -- the record linked to it
    -- The review-ledger row (0003) that put this pair in front of a person.
    candidate_id              uuid not null references mpi.match_candidates (id),
    status                    text not null default 'awaiting_second_approval'
                                  check (status in ('awaiting_second_approval', 'active', 'unmerged')),
    approved_by               uuid not null references iam.accounts (id),
    second_approved_by        uuid references iam.accounts (id),
    activated_at              timestamptz,
    unmerged_at               timestamptz,
    unmerged_by               uuid references iam.accounts (id),
    -- Origin tag (§0): no default, so a test merge can never be silently tagged `live`.
    origin                    text not null check (origin in ('live', 'test', 'eval', 'canary')),
    created_at                timestamptz not null default clock_timestamp(),
    -- Rule 5: every step carries its own key, unique across the table.
    idempotency_key           text not null unique check (length(idempotency_key) > 0),
    approve_idempotency_key   text unique check (approve_idempotency_key is null or length(approve_idempotency_key) > 0),
    unmerge_idempotency_key   text unique check (unmerge_idempotency_key is null or length(unmerge_idempotency_key) > 0),

    foreign key (master_patient_id, clinic_id) references mpi.patients (id, clinic_id),
    foreign key (subordinate_patient_id, clinic_id) references mpi.patients (id, clinic_id),

    -- A record can never be its own master.
    constraint merges_not_self check (master_patient_id <> subordinate_patient_id),
    -- Two DIFFERENT people (§12.5), recorded by name.
    constraint merges_two_distinct_people check (second_approved_by is null or second_approved_by <> approved_by),
    constraint merges_awaiting_shape check (status <> 'awaiting_second_approval'
        or (second_approved_by is null and activated_at is null and unmerged_at is null)),
    constraint merges_active_needs_two_people check (status <> 'active'
        or (second_approved_by is not null and activated_at is not null and unmerged_at is null)),
    constraint merges_unmerged_shape check (status <> 'unmerged'
        or (unmerged_at is not null and unmerged_by is not null and unmerge_idempotency_key is not null))
);

-- A subordinate is in at most one live link, ever: the iron-clad uniqueness the merge model rests on.
create unique index merges_one_live_link_per_subordinate
    on mpi.merges (subordinate_patient_id) where status in ('awaiting_second_approval', 'active');
-- And a pair cannot be linked twice in opposite directions.
create unique index merges_one_live_link_per_pair
    on mpi.merges (least(master_patient_id, subordinate_patient_id), greatest(master_patient_id, subordinate_patient_id))
    where status in ('awaiting_second_approval', 'active');
create index merges_master_idx on mpi.merges (master_patient_id) where status in ('awaiting_second_approval', 'active');

-- Rules the unique indexes cannot express: both records share the merge's origin and are live (not erased),
-- and links stay flat (no chains). Runs as the caller, so RLS applies: a caller who cannot read the patients
-- cannot insert the merge in the first place.
create function mpi.check_merge_links() returns trigger
    language plpgsql set search_path = ''
as $$
declare
    bad_origin integer;
    erased integer;
begin
    select count(*) filter (where origin <> new.origin), count(*) filter (where erased_at is not null)
      into bad_origin, erased
      from mpi.patients where id in (new.master_patient_id, new.subordinate_patient_id);
    if bad_origin > 0 then
        raise exception 'both patients must have the same origin as the merge' using errcode = 'check_violation';
    end if;
    if erased > 0 then
        raise exception 'an erased patient record cannot be merged' using errcode = 'check_violation';
    end if;
    if exists (select 1 from mpi.merges m
               where m.status in ('awaiting_second_approval', 'active')
                 and (m.subordinate_patient_id = new.master_patient_id or m.master_patient_id = new.subordinate_patient_id)) then
        raise exception 'merge links must stay flat: the master is already a subordinate, or the subordinate is already a master'
            using errcode = 'check_violation';
    end if;
    return new;
end
$$;

create trigger merges_check_links before insert on mpi.merges
    for each row execute function mpi.check_merge_links();

-- ---- Row-level security -------------------------------------------------------------------------

alter table mpi.merges enable row level security;
alter table mpi.merges force row level security;

-- Clinical staff read the links (they need to route a record to its survivor); nobody else does.
create policy merges_select on mpi.merges for select to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk', 'nurse', 'doctor']));
-- Only front desk proposes, and only as themselves (no naming another person as the approver).
create policy merges_insert_front_desk on mpi.merges for insert to careflow_app
    with check (iam.has_clinic_role(clinic_id, array['front_desk']) and approved_by = auth.uid()
                and status = 'awaiting_second_approval');
-- Only front desk confirms or unmerges, and each signature must be the signed-in person's own.
create policy merges_update_front_desk on mpi.merges for update to careflow_app
    using (iam.has_clinic_role(clinic_id, array['front_desk']))
    with check (iam.has_clinic_role(clinic_id, array['front_desk'])
                and (second_approved_by is null or second_approved_by = auth.uid())
                and (unmerged_by is null or unmerged_by = auth.uid()));

revoke all on mpi.merges from public;
grant select, insert on mpi.merges to careflow_app;
-- Column-level UPDATE: who is linked to whom, the clinic, the origin and the proposer are immutable.
grant update (status, second_approved_by, activated_at, unmerged_at, unmerged_by, approve_idempotency_key, unmerge_idempotency_key)
    on mpi.merges to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on mpi.merges from %I', r);
        end if;
    end loop;
end $$;
