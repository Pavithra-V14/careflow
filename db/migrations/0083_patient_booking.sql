-- 0083_patient_booking (owner decision 2026-11-14, frontend request 28; BUILD-SPEC.md 17.5, 8.2; CLAUDE.md rules 1, 3, 4, 5, 8, 10).
--
-- A bound, active patient can list open FUTURE slots, book one, list their own bookings and cancel a FUTURE booking. MECHANISM ONLY, off unless the owner turns it on, and nothing is bookable until the owner says which visit types a patient may
-- book for themselves: that choice is operational and clinical, so it is data the owner inserts into `sched.patient_bookable_visit_types` (no default, no application grant). A visit type that is not listed is never offered and never bookable.
-- Each row also carries `max_future_bookings` (1 to 20, the owner's number): a patient with that many confirmed future bookings cannot book another.
--   * The patient has NO table grant and NO policy on sched.*; everything is a definer function that checks auth.uid() itself and refuses with one 42501 for every reason (missing, another clinic's, not yours, not offered).
--   * A slot that is held is bookable only by the patient it is held for (an offer front desk sent); a hold for nobody is NOT bookable by a patient. Expired holds are settled under the slot lock exactly as sched's staff path does.
--   * Cancelling needs the slot to start in the future (spec: only a future appointment can be cancelled); it frees the slot, writes an audit row, and contacts nobody.
--   * Refusals other than 42501: 55006 (the slot is taken, held for someone else, or not yet or no longer open), 55000 (the booking cannot be cancelled: already cancelled or past), 54000 (the patient's cap is reached), 23505 (an idempotency key
--     reused for a different request).
-- Every write carries the caller's idempotency key, namespaced by the account (rule 5). No dose, diagnosis, or clinical wording is involved.
--
-- NEW GRANTS (ledger): EXECUTE on sched.patient_open_slots, sched.patient_book_slot, sched.patient_bookings, sched.patient_cancel_booking to careflow_app. New table sched.patient_bookable_visit_types: RLS enabled and forced, no policy, no grant.

create table sched.patient_bookable_visit_types (
    clinic_id           uuid not null,
    visit_type_id       uuid not null,
    max_future_bookings smallint not null check (max_future_bookings between 1 and 20),
    created_at          timestamptz not null default clock_timestamp(),
    primary key (clinic_id, visit_type_id),
    foreign key (visit_type_id, clinic_id) references sched.visit_types (id, clinic_id)
);
alter table sched.patient_bookable_visit_types enable row level security;
alter table sched.patient_bookable_visit_types force row level security;
revoke all on sched.patient_bookable_visit_types from public;

-- ---- list open future slots of the visit types a patient may book --------------------------------------------------------------

create function sched.patient_open_slots(p_clinic_id uuid, p_limit integer)
    returns table (slot_id uuid, starts_at timestamptz, ends_at timestamptz, visit_type_id uuid, visit_type_name text, location_name text)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
begin
    if v_actor is null or p_clinic_id is null or p_limit is null or p_limit not between 1 and 100
       or not exists (select 1 from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
                       where pt.account_id = v_actor and pt.clinic_id = p_clinic_id and pt.erased_at is null) then
        raise exception 'slots refused' using errcode = '42501';
    end if;
    return query
        select s.id, s.starts_at, s.ends_at, s.visit_type_id, vt.name, l.name
          from sched.slots s
          join sched.patient_bookable_visit_types b on b.clinic_id = s.clinic_id and b.visit_type_id = s.visit_type_id
          join sched.visit_types vt on vt.id = s.visit_type_id and vt.clinic_id = s.clinic_id and vt.active
          join sched.locations l on l.id = s.location_id and l.clinic_id = s.clinic_id and l.active
         where s.clinic_id = p_clinic_id and s.status = 'open' and s.starts_at > clock_timestamp()
           and s.origin = (select pt.origin from mpi.patients pt where pt.account_id = v_actor and pt.clinic_id = p_clinic_id and pt.erased_at is null)
         order by s.starts_at, s.id
         limit p_limit;
end
$$;

-- ---- book --------------------------------------------------------------------------------------------------------------------

create function sched.patient_book_slot(p_slot_id uuid, p_idempotency_key text)
    returns table (o_booking_id uuid, o_slot_id uuid, o_created boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor    uuid := auth.uid();
    v_s        record;
    v_patient  uuid;
    v_max      smallint;
    v_key      text;
    v_prior    record;
    v_hold     uuid;
    v_hold_pt  uuid;
    v_status   text;
    v_booking  uuid;
begin
    if v_actor is null or p_slot_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'booking refused' using errcode = '42501';
    end if;
    select s.id, s.clinic_id, s.origin, s.status, s.visit_type_id, s.starts_at into v_s from sched.slots s where s.id = p_slot_id for update;
    if not found then
        raise exception 'booking refused' using errcode = '42501';
    end if;
    select pt.id into v_patient
      from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
     where pt.account_id = v_actor and pt.clinic_id = v_s.clinic_id and pt.origin = v_s.origin and pt.erased_at is null;
    select b.max_future_bookings into v_max from sched.patient_bookable_visit_types b where b.clinic_id = v_s.clinic_id and b.visit_type_id = v_s.visit_type_id;
    if v_patient is null or v_max is null then
        raise exception 'booking refused' using errcode = '42501';
    end if;
    v_key := 'patient-booking:' || v_actor::text || ':' || p_idempotency_key;
    select bk.id, bk.slot_id, bk.patient_id into v_prior from sched.bookings bk where bk.idempotency_key = v_key;
    if found then
        if v_prior.slot_id <> p_slot_id or v_prior.patient_id <> v_patient then
            raise exception 'idempotency key reused' using errcode = '23505';
        end if;
        return query select v_prior.id, p_slot_id, false;
        return;
    end if;
    if v_s.starts_at <= clock_timestamp() then
        raise exception 'slot not open' using errcode = '55006';
    end if;
    if (select count(*) from sched.bookings bk join sched.slots s2 on s2.id = bk.slot_id
         where bk.patient_id = v_patient and bk.status = 'confirmed' and s2.starts_at > clock_timestamp()) >= v_max then
        raise exception 'booking cap reached' using errcode = '54000';
    end if;
    -- settle expired holds under the slot lock, as the staff path does
    update sched.holds h set status = 'expired' where h.slot_id = p_slot_id and h.status = 'active' and h.expires_at <= clock_timestamp();
    select h.id, h.patient_id into v_hold, v_hold_pt from sched.holds h where h.slot_id = p_slot_id and h.status = 'active';
    v_status := v_s.status;
    if v_status = 'held' and v_hold is null then
        v_status := 'open';
    end if;
    if v_status = 'held' then
        if v_hold_pt is distinct from v_patient then
            raise exception 'slot not open' using errcode = '55006';
        end if;
    elsif v_status <> 'open' then
        raise exception 'slot not open' using errcode = '55006';
    end if;
    insert into sched.bookings (clinic_id, slot_id, patient_id, hold_id, origin, idempotency_key)
    values (v_s.clinic_id, p_slot_id, v_patient, v_hold, v_s.origin, v_key)
    returning id into v_booking;
    if v_hold is not null then
        update sched.holds h set status = 'converted' where h.id = v_hold;
    end if;
    update sched.slots s set status = 'booked' where s.id = p_slot_id;
    update sched.waitlist w set status = 'fulfilled' where w.patient_id = v_patient and w.status = 'waiting' and w.visit_type_id = v_s.visit_type_id;
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (v_s.origin, v_s.clinic_id, v_actor, 'sched.patient_booking.created', 'sched.bookings', v_booking::text, jsonb_build_object('slot_id', p_slot_id),
            'patient-booking-audit:' || v_booking::text);
    return query select v_booking, p_slot_id, true;
end
$$;

-- ---- the caller's own bookings -----------------------------------------------------------------------------------------------

create function sched.patient_bookings()
    returns table (booking_id uuid, clinic_id uuid, slot_id uuid, status text, starts_at timestamptz, ends_at timestamptz, visit_type_name text, location_name text, cancellable boolean)
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor uuid := auth.uid();
begin
    if v_actor is null or not exists (select 1 from iam.accounts a where a.id = v_actor and a.account_type = 'patient' and a.status = 'active') then
        raise exception 'bookings refused' using errcode = '42501';
    end if;
    return query
        select bk.id, bk.clinic_id, bk.slot_id, bk.status, s.starts_at, s.ends_at, vt.name, l.name,
               (bk.status = 'confirmed' and s.starts_at > clock_timestamp())
          from sched.bookings bk
          join mpi.patients pt on pt.id = bk.patient_id and pt.clinic_id = bk.clinic_id and pt.account_id = v_actor and pt.erased_at is null
          join sched.slots s on s.id = bk.slot_id and s.clinic_id = bk.clinic_id
          join sched.visit_types vt on vt.id = s.visit_type_id and vt.clinic_id = s.clinic_id
          join sched.locations l on l.id = s.location_id and l.clinic_id = s.clinic_id
         order by s.starts_at desc, bk.id;
end
$$;

-- ---- cancel a future booking ----------------------------------------------------------------------------------------------------

create function sched.patient_cancel_booking(p_booking_id uuid, p_idempotency_key text)
    returns table (o_booking_id uuid, o_changed boolean)
    language plpgsql security definer set search_path = ''
as $$
declare
    v_actor   uuid := auth.uid();
    v_b       record;
    v_key     text;
    v_starts  timestamptz;
begin
    if v_actor is null or p_booking_id is null or p_idempotency_key is null or length(btrim(p_idempotency_key)) = 0 or length(p_idempotency_key) > 200 then
        raise exception 'cancel refused' using errcode = '42501';
    end if;
    select bk.id, bk.slot_id, bk.clinic_id, bk.origin, bk.status, bk.cancel_idempotency_key into v_b
      from sched.bookings bk
      join mpi.patients pt on pt.id = bk.patient_id and pt.clinic_id = bk.clinic_id and pt.account_id = v_actor and pt.erased_at is null
      join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
     where bk.id = p_booking_id;
    if not found then
        raise exception 'cancel refused' using errcode = '42501';
    end if;
    v_key := 'patient-cancel:' || v_actor::text || ':' || p_idempotency_key;
    select s.starts_at into v_starts from sched.slots s where s.id = v_b.slot_id for update;
    if v_b.status = 'cancelled' then
        if v_b.cancel_idempotency_key = v_key then
            return query select p_booking_id, false;
            return;
        end if;
        raise exception 'booking cannot be cancelled' using errcode = '55000';
    end if;
    if v_starts <= clock_timestamp() then
        raise exception 'booking cannot be cancelled' using errcode = '55000';
    end if;
    update sched.bookings bk set status = 'cancelled', cancelled_at = clock_timestamp(), cancel_idempotency_key = v_key where bk.id = p_booking_id;
    update sched.slots s set status = 'open' where s.id = v_b.slot_id and s.status = 'booked';
    insert into audit.log (origin, clinic_id, actor_id, action, resource_type, resource_id, details, idempotency_key)
    values (v_b.origin, v_b.clinic_id, v_actor, 'sched.patient_booking.cancelled', 'sched.bookings', p_booking_id::text, jsonb_build_object('slot_id', v_b.slot_id),
            'patient-cancel-audit:' || p_booking_id::text);
    return query select p_booking_id, true;
end
$$;

-- ---- privileges ---------------------------------------------------------------------------------------------------------------------

revoke all on function sched.patient_open_slots(uuid, integer), sched.patient_book_slot(uuid, text), sched.patient_bookings(), sched.patient_cancel_booking(uuid, text) from public;
grant execute on function sched.patient_open_slots(uuid, integer), sched.patient_book_slot(uuid, text), sched.patient_bookings(), sched.patient_cancel_booking(uuid, text) to careflow_app;

do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function sched.patient_open_slots(uuid, integer), sched.patient_book_slot(uuid, text), sched.patient_bookings(), sched.patient_cancel_booking(uuid, text) from %I', r);
            execute format('revoke all on sched.patient_bookable_visit_types from %I', r);
        end if;
    end loop;
    if has_table_privilege('careflow_app', 'sched.patient_bookable_visit_types', 'select') or has_table_privilege('careflow_app', 'sched.patient_bookable_visit_types', 'insert') then
        raise exception '0083: the bookable list is owner-managed';
    end if;
end $$;
