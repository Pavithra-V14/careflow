# CareFlow architecture overview

For a new engineer or an auditor. It says what is true of the code on `main` and points at the detailed documents instead of copying them. **Nothing here is approved for real patients**; the launch conditions are in `docs/design/pre-launch-checklist.md` and the honest status of every requirement is in `docs/spec-sweep.md`. The specification is `BUILD-SPEC.md`; the 13 rules every change is held to are in `CLAUDE.md`.

## 1. What it is

A backend for one outpatient clinic (many clinics are isolated by the database, not by application code). It reads referrals, screens them for danger with deterministic rules, proposes a protocol match, schedules and re-prioritises appointments, and follows care plans. **It never decides anything a wrong answer could hurt someone over: every clinical judgement is a clinician-authored deterministic rule or a human decision.** The routes number about 170 (`openapi.snapshot.json`): about 130 staff, 30 patient portal, a few public.

## 2. Components

| Part | Where | Notes |
|---|---|---|
| API | `careflow/app.py` (FastAPI, async, Pydantic v2) | Verifies the Supabase token per request (`careflow/auth.py`, key chosen by `kid`), sets the verified claims in the transaction (`careflow/db.py`), then runs hand-written SQL. Routes are grouped `/v1/staff/*`, `/v1/portal/*`, `/public/*`, `/readyz` |
| Database | `db/migrations/` (98 plain SQL files), `careflow/migrate.py` | Hosted Supabase Postgres. Checksummed ledger, advisory lock, append-only. The application connects as the restricted role `careflow_app` through the transaction-mode pooler; the owner role is for migrations and owner-run scripts only |
| Identity and auth | Supabase Auth; `iam.*` tables | Staff: invitation only, second factor (aal2) on every staff route and in the database. Patients: phone code (needs an SMS provider that is not approved); e-mail + password exists as a demo shortcut only |
| Queue and worker | `careflow/queue.py`, `tick.py`, `producers_sql.py` | Postgres tables, a minute tick plus an immediate poke, 5 attempts then dead letter. A second adapter stub exists and is not wired (`queue_broker_stub.py`) |
| Agents (graphs) | `front_door.py`, `scheduling_graph.py`, `care_journey.py` (flag), `visit_readiness.py` (flag) | LangGraph with our own Postgres checkpointer (`checkpointer.py`). Voice is a channel, not an agent (`voice.py`) |
| Deterministic safety rules | `careflow/rules.py` | Phrase, pattern and token-set rules a clinician authors and two doctors approve (`rule_approval_sql.py`, migrations 0072, 0076 to 0079). Run before any model. **No rule ships**: the repository holds none |
| Model access | `gateway.py`, `registry.py`, `decision.py`, `adapters/` | Every model call goes through a gateway that enforces the data policy, ceilings and breakers. Hosted adapters (Gemini, Jev through OpenRouter, Sarvam) exist for the synthetic demo only |
| Retrieval | `retrieval.py`, `knowledge.py`, `knowledge_sql.py`, migration 0085 | BM25 and dense search fused; dense search is pgvector in Postgres (a deviation, section 9) |
| Knowledge editor | `knowledge_editor_sql.py`, migrations 0090 to 0093 | Draft, edit, submit, a different doctor approves; immutable versions; the worker indexes approved cards |
| Observability | `observability.py`, `watchdog.py`, `metrics_catalog.py`, `metrics-catalog.yaml` | In-app alerts are real and need no third party; hosted tracing and metrics are fakes |
| Frontend | `frontend/` (separate workstream) | Contract: `openapi.snapshot.json`, `docs/frontend-backend-requests.md` |

## 3. From a patient letter to a human decision

1. **Upload.** `documents.py`: a signed upload, server-side re-verification of type, size and existence, registration in `docs.*`. Real uploads are refused outside the demo (no malware scanner is wired; checklist row 2).
2. **Run.** A staff member starts a run (`POST /v1/staff/referrals/{id}/run`); an idempotent job is queued (`front_door_runner.py`); the worker picks it up.
3. **Screen first.** The `screen` node (`front_door.py`, `referral.py`) reads the letter text and runs the deterministic rules. A red flag raises a nurse task at once and ends the run. A letter the rules cannot vouch for is `unassessable`, **never clear**, and goes to a person. Only `clear` mints a `Clearance`; the model-backed extractor cannot be called without one (`referral.Clearance`, checked by `ReferralExtractor.extract`). The one narrow carve-out: transcribing a scanned page into text may precede the rules, and its output is screened before anything else reads it (`extraction.py`).
4. **Extract and ground.** Only now may a model read the letter (`extraction.py`, `grounding.py`): structured values are compared canonically, interpretive findings need an entailment check, a failed check means "unverified", which satisfies no rule.
5. **Identity.** `mpi.py`, `identity.py`: encrypted demographics, keyed hash tokens, a near-match is a flagged new record, never a silent merge.
6. **Match.** Card retrieval (`knowledge.py`) abstains below a threshold nobody has calibrated; abstaining is a normal outcome and goes to a human.
7. **Propose.** `triage_store.py` writes a triage proposal and a review task, plus the decision provenance (ids and versions of what was read, `provenance_sql.py`, migration 0095).
8. **Pause.** The `review` node waits (an `interrupt`) with no side effect. Nothing is approved by timeout.
9. **Human decision.** A nurse or doctor records approve, edit or reject (`POST /v1/staff/referrals/{id}/review`), with a reason code from the clinic's list and an optional escalation assessment (0094). The decision row is append-only and names the human (`agents.review_decisions`, migration 0018). Auto-approval exists only as mode-dependent policy (`autonomy.py`); the default mode is `shadow`.
10. **After.** Overdue tasks escalate up the chain (`review_console.py`, `watchdog.py`); canaries and sampling watch for rubber-stamping (`canary.py`, `reviewer_stats.py`).

## 4. Where each CLAUDE.md rule is enforced

| # | Rule | Enforced by |
|---|---|---|
| 1 | Never invent clinical content | No rule, card or threshold in the repository (`kb/` is empty); required settings with no default (`docs/config-reference.md`); clinical content is `draft_unreviewed` until two-person approval (migrations 0007, 0072, 0090); demo values carry labels (`config/demo-phase4.yaml`, `docs/demo-placeholders.md`); `tests/test_demo_placeholders.py` keeps clinical words out of the seed |
| 2 | Red-flag rules before any model | `careflow/rules.py`; `referral.Clearance`; every text path screens first (`front_door.py`, `messages.py`, `voice.py` with `StreamEvaluator`); the database refuses an unscreened patient message (migration 0082); `scripts/demo_smoke.py` re-checks the structure |
| 3 | Unknown is not a value | `unassessable` is not clear (`rules.py`); an unverified fact satisfies no rule (`grounding.py`); family links, patient features and every owner setting answer "not configured" when unset; `careflow/placeholder_check.py` refuses to start when it cannot rule placeholders out |
| 4 | A pausing node has no side effect | `careflow/graph_structure.py`, run by `tests/test_front_door_structure.py`, `test_scheduling_graph_structure.py`, `test_care_journey_structure.py`, `test_visit_readiness_structure.py` |
| 5 | Every write has an idempotency key | The `IdempotencyKey` dependency on every write route in `app.py`; keys inside the definer functions (actor-bound, for example migrations 0090 to 0093); the role matrix has a "missing key is rejected" case per write route |
| 6 | No agent-owned memory of patients | There is no such table; durable data is clinical record, operational state, versioned knowledge or checkpoints (`checkpointer.py`); `docs/design/threat-model.md` |
| 7 | Patient data never leaves except by an approved call | `data-flow-register.yaml` (checked by `tests/test_data_flow_register.py`); the gateway filters by data policy (`gateway.py`); hosted providers are demo and synthetic only (`provider_setup.py`); `GET /readyz` is not ready in `clinical` without an approved route (`readiness.py`) |
| 8 | Row-level security, not application code | Forced RLS from the creating migration of every clinic-scoped table; definer functions that check `auth.uid()`, role and aal2; `careflow_app` has no bypass (`scripts/create_app_role.py`, `verify_app_role.py`); `tests/test_api_role_matrix.py` runs against the real restricted role, with a case per route (`test_every_route_is_in_the_matrix`) |
| 9 | Auto-approved outputs never become labels | `careflow/learning_labels.py` (human decisions only; reviewer pseudonymised; about 5% sampled after the fact) |
| 10 | Never edit a merged migration | The checksummed ledger in `careflow/migrate.py` fails when an applied file changes |
| 11 | Voice is higher urgency, narrow scope | `careflow/voice.py` (scoped prompts, the rules on the live transcript in parallel, interrupt to the fixed response, a nurse task); migrations 0045, 0047, 0063; no engine is wired, the session route answers 503 |
| 12 | No Docker | `.github/workflows/ci.yml` uses hosted Supabase projects; `docs/first-run-guide.md` |
| 13 | Ask before a new external service | The data-flow register and the dependency audit gate (`scripts/audit_gate.py`, `ops/audit-ignore.yaml`); `docs/config-reference.md` is tested to list every setting; each service needs a recorded owner decision (`docs/design/pending-owner-ok-2026-11-04.md`) |

## 5. Authorization and the role matrix

Roles: `patient`, `front_desk`, `front_desk_supervisor`, `nurse`, `doctor`, `admin`, `business_owner`, `auditor`, `queue_worker` (the worker holds no other role, enforced by migration 0024). Enforced at three layers: the API checks identity then clinic and role then the feature switch (503) then the database; Postgres row-level security scopes by clinic and by patient relationship; the tool servers are split read and write (`mcp_kit.py`, `clinical_server.py`). **Deny by default**: a wrong role, a missing second factor and another clinic's record each get one fixed body (403 or 404), never a reason. The CI role matrix drives every route against every role and against an unauthenticated caller; a route without a case fails `test_every_route_is_in_the_matrix`. Break-glass (`break_glass_sql.py`) is narrow, reasoned, audited and reviewed. Detail: `docs/design/grants-ledger.md`, `docs/design/role-matrix-exceptions.md`, `docs/design/active-role.md`.

## 6. Demo-only versus clinical

| | `demo` (dev, synthetic data) | `clinical` |
|---|---|---|
| Profile | `CAREFLOW_PROFILE=demo` and `CAREFLOW_DATA_POLICY=synthetic_only` | Anything else; unset means clinical |
| Models | Fakes by default; hosted Gemini, Jev, Sarvam only when every gate holds (`CAREFLOW_DEMO_REAL_MODELS`) | No hosted route is approved; `readyz` says not ready; extraction becomes a manual task |
| Placeholders | Labelled demo values: `CAREFLOW_DEMO_PHASE3` and `CAREFLOW_DEMO_PHASE4` packs, `seed_demo_*` scripts | The application refuses to start if a demo switch is on or a labelled row exists (`demo_phase3.py`, `demo_phase4.py`, `placeholder_check.py`, `demo_email_signin.py`, `knowledge_editor_sql.RebuildRefused`) |
| Patient sign-in | Phone code, or e-mail + password for walks (`CAREFLOW_DEMO_EMAIL_SIGNIN`) | Phone code only, which needs an SMS provider that is not approved |
| Second factor | On; may be switched off only here, on purpose | On, and the application refuses to start with it off |
| Record view, uploads, vector rebuild | Allowed (synthetic) | Refused or off |

Details and commands: `docs/demo-runbook.md`, `docs/demo-placeholders.md`, `docs/config-reference.md`.

## 7. Data

Eleven schema families (`iam`, `mpi`, `sched`, `clinical`, `docs`, `msg`, `kb`, `agents`, `ai`, `ops`, `audit`). Demographics are encrypted (AES-256-GCM, a key version on every ciphertext) and looked up by keyed hash tokens (`crypto.py`). Clinical rows are bi-temporal where built (results, medications, notes); notes and decisions are append-only. The audit log is append-only and trigger-enforced. Retention, erasure and the purge: `docs/design/retention-and-erasure.md`.

## 8. Operations

Backups, key rotation, provider outages, the audit log and the first-patient checks: `docs/ops-runbook.md`. Drills have **not** been run (checklist rows 28 and 54).

## 9. Deliberate deviations from BUILD-SPEC.md

Recorded decisions, each with a dated note; the complete list with every other gap is `docs/spec-sweep.md`.

- **Default autonomy mode is `shadow`**, not `assisted` (the safer start; owner 2026-11-18).
- **Vectors are in Postgres with pgvector, not Qdrant** (owner 2026-11-14 and 2026-11-18; amendment notes in `BUILD-SPEC.md` sections 3 and 16.6; `docs/design/retrieval-pgvector.md`).
- **No cache for cards and red-flag rules** (spec 6.2b), so a withdrawn rule stops working immediately (owner 2026-11-18).
- **Vital signs and resource history are not built**: build when a feature needs them.
- **The pre-visit brief is not built**: asked of the clinician (sheet item 40).
- **Hosted observability, e-mail and chat senders, SMS, the malware scanner, a local decision model, Unstructured, Presidio and a FHIR library are not adopted**: each is a new external service or dependency that needs approval first.
- **Dev and CI projects are in Tokyo** with synthetic data only (an accepted deviation; production must be an India region).

## 10. Where to look next

`docs/status-now.md` (what changed lately), `docs/spec-sweep.md` (every requirement), `docs/design/pre-launch-checklist.md` (what must be true before real patients), `docs/design/threat-model.md` (what was reviewed and what was accepted), `docs/design/phase*-*.md` (one note per phase), `docs/runbooks/` (incidents).
