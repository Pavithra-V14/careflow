-- 0014_pin_clinic_on_child_foreign_keys: three foreign keys that did not pin the clinic now do (BUILD-SPEC.md §17.1, §18.4, §21; CLAUDE.md
-- rule 8, rule 10: fix forward).
--
-- Found at the Phase 1 close-out (tests/test_rls_child_tables_live.py, docs/design/phase1-status.md): every clinic-scoped table carries its
-- own clinic_id, and almost every child foreign key is composite (child_id, clinic_id) -> parent (id, clinic_id), so a child row cannot
-- point at another clinic's parent. Three were single-column, so the database accepted a child that pointed at another clinic's row:
--
--   sched.bookings.hold_id   -> sched.holds (id)
--   mpi.merges.candidate_id  -> mpi.match_candidates (id)
--   ops.outbox.job_id        -> ops.queue (id)
--
-- An integrity gap, not a read leak (the managers never write it, and RLS still hides the other clinic's rows), but a foreign-key probe
-- also tells a caller whether a guessed uuid exists in another clinic, so it is closed at the database. Each gets a matching UNIQUE
-- (id, clinic_id) on the parent (the same device 0004 used for sched.slots) and a composite foreign key.
--
-- ORIGIN is deliberately NOT part of these keys. The sibling foreign keys in sched, mpi and ops (slots, patients, visit types...) pin the
-- clinic only; origin is pinned in the msg tables (0011, 0013), which is where that pattern lives. The writers keep a child's origin equal
-- to its parent's (the relay copies an event's origin to its job; a booking and its hold both take the slot's), but no constraint here
-- depends on it.
--
-- Safe on a valid database: every new foreign key is preceded by a check that no existing row violates it. If one does, the migration
-- stops with a message naming the table and the count (never a row), instead of failing half-applied; the whole file runs in the
-- migrator's single transaction, so a failure leaves nothing changed. MATCH SIMPLE applies, so a NULL hold_id or job_id (no reference)
-- is unconstrained, exactly as before.
--
-- Nothing changes for row-level security: no policy, grant or table is added or altered, only constraints and the unique indexes that back
-- them. The application role gains no privilege.

-- Fail fast instead of queueing behind a long application transaction (and making new application queries queue behind this ALTER):
-- if a lock is not granted within five seconds the migration stops, rolls back whole, and can simply be re-run in a quieter moment.
set local lock_timeout = '5s';

-- ---- parents: the target of a composite key must be unique on exactly those columns --------------------------------------------------

alter table sched.holds add constraint holds_id_clinic_key unique (id, clinic_id);
alter table mpi.match_candidates add constraint match_candidates_id_clinic_key unique (id, clinic_id);
alter table ops.queue add constraint queue_id_clinic_key unique (id, clinic_id);

-- ---- refuse to proceed on data that would violate the new keys ---------------------------------------------------------------------

do $$
declare
    bad_bookings integer;
    bad_merges   integer;
    bad_outbox   integer;
begin
    select count(*) into bad_bookings
      from sched.bookings b join sched.holds h on h.id = b.hold_id
     where h.clinic_id <> b.clinic_id;
    select count(*) into bad_merges
      from mpi.merges m join mpi.match_candidates c on c.id = m.candidate_id
     where c.clinic_id <> m.clinic_id;
    select count(*) into bad_outbox
      from ops.outbox o join ops.queue q on q.id = o.job_id
     where q.clinic_id <> o.clinic_id;
    if bad_bookings + bad_merges + bad_outbox > 0 then
        raise exception '0014 cannot add the composite foreign keys: % booking(s), % merge(s), % outbox event(s) point at another clinic''s row. Fix the data deliberately, then re-run.',
            bad_bookings, bad_merges, bad_outbox using errcode = 'check_violation';
    end if;
end $$;

-- ---- children: replace each single-column key with the composite one ---------------------------------------------------------------

alter table sched.bookings drop constraint bookings_hold_id_fkey;
alter table sched.bookings add constraint bookings_hold_id_clinic_id_fkey
    foreign key (hold_id, clinic_id) references sched.holds (id, clinic_id);

alter table mpi.merges drop constraint merges_candidate_id_fkey;
alter table mpi.merges add constraint merges_candidate_id_clinic_id_fkey
    foreign key (candidate_id, clinic_id) references mpi.match_candidates (id, clinic_id);

alter table ops.outbox drop constraint outbox_job_id_fkey;
alter table ops.outbox add constraint outbox_job_id_clinic_id_fkey
    foreign key (job_id, clinic_id) references ops.queue (id, clinic_id);
