-- 0055_care_plans_insert_id: fix forward for 0050 (found by CI run 42336238364; CLAUDE.md rule 10).
--
-- The application creates a care plan with a DETERMINISTIC id (a uuid derived from the clinic and the idempotency key, careflow.care_journey_sql.plan_id_for), because the plan text is encrypted with the plan
-- id bound into it and a replay must name the same plan. 0050 granted INSERT on every column of ops.care_plans EXCEPT id, so every plan creation failed with "permission denied". This grants the one column.
-- The one thing this adds: a client-chosen id can reveal, through a unique violation, that the same uuid exists in some clinic (one bit, no clinic named; the route answers 404 either way). Nothing else changes: the id is a primary key with the insert policy still pinning clinic, origin, creator and status, and a duplicate id is a unique violation, never an overwrite.

grant insert (id) on ops.care_plans to careflow_app;

do $$
begin
    if not has_column_privilege('careflow_app', 'ops.care_plans', 'id', 'insert') then
        raise exception '0055: the application role must be able to insert the care plan id';
    end if;
    if has_column_privilege('careflow_app', 'ops.care_plans', 'id', 'update') or has_column_privilege('careflow_app', 'ops.care_plans', 'plan_ct', 'update')
       or has_column_privilege('careflow_app', 'ops.care_plans', 'clinic_id', 'update') or has_column_privilege('careflow_app', 'ops.care_plans', 'patient_id', 'update') then
        raise exception '0055: the plan id, text, clinic and patient must stay immutable for the application role';
    end if;
end $$;
