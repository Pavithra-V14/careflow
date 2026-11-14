# CareFlow: handover for a new developer

Written 2026-11-13. `BUILD-SPEC.md` is the source of truth for every design decision and its reason; `CLAUDE.md` holds the 13 non-negotiable rules; this page is the map. Where this page and the code disagree, the code and the spec win: please fix this page.

## 1. Architecture on one page

CareFlow is a clinic's scheduling, messaging and review backend (FastAPI, Python 3.12, SQLAlchemy over asyncpg, a hosted Supabase Postgres for data and sign-in, LangGraph for the few long-running flows). The frontend is a separate workstream under `frontend/` and is not described here.

- **Routes** (`careflow/app.py`, the one large module): every staff route takes a verified token (`verified_claims`), then a database connection set to the restricted role `careflow_app` with the caller's claims (`db_conn`). Row-level security, not application code, decides what a caller sees; the routes add the role checks and the audit rows.
- **Database** (`db/migrations/`, applied in order by `python -m careflow.migrate`, checksummed ledger, never edit a merged migration): schemas `iam` (accounts, staff roles, clinics), `mpi` (patients, identity), `sched` (slots, holds, bookings, waitlist), `msg` (threads, messages, screenings, voice), `docs` (referral documents), `agents` (review tasks, triage, graph checkpoints), `kb` (red-flag rules), `clinical` (results, notes, medications), `ops` (queue, alerts, ledgers, care plans), `audit` (append-only log), `ai` (model registry). Many writes go through SECURITY DEFINER functions so the application role has no table grant.
- **Rules before models**: the deterministic red-flag engine (`careflow/rules.py`) runs first on every text path, then any model (today only fakes). `unassessable` is never `clear`.
- **Agents**: the Front Door (referral intake, `front_door.py`), Scheduling (offers and backfill), Care Journey, Visit Readiness, Care Gaps and the Voice monitor are graphs or pure modules over ports with fake adapters (`careflow/adapters/`). A node that pauses causes no side effect (a structural test enforces it).
- **Queue and worker** (`queue.py`, `tick.py`, `app_worker`): a Postgres job queue and an outbox; the worker acts as its own database identity.
- **Gateway** (`gateway.py`): every model call goes through it (registry rows, budgets, the spend ledger); no real model is approved or wired.
- **Operating profiles**: every environment is `demo` (synthetic data, labelled placeholders allowed) or `clinical` (no placeholder, every required setting present, an approved route for anything touching patient text). Features degrade visibly (503 with a fixed reason), never silently.

## 2. How the phases fit

Phase order is Appendix D of the spec. 0 to 1: database, auth, patients, scheduling. 2: the Front Door (upload, extract, rules, triage proposal, human review). 3: scheduling offers, backfill, reply handling. 4: guardrails, approval modes, canaries, reviewer statistics, alerts, cost. 5: evaluation (gold set, gates, scorecard). 6 to 9: Care Journey, Visit Readiness, Voice, Care Gaps (flagged off until their clinician content exists). 10: pre-launch checks (drills, readiness rubric). Later work added the patient portal and rights, break-glass, staff recovery, clinical results, notes and medications, erasure, retention reporting, the session spend ledger, and the red-flag rule life cycle (author, two-doctor approve, withdraw, two-doctor retire). Each phase's design note is in `docs/design/`.

## 3. Where the 13 rules are enforced

| Rule | Where |
|---|---|
| 1 No invented clinical content | `# CLINICAL-PLACEHOLDER` markers; rule and card content are data from clinicians; tests use meaningless marker words; the `clinical-content-reviewer` agent |
| 2 Red-flag rules before any model | `careflow/rules.py`; callers in `messages.py`, `front_door.py`, `voice.py`; `msg.record_screening` (migration 0013) refuses a `clear` the database cannot confirm |
| 3 Unknown is not a value | `unassessable` outcome; NULL `verified` flags; required settings with no defaults; `503` when a setting is unset |
| 4 A pausing node has no side effect | `careflow/graph_structure.py` and the structure tests (`tests/test_*_structure.py`) |
| 5 Every write has an idempotency key | `IdempotencyKey` on routes; unique keys per clinic in tables; definer functions take a key |
| 6 No agent-owned patient memory | Everything durable is clinical record, operational state or versioned knowledge (`docs/design/`); no free-text agent store |
| 7 Patient data stays inside the boundary | the data-flow register of `BUILD-SPEC.md` section 17.6 (checked by `tests/test_data_flow_register.py`); no external call sees patient text without a listed route |
| 8 RLS enforces isolation | Forced RLS on every clinic table from its creating migration; the role-matrix test (`tests/test_api_role_matrix.py`) runs against the restricted role; every route has cases there |
| 9 Auto-approved outputs never become labels | `careflow/learning_labels.py` (only human decisions; never from a demo or synthetic-only deployment) |
| 10 Never edit a merged migration | The checksummed ledger in `careflow/migrate.py`; fix forward |
| 11 Voice is not lower scrutiny | `careflow/voice.py`: the same rule engine on the live transcript (`StreamEvaluator`) |
| 12 No Docker | Hosted Supabase for dev, CI and production; none in the repository |
| 13 Ask before a new external service | The data-flow register, `docs/design/grants-ledger.md` and the decision notes record each approval |

## 4. How to run everything

Windows with PowerShell is the owner's setup; see `docs/first-run-guide.md` for the exact windows and commands. In short:

- `uv sync`, then `uv run ruff check .` and `uv run pytest -q tests --ignore-glob='*live*' --ignore=tests/test_api_role_matrix.py` for the offline suite (about 2300 tests, a few minutes).
- Live tests (`*_live.py`, the role matrix) need the hosted CI project and run in CI only: `.github/workflows/ci.yml` splits them into numbered shards; a new live test file must be added to exactly one shard (`tests/test_ci_shards.py` checks it).
- Migrate before starting: `python -m careflow.migrate --check`, then `python -m careflow.migrate`. The app REFUSES TO START on a database missing the migrations its code needs (`careflow/schema_check.py`).
- Demo backend: `scripts/run_demo_backend.ps1 -Port 8000` (demo profile, synthetic data). OpenAPI snapshot: `python scripts/export_openapi.py` (a test fails if it is stale). Gold run: `python scripts/gold_run.py out.json`; scorecard: `python scripts/gate_scorecard.py out.json`.
- Demo walkthrough: `docs/demo-walkthrough.md`; synthetic end-to-end sequence: `tests/test_e2e_demo_flow_live.py` and `tests/test_phase2_e2e_live.py`.

## 5. Required settings (environment variables), and what they mean

Authority is the module that reads each one; many are CLINICIAN- or OWNER-OWNED values with NO default (unset means the feature refuses or stays off). Grouped:

- **Platform**: `CAREFLOW_PROFILE` (`demo` or `clinical`; unset or unknown means `clinical`); `CAREFLOW_DATA_POLICY` (`synthetic_only` or `phi_approved`); `CAREFLOW_ENV`; `CAREFLOW_DEV_PROJECT_REF` (guards the owner-run dev scripts); `CAREFLOW_DEMO_PHASE3`, `CAREFLOW_DEMO_PHASE4` (load the labelled demo packs `config/demo-phase3.yaml` and `demo-phase4.yaml`; demo plus synthetic only).
- **Database and sign-in**: `SUPABASE_URL`, `DATABASE_URL_APP` (restricted role), `DATABASE_URL_MIGRATOR` (owner; migrations and owner scripts only), `SUPABASE_SECRET_KEY`. Never print or commit these.
- **Staff second factor**: `CAREFLOW_REQUIRE_AAL2` (default on; may be off only in demo with synthetic data), `CAREFLOW_STEP_UP_MAX_AGE_SECONDS` (how fresh a code must be for step-up routes), `CAREFLOW_REQUIRED_ROLES`, `CAREFLOW_SIGN_IN_URL`, `CAREFLOW_AUTH_ADMIN_VERIFIED` (the Auth admin call stays unwired by owner decision).
- **Encryption keys**: `CAREFLOW_ENC_SECRET_V<n>` and `CAREFLOW_HMAC_SECRET_V<n>` (AES-256-GCM and keyed hashes, by version), `CAREFLOW_ACTIVE_KEY_VERSION`, `CAREFLOW_LABEL_PSEUDONYM_KEY` (pseudonymises reviewer ids in label exports).
- **Worker and runs**: `CAREFLOW_WORKER_ACCOUNT_ID` (the queue worker's identity), `CAREFLOW_RUN_WORKER`, `CAREFLOW_DURABLE_RUNS`.
- **Feature flags (off by default)**: `FEATURE_CARE_JOURNEY`, `FEATURE_VISIT_READINESS`, `FEATURE_VOICE_CHANNEL`.
- **Clinician-owned content** (no defaults; `# CLINICAL-PLACEHOLDER: needs clinician sign-off`): `CAREFLOW_CARE_GAP_RULES`, `CAREFLOW_CARE_GAP_ALERT_DAYS`, `CAREFLOW_VISIT_READINESS_CONTENT`, `CAREFLOW_VISIT_READINESS_TASK_PRIORITY`, `CAREFLOW_CARE_PLAN_TASK_ROLE`, `CAREFLOW_CARE_PLAN_TASK_PRIORITY`, `CAREFLOW_REVOKE_TASK_ROLE`, `CAREFLOW_REVOKE_TASK_PRIORITY`, `CAREFLOW_REVOKE_ALERT_REASONS`, `CAREFLOW_MEDICATION_CODES`, `CAREFLOW_REVIEW_REASON_CODES`, `CAREFLOW_RUBBER_STAMP_POLICY`, `CAREFLOW_REVIEW_DEADLINE_MINUTES`, the scheduling values `CAREFLOW_SCHED_*` (offer expiry, hold lifetime, backfill candidate count, accept-confidence threshold, offer template), and the gold-run values `CAREFLOW_EVAL_RULES_FILE`, `CAREFLOW_EVAL_BAR_<GATE>`, `CAREFLOW_EVAL_BASELINE_<GATE>`.
- **Owner/clinic-owned values** (no defaults): `CAREFLOW_CLINIC_TIME_ZONE`, `CAREFLOW_GRIEVANCE_CATEGORIES`, `CAREFLOW_PUBLIC_READ_IP_PER_MINUTE` (the per-address limit on the public category list; no default), `CAREFLOW_CONSENT_NOTICES` and `CAREFLOW_CONSENT_LABELS` (counsel's wording; published only by the owner-run seed script), `CAREFLOW_ERASE_REASONS`, `CAREFLOW_BREAK_GLASS_REASONS` / `_MINUTES` / `_MAX_REOPENS` (seeded into owner-managed tables by `scripts/seed_break_glass_reasons.py`), `CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS`, `CAREFLOW_RETENTION_CHECKPOINT_DAYS`, `CAREFLOW_RETENTION_CLINICAL_RECORD_DAYS`, `CAREFLOW_AUTONOMOUS_MODE_ENABLED` and `CAREFLOW_CLINICAL_AUTONOMY_SIGNOFF` (the switch to autonomous stays off until both exist), `CAREFLOW_AUTONOMY_MODE`.
- **Rates, limits, cost**: `CAREFLOW_PATIENT_VIEW_*` (open, label-row and page limits), `CAREFLOW_CANARY_RATE`, `CAREFLOW_AUTO_REVIEW_SAMPLE_RATE`, `CAREFLOW_TRACE_SAMPLE_RATE`, `CAREFLOW_WATCHDOG_LIMITS`, `CAREFLOW_COST_PLATFORM_FLOOR`, `CAREFLOW_COST_REVIEWER_RATES`.
- **Storage**: `CAREFLOW_STORAGE_BUCKET`, `CAREFLOW_STORAGE_VERIFIED` (uploads stay refused outside demo until a scanner is wired and shown to work).
- **Owner-managed database tables, not environment variables**: `ops.spend_limits` (the per-clinic per-call cap of the session spend ledger), `iam.break_glass_settings` and `iam.break_glass_reasons`, `iam.public_clinics`. The application role cannot read or write them.

## 6. The migrations, in order

| # | Migration | What it does (first line of the file) |
|---|---|---|
| 0001 | `0001_initial_schema.sql` | Phase 0 foundations (BUILD-SPEC.md §17.1, §18.1, §18.4, Appendix D). |
| 0002 | `0002_mpi_and_scheduling.sql` | Phase 1 data layout for `mpi` and `sched` (BUILD-SPEC.md §17.1, §17.2, |
| 0003 | `0003_mpi_match_candidates.sql` | the human-review ledger for possible duplicate patients (§17.1, §17.2). |
| 0004 | `0004_scheduling_operations.sql` | holds, bookings, waitlist and clinician-authored priority levels |
| 0005 | `0005_audit_doctor_signoff_security_fix.sql` | make the named approver on a priority level genuine |
| 0006 | `0006_outbox_and_queue.sql` | the transactional outbox and the Postgres-backed queue (BUILD-SPEC.md §7.3, §7.4, |
| 0007 | `0007_red_flag_rules_schema.sql` | the STRUCTURE for deterministic red-flag rules and the nurse task they raise |
| 0008 | `0008_mpi_merge_ledger.sql` | logical patient merges, never destructive (BUILD-SPEC.md §12.1, §12.5, §17.1, §17.2; |
| 0009 | `0009_merge_signature_fix.sql` | fix-forward for 0008's update policy on mpi.merges (CLAUDE.md rule 10). |
| 0010 | `0010_key_rotation_ledger.sql` | the record of each batch of a manual key rotation (BUILD-SPEC.md §17.2; CLAUDE.md |
| 0011 | `0011_msg_threads_messages.sql` | message threads and messages, STAFF ACCESS ONLY (BUILD-SPEC.md §9.1, §9.4, §17.1, §17.2, |
| 0012 | `0012_patient_account_binding.sql` | close the hole that let any front-desk user bind ANY account to ANY patient record |
| 0013 | `0013_msg_screenings.sql` | the record that a stored message WAS screened for red flags, and the one function allowed to write it |
| 0014 | `0014_pin_clinic_on_child_foreign_keys.sql` | three foreign keys that did not pin the clinic now do (BUILD-SPEC.md §17.1, §18.4, §21; CLAUDE.md |
| 0015 | `0015_ai_model_registry.sql` | the model registry (BUILD-SPEC.md §2, §5.1, §6.1, §17.1 `ai`; CLAUDE.md rules 5, 7, 8, 13). |
| 0016 | `0016_spend_ledger_and_registry_approval.sql` | (BUILD-SPEC.md §5.1, §6.1, §15.2, §17.1 `ops` and `ai`; CLAUDE.md rules 5, 7, 8, 10, 13). |
| 0017 | `0017_docs_documents.sql` | the record that an uploaded document was received and VERIFIED (BUILD-SPEC.md §10.4, §11.5, §17.1 `docs`, §17.3; |
| 0018 | `0018_agents_triage_proposals_and_decisions.sql` | the triage PROPOSAL the Front Door writes, and the HUMAN decision on it (BUILD-SPEC.md §12.1, §12.3, §12.6; |
| 0019 | `0019_mpi_queue_worker_ingestion.sql` | let the queue worker (the system identity that runs the Front Door graph) register a patient from a verified referral |
| 0020 | `0020_referral_hold_review_tasks.sql` | a review task for a referral the Front Door could not take further, visible to and resolvable by front desk when it is theirs |
| 0021 | `0021_agents_graph_checkpoints.sql` | durable state for the agent graphs (BUILD-SPEC.md §7.2, §17.1 `agents`; CLAUDE.md rules 5, 6, 8, 10). Package P2-8 (checkpointer). |
| 0022 | `0022_worker_registration_function.sql` | close pre-launch blocker 1 (0019's residual risk 4). Design: docs/design/blocker1-worker-token-forgery.md (owner-approved 2026-10-28). |
| 0023 | `0023_unverified_worker_records.sql` | pre-launch blocker 10 (planted tokens), option A (owner-approved 2026-10-28). Design: docs/design/planted-token-design.md. CLAUDE.md rules 5, 8, 10. |
| 0024 | `0024_worker_role_exclusive.sql` | an account that holds `queue_worker` holds no other role, and an account that holds any other role cannot be given `queue_worker` |
| 0025 | `0025_identity_confirmation_revoke.sql` | 0025: revoking a mistaken identity confirmation (docs/design/identity-confirmation-revoke.md; owner instruction 2026-10-29). Fix-forward of 0023: nothing in 0023 is edited. |
| 0026 | `0026_unmerge_task_bound_to_merge.sql` | 0026: bind the front-desk insert of an `unmerge_review` review task to a real unmerge (fix-forward of 0013; owner decision 2026-10-30; found by the rls-security-reviewer pass on 0025). |
| 0027 | `0027_storage_objects_policies.sql` | Owner-approved 2026-10-30 (final shape: deny everything on storage.objects and storage.buckets for anon and authenticated). Dry-run in the dev project as the real migrator inside begin/rollback: ap... |
| 0028 | `0028_active_role.sql` | 0028: the ACTIVE ROLE (docs/design/active-role.md; owner decision 2026-10-31). A request may carry `careflow_active = {"clinic_id", "role"}` inside the SAME claims JSON that already |
| 0029 | `0029_merge_state_machine.sql` | fix-forward for 0008 and 0009 (CLAUDE.md rule 10; review H1 and M2 of 2026-11-04, docs/design/review-2026-11-04.md). |
| 0030 | `0030_audit_and_queue_insert_hygiene.sql` | tighten two INSERT policies (review M3 and the write half of M4, 2026-11-04; docs/design/review-2026-11-04.md). Fix-forward for 0001 and 0006 (CLAUDE.md rule 10). |
| 0031 | `0031_offer_deliveries.sql` | a truthful record that a PERSON delivered an offer (Phase 3 delivery with no external service; pre-approved by the owner 2026-11-05; docs/design/phase3-manual-delivery.md). |
| 0032 | `0032_patient_update_narrowing_and_audited_erasure.sql` | review finding M1 (docs/design/review-2026-11-04.md; docs/design/patient-erasure-and-update-narrowing.md; pre-approved by the owner 2026-11-05). |
| 0033 | `0033_expired_hold_sweep.sql` | let the worker's scheduled tick tidy expired holds (pre-approved by the owner 2026-11-05; docs/design/expired-hold-sweep.md; referral intake stays manual). |
| 0034 | `0034_staff_account_management.sql` | invite, grant, revoke, suspend and activate staff accounts (pre-approved by the owner 2026-11-05; docs/design/staff-account-management.md). |
| 0035 | `0035_reserved_system_audit_keys.sql` | audit keys beginning `sys:` belong to the database's own functions (reviewer finding 2026-11-05; fix-forward for 0030, CLAUDE.md rule 10). |
| 0036 | `0036_merge_candidate_trigger_self_merge.sql` | fix-forward for 0029 (CLAUDE.md rule 10; found by CI on main, 2026-11-05). |
| 0037 | `0037_front_desk_scheduling_checkpoints.sql` | the Scheduling graph's runs become durable (Phase 3 follow-up, owner decision of 2026-11-06 (a); BUILD-SPEC.md 7.2; CLAUDE.md rules 5, 6, 8, 10). |
| 0038 | `0038_backfill_review_task_function.sql` | a durable review task for a Scheduling run that needs a person (Phase 3 follow-up, owner decision of 2026-11-06 (b); BUILD-SPEC.md 7.2, 9; CLAUDE.md rules 5, 6, 8, 10). |
| 0039 | `0039_alerts_and_review_reason_codes.sql` | Phase 4 (BUILD-SPEC.md 12.2, 12.3, 12.4, 13.3, 13.4; CLAUDE.md rules 5, 6, 8, 10). |
| 0040 | `0040_reviewer_stats_function.sql` | per-reviewer approval rate and time-to-decide, for the admin only (Phase 4; BUILD-SPEC.md 12.4; CLAUDE.md rules 6, 8, 10). |
| 0041 | `0041_checkpoint_policies_require_the_slot.sql` | closes the residual risk stated in 0037 (reviewer finding, 2026-11-06; owner decision 2026-11-06 item 6; BUILD-SPEC.md 7.2; CLAUDE.md rules 8, 10). |
| 0042 | `0042_canary_document_function.sql` | lets the queue worker register a CANARY referral document (Phase 4 follow-up, owner decision 2026-11-06 item 2; BUILD-SPEC.md 12.4; CLAUDE.md rules 5, 6, 8, 10). |
| 0043 | `0043_canary_document_daily_cap.sql` | a daily cap on canary documents per clinic (owner decision 2026-11-06 item 4; BUILD-SPEC.md 12.4; CLAUDE.md rules 5, 8, 10). Fix-forward for 0042, which is merged and untouched. |
| 0044 | `0044_care_gaps.sql` | the data behind the Care Gaps panel (Phase 9 follow-up, owner decision 2026-11-06 item 2; BUILD-SPEC.md 9.3, 12.6, 22.4, 24; CLAUDE.md rules 5, 6, 8, 10). |
| 0045 | `0045_voice_preference_sessions_transcripts.sql` | the database half of the in-app voice channel (Phase 8 follow-up, owner decision 2026-11-06 item 2; BUILD-SPEC.md 19.4, 23, 11.6, 9.4; CLAUDE.md rules 5, 6, 7, 8, 10). |
| 0046 | `0046_revoke_public_execute_on_guard_functions.sql` | fix forward for 0044 and 0045 (CLAUDE.md rule 10; no merged migration is edited). |
| 0047 | `0047_residual_fixes_aal2_and_voice_admin.sql` | (owner decisions of 2026-11-06, items D, E, F; BUILD-SPEC.md 9.4, 18.1; CLAUDE.md rules 5, 8, 10). Fix forward: no merged migration is edited. |
| 0048 | `0048_autonomy_setting_care_gap_producers_sampler.sql` | (owner decisions of 2026-11-06, item C; BUILD-SPEC.md 9.3, 12.1, 12.5, 12.6, 24; CLAUDE.md rules 5, 8, 9, 10). Fix forward from 0044: no merged migration is edited. |
| 0049 | `0049_patient_portal.sql` | the patient-portal backend's database half (owner decision of 2026-11-06 item B; BUILD-SPEC.md 17.2, 18.4, 20.1; docs/design/deferred-items.md; CLAUDE.md rules 3, 5, 6, 7, 8, 10). |
| 0050 | `0050_clinical_results_care_plans_readiness_topic.sql` | (owner decisions of 2026-11-06 items A and C; BUILD-SPEC.md 9.5, 22.5, 17.5, Phase 6; CLAUDE.md rules 3, 5, 6, 8, 10). |
| 0051 | `0051_review_fixes_portal_resolution_worker_wake.sql` | (owner decisions of 2026-11-07; BUILD-SPEC.md 12.6, 17.2, 18.1; CLAUDE.md rules 3, 5, 8, 10). Fix forward from 0047 to 0050: no merged migration is edited. |
| 0052 | `0052_visit_readiness.sql` | (owner decision of 2026-11-07, item 1; BUILD-SPEC.md 22; CLAUDE.md rules 1, 3, 5, 6, 8, 10): the storage behind the Visit Readiness agent, to mechanism level. |
| 0053 | `0053_notice_lockdown_worker_results_two_person_autonomy.sql` | (owner decisions of 2026-11-07/08; BUILD-SPEC.md 12.1, 12.5, 18.1, 20.1; CLAUDE.md rules 3, 5, 8, 10). Fix forward: no merged migration is edited. |
| 0054 | `0054_autonomy_switch_admin_and_doctor.sql` | (owner decision of 2026-11-08: follow BUILD-SPEC.md 12.5; CLAUDE.md rules 8, 10). Fix forward from 0053: no merged or earlier migration is edited. |
| 0055 | `0055_care_plans_insert_id.sql` | fix forward for 0050 (found by CI run 42336238364; CLAUDE.md rule 10). |
| 0056 | `0056_erase_and_rotate_care_plan_text_and_results.sql` | (owner decision of 2026-11-08; BUILD-SPEC.md 9.4, 17.2, 20.1; CLAUDE.md rules 5, 6, 8, 10). Fix forward: no merged migration is edited. |
| 0057 | `0057_erase_messages_and_close_care_plans.sql` | (owner decisions of 2026-11-08; BUILD-SPEC.md 9.4, 17.2, 20.1; CLAUDE.md rules 5, 6, 8, 10). Fix forward from 0056: no merged migration is edited. |
| 0058 | `0058_break_glass.sql` | (owner request 2026-11-08; BUILD-SPEC.md 18.4, 12.1; CLAUDE.md rules 5, 6, 8, 10). |
| 0059 | `0059_patient_rights_grievances_and_export.sql` | (owner request 2026-11-08; BUILD-SPEC.md 19.3, 20.1, 17.5; CLAUDE.md rules 5, 6, 8, 10). |
| 0060 | `0060_staff_recovery_two_admins.sql` | (owner request 2026-11-08; BUILD-SPEC.md 18.1: "Staff account recovery goes through an admin plus a second admin — no self-service bypass of MFA"; CLAUDE.md rules 5, 8, 10). |
| 0061 | `0061_result_corrections_and_clinical_notes.sql` | (owner request 2026-11-08; BUILD-SPEC.md 9.5, 9.6; CLAUDE.md rules 5, 6, 8, 10). |
| 0062 | `0062_break_glass_reopen_cap_and_patient_stages.sql` | (owner decisions of 2026-11-08; BUILD-SPEC.md 18.4, 17.5; CLAUDE.md rules 5, 8, 10). Fix forward from 0058 and 0059: no merged migration is edited. |
| 0063 | `0063_scheduled_voice_transcript_purge.sql` | (owner request 2026-11-08; BUILD-SPEC.md 9.4, 23; CLAUDE.md rules 5, 8, 10). |
| 0064 | `0064_indexes_for_the_main_queries.sql` | (owner request 2026-11-08). A static review of the SQL the application runs per request or per tick against the indexes the migrations created found the gaps below. Every index |
| 0065 | `0065_hardening_from_the_set_review.sql` | (owner request 2026-11-08; CLAUDE.md rules 5, 8, 10). A review of migrations 0029 to 0062 read AS ONE SET (interactions between migrations, not file by file) found the |
| 0066 | `0066_break_glass_limits_in_the_database.sql` | (owner decision of 2026-11-08; the same move 0047 made for the canary cap; CLAUDE.md rules 8, 10). Fix forward from 0062: no merged migration is edited. |
| 0067 | `0067_erase_visit_readiness_rows.sql` | (owner decision of 2026-11-08; BUILD-SPEC.md 20.1; CLAUDE.md rules 8, 10). Fix forward. |
| 0068 | `0068_guards_survive_replica_mode.sql` | (owner decision of 2026-11-08; BUILD-SPEC.md 16.5; CLAUDE.md rule 10). Fix forward. |
| 0069 | `0069_clinic_origin_only_for_members.sql` | (owner decision of 2026-11-08; CLAUDE.md rules 8, 10). Fix forward from 0048: no merged migration is edited. |
| 0070 | `0070_public_clinic_and_break_glass_pick_lists.sql` | (owner request 2026-11-10; frontend requests 13 to 15; CLAUDE.md rules 5, 7, 8, 10). |
| 0071 | `0071_retention_report.sql` | (owner decision 2026-11-10: a report-only retention job; BUILD-SPEC.md 9.4; CLAUDE.md rules 3, 5, 8, 10; BUILD-SPEC.md 20.2). |
| 0072 | `0072_red_flag_rule_approval.sql` | (owner instruction 2026-11-13; BUILD-SPEC.md 5.2, 12.5, 20.2; CLAUDE.md rules 1, 2, 3, 5, 8, 10; closes the MECHANISM half of pre-launch item 19). |
| 0073 | `0073_front_desk_supervisor_and_revoke_alert.sql` | (owner instruction 2026-11-13; pre-launch item 23; docs/design/identity-confirmation-revoke.md; CLAUDE.md rules 1, 3, 5, 8, 10). Fix forward: no merged migration is edited. |
| 0074 | `0074_clinical_medications.sql` | (owner instruction 2026-11-13; BUILD-SPEC.md 9.5; CLAUDE.md rules 1, 3, 5, 6, 8, 10). |
| 0075 | `0075_session_spend_ledger.sql` | (owner instruction 2026-11-13; docs/design/spend-ledger-options.md option (b2); BUILD-SPEC.md 6.1, 15.2; CLAUDE.md rules 5, 8, 10, 13; pre-launch item 13). |
| 0076 | `0076_red_flag_rule_authoring.sql` | (owner decision 2026-11-13, item 3; BUILD-SPEC.md 5.2, 12.5, 20.2; CLAUDE.md rules 1, 2, 5, 8, 10). Fix forward from 0072; no merged migration is edited. |
| 0077 | `0077_red_flag_draft_withdrawal.sql` | (owner decision 2026-11-13, item 6; BUILD-SPEC.md 20.2; CLAUDE.md rules 1, 5, 8, 10). Fix forward from 0072 and 0076; no merged migration is edited. |
| 0078 | `0078_red_flag_rule_retirement.sql` | (owner decision 2026-11-13; BUILD-SPEC.md 12.5, 20.2; CLAUDE.md rules 1, 3, 5, 8, 10). Fix forward from 0072, 0076 and 0077; no merged migration is edited. |
| 0079 | `0079_red_flag_decision_replay_words.sql` | (owner decision 2026-11-13; fix forward from 0078; CLAUDE.md rules 3, 10). The whole-set review found that a doctor who recorded an approve on a draft that was later WITHDRAWN (or an |
| 0080 | `0080_patient_portal_profile_notifications_requests_public_grievance.sql` | (owner decision 2026-11-14 on frontend requests 20, 22, 24 and 26; BUILD-SPEC.md 19.3, 19.4, 20.1; CLAUDE.md rules 1, 3, 5, 6, 8, 10). |
| 0081 | `0081_consent_purpose_voice_session.sql` | (owner decision 2026-11-14 on frontend request 31; BUILD-SPEC.md 20.1, 23.4; CLAUDE.md rules 1, 3, 10). |
| 0082 | `0082_patient_messaging.sql` | (owner decision 2026-11-14, frontend request 29; BUILD-SPEC.md 17.5, 5.2, 11.5; CLAUDE.md rules 1, 2, 3, 5, 8, 10). |
| 0083 | `0083_patient_booking.sql` | (owner decision 2026-11-14, frontend request 28; BUILD-SPEC.md 17.5, 8.2; CLAUDE.md rules 1, 3, 4, 5, 8, 10). |
| 0084 | `0084_patient_booking_hardening.sql` | (whole-set review of 0079 to 0083, 2026-11-14; fix forward from 0083; CLAUDE.md rules 8, 10). |

## 7. What is still fake or undecided

The full list with a reason each is `docs/gap-audit.md` (kept current: FAKE, NOT BUILT, DECISION, UNMEASURED). The launch conditions are `docs/design/pre-launch-checklist.md` (numbered rows with an owner and closing evidence). In one paragraph: no real model, vision, speech, embedding or vector store is wired (fakes behind ports); no e-mail or SMS provider; no malware scanner; hosted observability (Langfuse, Grafana) is fake by owner decision; production hosting, India region and backups are undecided; all clinical content (red-flag rules, protocol cards, intake questions, thresholds, care-gap rules, medication codes) and all legal text (consent notices, grievance procedure, retention periods, breach wording) is missing by design and waits for named clinicians and counsel (the AI-prepared drafts and option sheets are in `docs/drafts/` and `docs/approval-pack/`, none approved). Nothing here may be marked approved by the build.

## 8. Reading order for a new developer

`CLAUDE.md`, then `BUILD-SPEC.md` sections 1 to 9 and Appendix D, then `docs/gap-audit.md`, `docs/design/pre-launch-checklist.md`, `docs/design/threat-model.md`, `docs/design/grants-ledger.md`, then the design note of the area you are changing (`docs/design/`).
