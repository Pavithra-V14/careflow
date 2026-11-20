-- 0099_patient_booking_state: say WHY the patient's list of bookable slots is empty, in two fixed words (found in the live patient walk, 2026-11-20: the list was empty and nothing told the patient or the demonstrator why).
--
-- sched.patient_booking_state(p_clinic_id) returns ONE word for the caller's own clinic:
--   * 'ok'             there is at least one open, future slot of a visit type the clinic lets patients book (the list is not empty);
--   * 'no_open_times'  booking IS set up (at least one active visit type the owner listed as bookable, at an active location) but no open future slot of such a type exists now;
--   * 'not_set_up'     no active visit type is listed as bookable for patients at this clinic (the owner has not set it up).
-- Who may ask: exactly the callers sched.patient_open_slots lets in (an ACTIVE patient account bound to a live record of that clinic); anyone else gets the one fixed 42501. It reads the same tables with the same
-- conditions (status open, start in the future, the patient's own origin, active type and location) and returns a word, never a count, a slot, a name or an id. SECURITY DEFINER, search_path pinned, executable by
-- careflow_app only. No table changes; it writes nothing.

create function sched.patient_booking_state(p_clinic_id uuid) returns text
    language plpgsql stable security definer set search_path = ''
as $$
declare
    v_actor  uuid := auth.uid();
    v_origin text;
begin
    if v_actor is null or p_clinic_id is null then
        raise exception 'booking state refused' using errcode = '42501';
    end if;
    select pt.origin into v_origin
      from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active'
     where pt.account_id = v_actor and pt.clinic_id = p_clinic_id and pt.erased_at is null;
    if not found then
        raise exception 'booking state refused' using errcode = '42501';
    end if;
    if not exists (
        select 1 from sched.patient_bookable_visit_types b
          join sched.visit_types vt on vt.id = b.visit_type_id and vt.clinic_id = b.clinic_id and vt.active
         where b.clinic_id = p_clinic_id
    ) then
        return 'not_set_up';
    end if;
    if exists (
        select 1 from sched.slots s
          join sched.patient_bookable_visit_types b on b.clinic_id = s.clinic_id and b.visit_type_id = s.visit_type_id
          join sched.visit_types vt on vt.id = s.visit_type_id and vt.clinic_id = s.clinic_id and vt.active
          join sched.locations l on l.id = s.location_id and l.clinic_id = s.clinic_id and l.active
         where s.clinic_id = p_clinic_id and s.status = 'open' and s.starts_at > clock_timestamp() and s.origin = v_origin
    ) then
        return 'ok';
    end if;
    return 'no_open_times';
end
$$;

revoke all on function sched.patient_booking_state(uuid) from public;
do $$
declare r text;
begin
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
        if exists (select 1 from pg_roles where rolname = r) then
            execute format('revoke all on function sched.patient_booking_state(uuid) from %I', r);
        end if;
    end loop;
end $$;
grant execute on function sched.patient_booking_state(uuid) to careflow_app;

do $$
begin
    if has_function_privilege('public', 'sched.patient_booking_state(uuid)', 'execute') or not has_function_privilege('careflow_app', 'sched.patient_booking_state(uuid)', 'execute') then
        raise exception '0099: patient_booking_state must be executable by careflow_app only';
    end if;
end $$;
