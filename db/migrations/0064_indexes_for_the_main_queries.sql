-- 0064_indexes_for_the_main_queries (owner request 2026-11-08). A static review of the SQL the application runs per request or per tick against the indexes the migrations created found the gaps below. Every index
-- is `if not exists`, ADDS no privilege and changes no behaviour. Partial indexes match the exact predicate of the query they serve (the watchdog's queue facts query was rewritten in the same change so
-- its `state = ...` filters sit in the WHERE clause where a partial index can be used). The voice chunk expiry index came with 0063.

-- ops.queue: the referral list looks up the newest job per document; the watchdog and the dead-letter raiser run every tick
create index if not exists queue_idx_document on ops.queue ((payload ->> 'document_id'), created_at desc, id) where payload ->> 'document_id' is not null;
create index if not exists queue_idx_unclaimed_age on ops.queue (clinic_id, origin, created_at) where state = 'unclaimed';
create index if not exists queue_idx_dead on ops.queue (clinic_id, origin, dead_lettered_at) where state = 'dead_lettered';
create index if not exists queue_idx_dead_order on ops.queue (dead_lettered_at, id) where state = 'dead_lettered';
create index if not exists queue_idx_completed on ops.queue (clinic_id, completed_at) where state = 'completed';

-- agents.review_tasks
create index if not exists review_tasks_idx_source on agents.review_tasks (source_message_id, kind) include (status);
create index if not exists review_tasks_idx_patient on agents.review_tasks (clinic_id, (provenance ->> 'patient_id')) where status <> 'resolved';
create index if not exists review_tasks_idx_clinic_created on agents.review_tasks (clinic_id, created_at desc);

-- mpi.match_candidates: per-patient lookups on every identity load and every merge
create index if not exists mc_idx_patient on mpi.match_candidates (patient_id, created_at, id);
create index if not exists mc_idx_candidate on mpi.match_candidates (candidate_patient_id);

-- ops.care_plan_waits / ops.care_plans
create index if not exists cpw_idx_wait_key on ops.care_plan_waits (wait_key);
create index if not exists cpw_idx_plan_step on ops.care_plan_waits (plan_id, step_key);
create index if not exists cp_idx_clinic_created on ops.care_plans (clinic_id, created_at desc, id) where status <> 'erased';

-- sched.holds / sched.bookings
create index if not exists holds_idx_clinic_expiry on sched.holds (clinic_id, expires_at, id);
create index if not exists holds_idx_offer on sched.holds (clinic_id, created_at desc, id) where idempotency_key like 'offer-hold:%';
create index if not exists holds_idx_patient on sched.holds (patient_id) where patient_id is not null;
create index if not exists bk_idx_clinic_confirmed on sched.bookings (clinic_id, slot_id) where status = 'confirmed';

-- small tables listed newest first
create index if not exists gr_idx_clinic_created on iam.grievances (clinic_id, created_at desc, id);
create index if not exists gr_idx_account_created on iam.grievances (account_id, created_at desc, id);
create index if not exists alerts_idx_clinic_created on ops.alerts (clinic_id, created_at desc, id);
create index if not exists bg_idx_clinic_opened on iam.break_glass_grants (clinic_id, opened_at desc, id);

-- audit.log: the auditor's filters
create index if not exists audit_idx_resource on audit.log (resource_type, resource_id, id desc);
create index if not exists audit_idx_actor on audit.log (actor_id, id desc) where actor_id is not null;
create index if not exists audit_idx_action on audit.log (action, id desc);
