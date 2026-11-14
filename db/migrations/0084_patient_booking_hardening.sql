-- 0084_patient_booking_hardening (whole-set review of 0079 to 0083, 2026-11-14; fix forward from 0083; CLAUDE.md rules 8, 10).
--
--   (1) AUDIT KEYS. 0083 wrote its two audit rows with keys built from ids clinic staff can read (`patient-booking-audit:<booking>`, `patient-cancel-audit:<booking>`). The audit insert policy lets clinic staff insert any key
--       that does not start with the reserved prefix `sys:` (0035), so a planted key would make the patient's cancel fail for ever. Both now use a server-made key with the reserved prefix and a random part
--       (idempotency is already enforced by the booking rows written in the same transaction). Every other audit key in 0079 to 0083 already has the `sys:` prefix.
--   (2) NO ADVISORY LOCK. 0083 serialised one patient's bookings with an advisory lock any application session could also take (0013 avoids them for that reason). It now takes a row lock on the owner-managed bookable-visit-type row,
--       which the application role has no grant on. That serialises bookings per visit type of a clinic, coarser than per patient and safe; the cap cannot be passed by two concurrent bookings.
-- The functions are re-issued with pg_get_functiondef and replace (as 0082 did) so nothing else in them can drift; the closing block asserts each replacement happened exactly once.

do $$
declare
    v_def text;
    v_new text;
begin
    v_def := pg_get_functiondef('sched.patient_book_slot(uuid, text)'::regprocedure);
    v_new := replace(v_def, '''patient-booking-audit:'' || v_booking::text', '''sys:patient-booking:'' || gen_random_uuid()::text');
    if v_new = v_def then raise exception '0084: the booking audit key was not found'; end if;
    v_def := v_new;
    v_new := replace(v_def, 'perform pg_advisory_xact_lock(hashtextextended(''patient-booking-cap:'' || v_patient::text, 0));',
                     'perform 1 from sched.patient_bookable_visit_types b where b.clinic_id = v_s.clinic_id and b.visit_type_id = v_s.visit_type_id for no key update;');
    if v_new = v_def then raise exception '0084: the advisory lock was not found'; end if;
    execute v_new;

    v_def := pg_get_functiondef('sched.patient_cancel_booking(uuid, text)'::regprocedure);
    v_new := replace(v_def, '''patient-cancel-audit:'' || p_booking_id::text', '''sys:patient-cancel:'' || gen_random_uuid()::text');
    if v_new = v_def then raise exception '0084: the cancel audit key was not found'; end if;
    execute v_new;

    if exists (select 1 from pg_proc where oid = 'sched.patient_book_slot(uuid, text)'::regprocedure and (prosrc like '%advisory%' or prosrc like '%patient-booking-audit%' or not prosecdef))
       or exists (select 1 from pg_proc where oid = 'sched.patient_cancel_booking(uuid, text)'::regprocedure and (prosrc like '%patient-cancel-audit%' or not prosecdef)) then
        raise exception '0084: a booking function was not re-issued as expected';
    end if;
    if not has_function_privilege('careflow_app', 'sched.patient_book_slot(uuid, text)', 'execute') or not has_function_privilege('careflow_app', 'sched.patient_cancel_booking(uuid, text)', 'execute') then
        raise exception '0084: the application role lost execute on a booking function';
    end if;
end $$;
