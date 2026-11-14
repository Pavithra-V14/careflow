-- 0089_patient_visit_prep (owner instruction 2026-11-14, deferred item 30; BUILD-SPEC.md 22; CLAUDE.md rules 1, 3, 5, 6, 8, 10). MECHANISM ONLY, OFF unless the owner sets CAREFLOW_PATIENT_VISIT_PREP=on.
--
-- A patient sees what to prepare for their own upcoming visit (a booking) and marks each item done or not done. The ITEMS ARE CLINICIAN CONTENT in the required setting CAREFLOW_VISIT_READINESS_CONTENT (the same checklist
-- the Visit Readiness agent uses; nothing is seeded and nothing is invented here). This migration stores only the patient's own MARKS: a closed item id, done or not, who and when. No text, no patient words.
--   * ops.visit_prep_marks   append-only; the NEWEST mark per (visit, item) is the state. RLS enabled and FORCED, NO policy, NO table grant: everything goes through the definer functions below.
--   * sched.patient_visit_prep(booking)   the caller's OWN confirmed booking only (one 42501 for every other case): the visit type NAME (so the application can pick that visit type's checklist from the setting),
--                                         when it starts, and the newest mark of each item.
--   * sched.patient_mark_prep(booking, item, done, key)   a bound, active patient marks an item of their own confirmed FUTURE booking. Idempotent on a key namespaced by the account; an audit row (`sys:` key).
--   * sched.staff_visit_prep(booking)   the nurse or doctor of the clinic, with a verified second factor (aal2), reads the same marks (read-only).
-- The patient functions check the patient's own sign-in (a phone code, no staff second factor); the staff function requires aal2.
--
-- NEW GRANTS (ledger): EXECUTE on the three functions to careflow_app. New table: no grant, no policy.

create table ops.visit_prep_marks (
    id              uuid primary key default gen_random_uuid(),
    clinic_id       uuid not null references iam.clinics (id),
    origin          text not null check (origin in ('live', 'test', 'eval', 'canary')),
    visit_id        uuid not null,
    patient_id      uuid not null,
    item            text not null check (item ~ '^[a-z][a-z0-9_]{0,59}$'),
    done            boolean not null,
    marked_by       uuid not null references iam.accounts (id),
    created_at      timestamptz not null default clock_timestamp(),
    idempotency_key text not null check (length(idempotency_key) between 1 and 300),
    constraint visit_prep_marks_key_per_clinic unique (clinic_id, idempotency_key),
    foreign key (visit_id, clinic_id, patient_id) references sched.bookings (id, clinic_id, patient_id)
);
create index visit_prep_marks_visit_idx on ops.visit_prep_marks (visit_id, item, created_at desc);

alter table ops.visit_prep_marks enable row level security;
alter table ops.visit_prep_marks force row level security;
revoke all on ops.visit_prep_marks from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role', 'careflow_app'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on ops.visit_prep_marks from %I', r);
        end if;
    end loop;
end $$;

create function ops.visit_prep_marks_guard() returns trigger
    language plpgsql set search_path = ''
as $$
begin
    raise exception 'ops.visit_prep_marks is append-only' using errcode = 'restrict_violation';
end
$$;
create trigger visit_prep_marks_no_change before update or delete on ops.visit_prep_marks for each row execute function ops.visit_prep_marks_guard();
create trigger visit_prep_marks_no_truncate before truncate on ops.visit_prep_marks for each statement execute function ops.visit_prep_marks_guard();
alter table ops.visit_prep_marks enable always trigger visit_prep_marks_no_change;
alter table ops.visit_prep_marks enable always trigger visit_prep_marks_no_truncate;

-- ---- the patient's own visit and its marks ----------------------------------------------------------------------------------------------------------

create function sched.patient_visit_prep(p_booking_id uuid)
    returns table (o_visit_type text, o_starts_at timestamptz, o_item text, o_done boolean, o_marked_at timestamptz)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_b     record;
begin
    if v_actor is null or p_booking_id is null then
        raise exception 'visit refused' using errcode = '42501';
    end if;
    select bk.id, bk.clinic_id, vt.name as vt_name, s.starts_at into v_b
      from sched.bookings bk
      join mpi.patients pt on pt.id = bk.patient_id and pt.clinic_id = bk.clinic_id and pt.erased_at is null
      join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
      join sched.slots s on s.id = bk.slot_id and s.clinic_id = bk.clinic_id
      join sched.visit_types vt on vt.id = s.visit_type_id and vt.clinic_id = s.clinic_id
     where bk.id = p_booking_id and pt.account_id = v_actor and bk.status = 'confirmed';
    if not found then
        raise exception 'visit refused' using errcode = '42501';  -- missing, someone else's, cancelled and erased are one answer
    end if;
    return query
        select v_b.vt_name, v_b.starts_at, m.item, m.done, m.created_at
          from ops.visit_prep_marks m
         where m.visit_id = p_booking_id
           and not exists (select 1 from ops.visit_prep_marks n where n.visit_id = m.visit_id and n.item = m.item and (n.created_at, n.id) > (m.created_at, m.id))
         order by m.item;
    if not found then
        return query select v_b.vt_name, v_b.starts_at, null::text, null::boolean, null::timestamptz;  -- the visit exists and nothing is marked yet: one row with no item
    end if;
end
$$;

create function sched.patient_mark_prep(p_booking_id uuid, p_item text, p_done boolean, p_idempotency_key text)
    returns table (o_mark_id uuid, o_created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
    v_b     record;
    v_key   text;
    v_prior record;
    v_id    uuid;
begin
    if v_actor is null or p_booking_id is null or p_item is null or p_done is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200
       or p_item !~ '^[a-z][a-z0-9_]{0,59}$' then
        raise exception 'mark refused' using errcode = '42501';
    end if;
    select bk.id, bk.clinic_id, bk.patient_id, bk.origin, s.starts_at into v_b
      from sched.bookings bk
      join mpi.patients pt on pt.id = bk.patient_id and pt.clinic_id = bk.clinic_id and pt.erased_at is null
      join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
      join sched.slots s on s.id = bk.slot_id and s.clinic_id = bk.clinic_id
     where bk.id = p_booking_id and pt.account_id = v_actor and bk.status = 'confirmed' and s.starts_at > clock_timestamp();
    if not found then
        raise exception 'mark refused' using errcode = '42501';  -- not theirs, not confirmed, or the visit has started: one answer
    end if;
    v_key := 'visit-prep:' || v_actor::text || ':' || p_idempotency_key;
    select m.id, m.visit_id, m.item, m.done into v_prior from ops.visit_prep_marks m where m.clinic_id = v_b.clinic_id and m.idempotency_key = v_key;
    if found then
        if v_prior.visit_id <> p_booking_id or v_prior.item <> p_item or v_prior.done <> p_done then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, false;
        return;
    end if;
    insert into ops.visit_prep_marks (clinic_id, origin, visit_id, patient_id, item, done, marked_by, idempotency_key)
    values (v_b.clinic_id, v_b.origin, p_booking_id, v_b.patient_id, p_item, p_done, v_actor, v_key)
    returning id into v_id;
    insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key)
    values (v_b.origin, v_b.clinic_id, v_actor, 'patient', 'sched.visit_prep.marked', 'sched.bookings', p_booking_id::text,
            jsonb_build_object('item', p_item, 'done', p_done), 'sys:visit-prep:' || gen_random_uuid()::text);
    return query select v_id, true;
end
$$;

-- ---- the staff view (read-only) -----------------------------------------------------------------------------------------------------------------------

create function sched.staff_visit_prep(p_booking_id uuid)
    returns table (o_visit_type text, o_item text, o_done boolean, o_marked_at timestamptz)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_clinic uuid;
    v_vt     text;
begin
    if v_actor is null or p_booking_id is null or not iam.aal2_session() then
        raise exception 'visit refused' using errcode = '42501';
    end if;
    select bk.clinic_id, vt.name into v_clinic, v_vt
      from sched.bookings bk join sched.slots s on s.id = bk.slot_id and s.clinic_id = bk.clinic_id
      join sched.visit_types vt on vt.id = s.visit_type_id and vt.clinic_id = s.clinic_id
     where bk.id = p_booking_id;
    if v_clinic is null or not iam.has_clinic_role(v_clinic, array['nurse', 'doctor']) then
        raise exception 'visit refused' using errcode = '42501';
    end if;
    return query
        select v_vt, m.item, m.done, m.created_at
          from ops.visit_prep_marks m
         where m.visit_id = p_booking_id
           and not exists (select 1 from ops.visit_prep_marks n where n.visit_id = m.visit_id and n.item = m.item and (n.created_at, n.id) > (m.created_at, m.id))
         order by m.item;
    if not found then
        return query select v_vt, null::text, null::boolean, null::timestamptz;
    end if;
end
$$;

revoke all on function sched.patient_visit_prep(uuid), sched.patient_mark_prep(uuid, text, boolean, text), sched.staff_visit_prep(uuid) from public;
grant execute on function sched.patient_visit_prep(uuid), sched.patient_mark_prep(uuid, text, boolean, text), sched.staff_visit_prep(uuid) to careflow_app;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function sched.patient_visit_prep(uuid), sched.patient_mark_prep(uuid, text, boolean, text), sched.staff_visit_prep(uuid) from %I', r);
        end if;
    end loop;
end $$;
