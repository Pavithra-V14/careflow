# Spec sweep (BUILD-SPEC.md against the code, 2026-11-18)

One row per requirement, section by section. **Built from the code, not from `docs/spec-compliance.md`**: every file, migration and test that file cites was checked to exist, and each row below was re-judged against the code and the tests. Status words:

- **built and tested**: covered by a CI test against the real database or the whole application.
- **built, offline test only**: code exists with unit tests against fakes; nothing exercises it against the real thing.
- **partial**: some of the requirement is built; the row says what is not.
- **not built**.

For partial and not built, the reason is one of: **deliberate decision**, **waiting on a person** (owner, clinician, counsel, privacy owner), **needs an external service** (or a new dependency), **simply missed**.

Nothing here is approved for real patients; see `docs/design/pre-launch-checklist.md`.

## Counts

Total rows: 173.

| Status | Rows |
|---|---|
| built and tested | 48 |
| built, offline test only | 51 |
| partial | 51 |
| not built | 23 |

| Partial or not built, by reason | Partial | Not built |
|---|---|---|
| deliberate decision | 9 | 3 |
| waiting on a person | 22 | 9 |
| needs an external service | 20 | 11 |
| simply missed | 0 | 0 |

## Found by this sweep

- **Built now:** 3.7, the unwired second queue adapter (`careflow/queue_broker_stub.py`, refuses every call, imports no broker library, nothing wires it; `tests/test_queue_broker_stub.py`). The old comment in `queue.py` said it was deliberately not built; the spec says to write it.
- **Simply missed, now built (2026-11-18):** 9.5c decision provenance (0095), 14.4b escalation assessment (0094), the 16.6 demo rebuild, the care-plan flow over HTTP (21.4) and the dependency gate (21.6). The rows say what remains.
- **Corrections to `docs/spec-compliance.md`:** none of its cited files or migrations is missing. Its card-schema rows (17.1, 20.2) predate migration 0090 and are updated here; its 'NOT BUILT' for 3.7 is now built.

## Decisions recorded 2026-11-18 (owner answers to the first sweep)

1. **6.2b rule cache:** not cached; a deliberate deviation, so a withdrawn red-flag rule stops working immediately.
2. **14.4b escalation reason codes:** built (migration 0094).
3. **9.5c decision provenance:** built (migration 0095).
4. **9.5 vitals and resource history:** build when a feature needs it.
5. **22.1 pre-visit brief:** not built; asked of the clinician (sheet item 40).
6. **16.6 vector index rebuild:** built for the demo profile only; refused in clinical.
7. **21.4 / 21.6:** the care-plan flow runs over HTTP in the live shards; the dependency audit is a gate with a written ignore list.
8. **1.5 default mode:** stays `shadow`; a deliberate deviation recorded here and in `docs/spec-compliance.md`.
9. **3.9 / 10.1 vectors:** pgvector stays; dated amendment notes added to `BUILD-SPEC.md` (original wording kept).

Still open: the decisions that belong to others (clinician sheet items, counsel, the privacy owner) and everything that needs an external service (see the rows).

## The rows


### 1. Product overview and scope

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 1.1 | Four agents in the stated order (Front Door, Scheduling, Care Journey, Visit Readiness); no fifth | built, offline test only |  | front_door.py, scheduling_graph.py, care_journey.py, visit_readiness.py; structure tests |
| 1.2 | Care Journey and Visit Readiness behind flags, off by default | built, offline test only |  | FEATURE_CARE_JOURNEY, FEATURE_VISIT_READINESS; feature_flags tests |
| 1.3 | Voice: clinic flag AND per-patient preference | built and tested |  | voice_sql.py, 0045/0047; test_voice*_live |
| 1.4 | Autonomy per clinic, three modes, never global | built and tested |  | autonomy.py, 0048/0053/0054; live tests |
| 1.5 | Default mode `assisted` | partial | deliberate decision | DELIBERATE DEVIATION (owner 2026-11-18): the built default is `shadow`, the safer start; the spec text is unchanged and says assisted. Recorded in spec-compliance.md |
| 1.6 | Locked: merge, non-template patient message, out-of-range readiness answer | built and tested |  | two-person merge live tests; no free-text patient send path; readiness test |
| 1.7 | Emergency template always automatic, nurse must acknowledge | partial | waiting on a person | Nurse task is automatic and tested; the patient-facing template wording is clinician content (pending_clinician_template) |
| 1.8 | Never diagnoses, treats, or free-text replies | built, offline test only |  | no such path; offers/replies are templates |

### 2. Operating profiles

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 2.1 | Every external call and model role declares data_policy | built, offline test only |  | registry.py, gateway.py, data-flow-register.yaml + test |
| 2.2 | `clinical` refuses to report ready without a phi_approved route per required role | built, offline test only |  | GET /readyz; readiness tests |
| 2.3 | Deterministic features full in both profiles | built and tested |  | rules, booking, queue need no model |
| 2.4 | Model-dependent capabilities degrade visibly in `clinical` | built, offline test only |  | front_door.py manual holds; 503 for unwired routes |
| 2.5 | Real clinical launch needs an approved LLM + vision route | not built | waiting on a person | Provider agreement; demo hosted adapters are synthetic-only |

### 3. Architecture

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 3.1 | FastAPI async, Pydantic v2, OpenAPI feeds contract test; snapshot staleness check | built and tested |  | test_openapi_snapshot.py; frontend check:api |
| 3.2 | SQLAlchemy Core async + asyncpg, hand-written SQL | built and tested |  | db.py |
| 3.3 | Plain SQL migrations, checksummed ledger, advisory lock, append-only | built and tested |  | migrate.py; 93 migrations; CI migrate job |
| 3.4 | FHIR R4-shaped JSON validated by a FHIR library | not built | needs an external service | New dependency (fhir.resources) and a modelling decision; results/medications are typed rows |
| 3.5 | LangGraph with durable Postgres checkpointing | built and tested |  | own asyncpg checkpointer, 0021; resume tests |
| 3.6 | Two MCP servers in process (scheduling read+write, clinical read-only) | built, offline test only |  | mcp_kit.py, clinical_server.py; the clinical server has little chart data |
| 3.7 | Postgres queue behind QueuePort; second unwired Redis/SQS adapter stubbed | built, offline test only |  | Queue built and live-tested. The unwired stub `queue_broker_stub.py` was MISSED earlier and is built in this sweep (no broker library imported) |
| 3.8 | React + Vite + TypeScript, Vitest, contract test | built, offline test only |  | Frontend workstream (not assessed here) |
| 3.9 | Vectors in Qdrant | partial | deliberate decision | DELIBERATE DEVIATION: pgvector instead (owner 2026-11-14, confirmed 2026-11-18); dated amendment notes added to BUILD-SPEC.md 3 and 16.6, the original wording kept |

### 4. Use-case and agent inventory

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 4.1 | Structural test: topology matches inventory; no pause with side effect | built, offline test only |  | graph_structure.py; tests/test_*_structure.py |

### 5. Models and decision engines

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 5.1 | Model registry rows: provider, pinned id, data_policy, priority, price incl. free + shadow | built and tested |  | 0015; registry.py |
| 5.1b | Voice role priced per audio token with its own unit | partial | waiting on a person | Registry has no audio-token unit; the real per-minute figure is not measured (Appendix B), so a unit would hold a made-up number |
| 5.1c | Real adapters for the roles | partial | needs an external service | Fakes default; hosted Gemini/Jev/Sarvam merged for SYNTHETIC demo only |
| 5.1d | llm_judge differs from the generator | built, offline test only |  | evalmetrics.judge_may_gate |
| 5.2 | DecisionPort with Laya/Jev/LLM adapters; engine and confidence recorded | partial | needs an external service | No local Laya model; Jev demo-verified, synthetic only; confidence never calibrated |
| 5.2b | PHI chain never reaches Jev | built, offline test only |  | gateway filter + adapter refusal tests |
| 5.2c | Three-engine contract test (hard gate) | not built | needs an external service | Needs real engines + clinician-reviewed set |
| 5.2d | Laya: <=20 options; per-question calibration; route by script | partial | waiting on a person | 20-option cap and script routing are code; calibration needs labelled data |
| 5.2e | Red-flag rules first on every path; a model can only escalate | built and tested |  | rules.py; every text path; live message/voice/front-door tests |
| 5.2f | Vision carve-out: transcription only, screened first, failure never clear | built, offline test only |  | extraction.py, front_door.py |
| 5.2g | Safety-net second opinion, escalate-only, fail closed to a nurse | not built | waiting on a person | Needs a clinician-approved design of what the second opinion asks; no decision engine runs |
| 5.3 | Model deprecation job: list, compare, auto-disable, alert | partial | needs an external service | Comparison logic built (spend_policy.py); the scheduled job needs each provider's listing call (Appendix B) |

### 6. Gateway and caching

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 6.1 | In-process gateway: routing, fallback, ceilings, breakers | built, offline test only |  | gateway.py (breaker 10 failures / 60 s verified) |
| 6.1b | Cloudflare AI Gateway pass-through | not built | needs an external service | External service not approved |
| 6.1c | Ceilings 12/25/40/40s/$1; red-flag path never limited | built, offline test only |  | budget.py, tick.py |
| 6.2a | Prompt-prefix stability; no PHI in prompts; CI scan | built, offline test only |  | prompts.py, prompt_scan.py, test_prompt_scan.py |
| 6.2b | Exact-match cache: card/rule bodies 5 min, ICD 30 d, model lists 24 h | not built | deliberate decision | DELIBERATE DEVIATION (owner 2026-11-18): rules and cards are NOT cached, so a withdrawn red-flag rule stops working immediately. Embeddings are content-hashed (knowledge_sql.content_hash) |
| 6.2c | Semantic cache of de-identified card queries | not built | needs an external service | Needs a hosted embedding route in clinical; none approved |
| 6.2d | Request-scoped memoisation | built, offline test only |  | RunMemo; no tool uses it yet |
| 6.2e | Gateway response cache off for patient text | not built | needs an external service | No pass-through exists, so nothing to switch off |

### 7. Orchestration

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 7.1 | Deterministic routing; an LLM only proposes a care-plan task graph | built, offline test only |  | care_journey.py; no model route, so every plan is held for a doctor |
| 7.2 | Checkpoint every step by thread_id; resume without replay | built and tested |  | checkpointer.py; crash-resume tests |
| 7.2b | Pausing node never has a side effect (structural test) | built, offline test only |  | graph_structure.py |
| 7.2c | Idempotency key on every external side effect | built and tested |  | keys on every write route/definer; chaos + idempotency suites |
| 7.3 | Ceilings enforced: 40/25/12/40s, 5 retries then dead-letter | built and tested |  | budget.py, queue.py, dead_letters; test_dead_letters_live |
| 7.4 | Queue: visibility timeout, retries, dead letter, minute tick + poke | built and tested |  | queue.py, tick.py, 0006 |
| 7.4b | Outbox producers (event to job) | partial | deliberate decision | Producers exist for document runs and results; others are per-producer design decisions |

### 8. Tools and function calling

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 8.1 | Read tool cannot write (server split) | built, offline test only |  | mcp_kit.py, clinical_server.py |
| 8.2 | Write pipeline: schema, authorization, policy, budget, idempotency | built, offline test only |  | write_pipeline.py, scheduling_write_tools.py |
| 8.3 | Explicit-status results, never a bare empty list | built, offline test only |  | Envelope; OfferDraftOut |
| 8.4 | Load-bearing vs decorative per tool per workflow | built, offline test only |  | declared in config where tools exist |

### 9. Memory

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 9.1 | No agent-owned patient memory; four durable classes | built, offline test only |  | no such store exists; threat-model; CLAUDE.md rule 6 |
| 9.2 | Last turns + typed state; priority-ordered context; drops logged | built, offline test only |  | context_assembler.py; no real model call uses it yet |
| 9.3 | Care episodes and recurrence rules | built and tested |  | care_gaps.py, 0044/0048; live tests. Rule definitions are a clinician setting |
| 9.4 | Retention periods and a scheduled, audited deletion job | partial | waiting on a person | Report-only retention job (0071), voice purge, audited erasure; periods are counsel's/clinical lead's; ledgers excluded by owner decision |
| 9.5 | Bi-temporal clinical rows (vitals, labs, meds, notes) | partial | deliberate decision | Results, medications, notes built (0050/0061/0074). Vital signs and resource history: BUILD WHEN A FEATURE NEEDS THEM (owner 2026-11-18) |
| 9.5a | Correction: block unacted decisions; review decisions acted on within 30 days | partial | deliberate decision | Built reviews EVERY acted-on decision (stricter than the 30-day line); the spec's 30 days is 'tune with clinical input' |
| 9.5b | Amendment vs correction declared by the writer, inferred otherwise | built and tested |  | 0061; live tests |
| 9.5c | Every decision records versioned ids read, card version, rule ids, engine | built and tested |  | Migration 0095 `agents.decision_provenance` (document, patient, cards with versions, rule-set hash; ids and versions only, append-only, forced RLS), written by the triage proposer; engine, model and confidence stay on the proposal; live test in test_rls_triage_live. Care-plan and scheduling decisions do not write it yet: LATER, WHEN NEEDED (owner 2026-11-19) |
| 9.6 | Notes append-only, structure-aware splitting, content-addressed embedding cache, sync embed on save | partial | needs an external service | Append-only built and tested (0061); splitting/embedding of notes needs an embedding route |

### 10. Retrieval

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 10.1 | Own pipeline: BM25, RRF, hosted reranker | partial | needs an external service | BM25, RRF, pgvector, assembler built; reranker is a port with a fake |
| 10.2 | Card text from structured fields; notes wrapper makes an unscoped query impossible | built, offline test only |  | retrieval.py, note_search.py (fake store) |
| 10.2b | Card and template editor (draft, edit, submit, two-person approve, versions) | built and tested |  | 0090/0092/0093; knowledge_editor_db_live |
| 10.3 | Section-aware chunking, context line, small-to-big | not built | needs an external service | Needs Unstructured + an embedding route; real-PDF spike open |
| 10.4 | Tiered extraction: pypdf, Unstructured, Gemini | partial | needs an external service | pypdf and ports built; Unstructured not wired; Gemini is a synthetic demo adapter |
| 10.5 | Embeddings: local primary + hosted hedge | partial | needs an external service | Local fake default; Gemini (synthetic) demo adapter; no approved local model |
| 10.6 | Reranker with fallback chain (decorative) | partial | needs an external service | Fusion order is the live fallback |
| 10.7 | Deterministic fields gate; model tags only rank | built, offline test only |  | nothing model-derived gates access |
| 10.8 | Abstention; version mismatch discarded at the join; nightly rebuild | partial | waiting on a person | Mechanism built; the calibrated threshold is data nobody has; nightly rebuild job not scheduled |
| 10.9 | Retrieval quality gate in CI (top-1, recall@3, abstention, false routing) | partial | waiting on a person | Harness built (retrieval_eval.py); needs a clinician-reviewed gold set and thresholds |

### 11. Guardrails and safety

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 11.1 | Quoted data; injection classifier (decorative) | partial | waiting on a person | Heuristic text_guard built; hard gate needs a clinician adversarial set |
| 11.2 | Tiered grounding; failed means unverified; never fuzzy-match a number | built, offline test only |  | grounding.py |
| 11.2b | Guardrails AI validators | not built | needs an external service | New dependency not approved; tiers are own code |
| 11.2c | Source-image crop beside image-derived values | not built | needs an external service | Needs bounding boxes from a real vision parser |
| 11.3 | Presidio pattern recognizers; minimisation after identity extraction | partial | needs an external service | Heuristic masker built; Presidio not adopted |
| 11.4 | Clinical question goes to a human; never answered | built, offline test only |  | reply_interpretation.py; patient message screening live-tested |
| 11.5 | Abuse limits: rate limits, upload allowlists, malware scan | partial | needs an external service | Allowlists and rate limit built; ClamAV client built, no daemon; real uploads refused outside demo |
| 11.6 | Voice: rules on the live transcript in parallel; interrupt; nurse task | built, offline test only |  | voice.py, StreamEvaluator; interrupt gap unmeasured (no engine) |

### 12. Human approval

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 12.1 | Approval matrix per mode and action class (config) | built and tested |  | autonomy.py; live tests |
| 12.2 | Reviewer view: proposal, evidence, alternatives, engine, rule; reason picklist | partial | deliberate decision | Built; free-text note not stored by owner decision; image crop not built (see 11.2c) |
| 12.3 | Deadlines, escalation up the chain, nothing approved by timeout | built and tested |  | review_console.py, watchdog.py; live tests |
| 12.4 | Canaries, silent alert, rubber-stamp monitoring | built and tested |  | canary.py, reviewer_stats.py; 0039/0040/0042 |
| 12.5 | Two-person rules: merge, rule publish/retire, autonomous, erasure | built and tested |  | 0029/0032/0054/0072/0076-79; card publishing now via 0090 |
| 12.6 | Labels from human decisions only; ~5% after-the-fact sample | partial | waiting on a person | Mechanism built; no export job; no auto-approval path yet so nothing is sampled |

### 13. Observability

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 13.1 | Langfuse traces, profile-gated export | not built | needs an external service | In-memory sink only; owner decided hosted services stay fakes |
| 13.2 | Grafana via OpenTelemetry; label discipline; daily rollup | partial | needs an external service | Label checks and generated rules exist; no exporter; no rollup table |
| 13.3 | In-app watchdog independent of third parties | built and tested |  | watchdog.py; live tests |
| 13.4 | Alert routing; class + count + opaque id only | partial | needs an external service | In-app real; e-mail and Slack senders are ports with fakes |
| 13.5 | Per-call, tool and step capture | built, offline test only |  | observability.py |
| 13.6 | Origin tags; Live/Test split; CI check that Live queries filter origin | built and tested |  | metrics_catalog.py; test_metrics_catalog; dashboard route live tests. No dashboard page here (frontend) |

### 14. Evaluation

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 14.1 | Gold set: reviewed only gates; held-out; Tamil separate | partial | waiting on a person | Mechanism built; no clinician-reviewed case exists |
| 14.2 | pytest gates; DeepEval; Langfuse experiments | partial | needs an external service | pytest built; DeepEval and Langfuse are new services/dependencies |
| 14.3 | Hard gates incl. red-flag recall 100% | partial | waiting on a person | Runner scores the real engine; bars, cases and rules are clinician-owned |
| 14.3b | Threshold gates with variance band | built, offline test only |  | evalmetrics.py; bars are required settings |
| 14.4 | Metric source discipline; grounding rate; three-part latency | built, offline test only |  | functions built and tested |
| 14.4b | Two reason codes on every reviewed task (unnecessary escalation / missed by system) | built and tested |  | Migration 0094: optional closed `escalation_assessment` on `agents.review_decisions` (null = not recorded), set on the referral review route; live test in test_rls_triage_live and role-matrix cases. Only the referral review writes decisions today |
| 14.5 | Business KPIs with owner-set targets | not built | waiting on a person | Needs a baseline and the owner's numeric targets |
| 14.6 | Pre-deployment go/no-go scorecard | partial | waiting on a person | Checker built; every evidence cell empty (NO-GO) |
| 14.7 | Automatic demotion; promotion evidence gates | partial | waiting on a person | Demotion built and tested; promotion needs months of data |

### 15. Cost

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 15.1 | Cost canvas from real metrics, actual and shadow | built, offline test only |  | cost_canvas.py; needs a real week of traffic |
| 15.2 | Caps; 80% soft threshold sheds decorative features in order; safety never limited | built, offline test only |  | spend_policy.py, gateway.py; the shed features have no live callers yet |
| 15.2b | Durable spend ledger | built and tested |  | 0016, 0075; live tests |
| 15.3 | Cheap-then-expensive cascade; per-failed-page vision billing | partial | needs an external service | Decision-tier cascade only; no per-page billing without a real vision route |
| 15.4 | Attribution tags; failure multiplier; weekly tail review to admin | partial | needs an external service | Functions built; weekly delivery needs the Slack sender |

### 16. Reliability, security, deployment

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 16.1 | Backoff+jitter, 3 sync attempts, 5 queue attempts, breakers, timeouts | built, offline test only |  | gateway.py, queue.py |
| 16.1b | An idempotency gap blocks a release | built and tested |  | idempotency/chaos suites; role-matrix header checks |
| 16.2 | Degradation matrix | built, offline test only |  | each row degrades visibly; static emergency notice is frontend |
| 16.3 | Split topology; worker machine; SLOs; keep-alives | not built | waiting on a person | Hosting, region, worker machine are the owner's |
| 16.4 | Rollout ladder with change control and on-call | not built | waiting on a person | Process; docs/runbooks drafts only |
| 16.5 | Secrets never in logs/VCS; scanning; least privilege; append-only audit | built and tested |  | gitleaks in CI; restricted role; audit triggers; live tests |
| 16.5b | Database in an India region; PHI only to approved calls | not built | waiting on a person | Dev/CI are Tokyo and synthetic; production region is the owner's |
| 16.5c | Encrypted backups, PITR, quarterly restore drill | not built | waiting on a person | Checkers and runbook built; no drill run (drills.py). Backups are Supabase's; see ops-runbook |
| 16.5d | Breach runbook with 72-hour window | partial | waiting on a person | Skeleton; contacts and wording are the owner's and counsel's |
| 16.6 | Vector store safeguards: ids-only payloads, one query wrapper, verified erase, nightly reconcile | partial | needs an external service | Wrapper, ids-only payloads and verified delete built over the port. The nightly reconcile is built for the DEMO profile only (`CAREFLOW_VECTOR_REBUILD=on`, refused in clinical; owner 2026-11-18); in clinical it needs an approved embedding route |

### 17. Data model, identity, infrastructure

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 17.1 | Eleven schema families with their tables | partial | waiting on a person | kb card tables exist since 0090 for editor content; match log, calibration, ICD cache do not (clinician content / external); no vitals or resource history |
| 17.1b | RLS keyed on auth.uid(); forced RLS; restricted role | built and tested |  | role matrix against the real role; every migration |
| 17.2 | MPI: encrypted demographics, HMAC tokens, near-match flag, merges and unmerge | built and tested |  | mpi.py, identity.py, crypto.py; live tests |
| 17.2b | Key versioning; documented manual rotation | built, offline test only |  | rotation.py, docs/runbooks/key_rotation.md |
| 17.3 | Documents pipeline end to end | built, offline test only |  | documents.py, front_door.py; storage overwrite behaviour unverified (B.16) |
| 17.4 | Prompts versioned+hashed; calibration with approver | partial | waiting on a person | Prompts built; calibration data does not exist |
| 17.5 | Messaging topics incl. readiness | built and tested |  | 0011/0050 |
| 17.5b | Account states enforced server-side per feature | partial | deliberate decision | Derived stages enforced for messaging, booking, export, visit prep; other patient features not built |
| 17.6 | Data-flow register as a literal table | built, offline test only |  | data-flow-register.yaml + test |
| 17.7 | Local models (Laya, Unstructured, local embedding) | not built | needs an external service | No local model installed |

### 18. Authentication and authorization

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 18.1 | JWT verified per request by kid; set_config; pooler rules | built and tested |  | auth.py, db.py, verify_app_role.py; CI |
| 18.1b | Staff invitation-only, mandatory second factor, step-up | partial | deliberate decision | TOTP (aal2) enforced in API and database; passkeys excluded by owner; invitation needs the unwired Auth admin call |
| 18.1c | Staff recovery through two admins | built, offline test only |  | recovery_sql.py, 0060; Auth admin port is a fake |
| 18.1d | Patient OTP, device list, CAPTCHA, guardian accounts | partial | waiting on a person | Hosted Auth settings; devices need the Auth admin API; family links built as mechanism (0091), granting nothing |
| 18.2 | SmsPort, DLT registration, mitigations, sandbox sink | partial | needs an external service | Send guard and empty template registry built; no provider, nothing registered |
| 18.3 | EmailPort with two adapters | partial | needs an external service | Port + fake only |
| 18.4 | RBAC at three layers; deny by default | built and tested |  | role matrix; RLS; tool scopes |
| 18.4b | Break-glass with reason, audit, review | built and tested |  | 0058/0062/0066; messages only |
| 18.4c | Short-lived signed per-job tokens between agents and tool servers | not built | deliberate decision | Servers run in process behind the worker identity; recorded in gap-audit |
| 18.4d | CI test: every route against every role, populated per phase | built and tested |  | test_api_role_matrix.py; test_every_route_is_in_the_matrix |

### 19. UI/UX

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 19.1 | Design system, landing page, profile indicator, sign-up fields | built, offline test only |  | Frontend workstream (not assessed here) |
| 19.3b | Server supplies clinic name, LIVE/TEST badge, profile, time zone | built and tested |  | GET /v1/iam/me |
| 19.4b | Patient profile fields; staff name/mobile/council number | partial | waiting on a person | Patient profile built (0080); staff personal fields await the privacy owner |

### 20. Consent, DPDP, clinical content governance

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 20.1 | Independent withdrawable consents; versioned notices kept permanently | built and tested |  | consent_settings.py, 0049; live tests. Notice texts are counsel's |
| 20.1b | Rights requests through tracked grievance; export; erasure | built and tested |  | rights_sql.py, 0059/0080; live tests |
| 20.1c | Under-18: guardian consent and proxy trail | partial | waiting on a person | Family link mechanism (0091) is deny-by-default; the rules are counsel's |
| 20.1d | Training labels only from de-identified, confirmed, consented | built, offline test only |  | learning_labels.py |
| 20.2 | Seeded content draft_unreviewed; two-person approval; no invented clinical content | built and tested |  | 0007/0072/0076-79/0090; live tests |
| 20.2b | Card editor validates schema and runs embedded test cases before publish | partial | waiting on a person | Editor validates content; embedded card test cases need a card schema decided by clinicians |

### 21. Testing strategy

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 21.1 | GitHub Actions; hosted Supabase for CI; restricted role | built and tested |  | ci.yml shards |
| 21.2 | Unit/graph tests with fakes for every port | built, offline test only |  | about 2,580 offline tests |
| 21.3 | Integration tests listed (200 concurrent bookings, isolation, audit immutability, resume, dead-letter) | built and tested |  | test_scheduling_concurrency_live, test_rls_child_tables_live, test_dead_letters_live |
| 21.4 | End-to-end flows over HTTP | partial | deliberate decision | Referral to review, cancel to booking and red-flag are covered. The care-plan flow now runs over HTTP (create, confirm, result, audit: test_care_plan_http_live, in the live shards); the wake-up and the conditional referral are the WORKER's step and stay in test_care_journey_db_live |
| 21.5 | Contract test + stale snapshot check | built and tested |  | test_openapi_snapshot.py; frontend check:api (it currently fails until the frontend regenerates types) |
| 21.6 | Dependency/secret scanning; red-team and load suites | partial | waiting on a person | The dependency audit is now a GATE (2026-11-18; scripts/audit_gate.py, ops/audit-ignore.yaml: any fixable finding fails unless a person listed it, because pip-audit gives no severity). Load/chaos run in process with a placeholder peak (the owner's expected peak is missing); no visual regression (frontend) |

### 22. Visit Readiness

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 22.1 | Pre-visit brief (structured, cited, decorative) | not built | waiting on a person | NOT BUILT by owner decision 2026-11-18; the question (which chart fields, which roles) is item 40 of the clinician sheet; a narrative also needs a model route |
| 22.2 | Adaptive intake from the card's missing facts; clinician-worded questions | built, offline test only |  | visit_readiness.py; questions are a required clinician setting |
| 22.3 | Readiness checklist; not-done/unclear reach a nurse in every mode | built and tested |  | visit_readiness.py; live tests |
| 22.4 | Barrier classification; rebooking hand-off; social barriers to front desk | partial | needs an external service | Classifier is a port with no model route; hand-off raises a front-desk task |
| 22.5 | No new tool server; readiness topic | built and tested |  | 0050 |
| 22.x | Lead-time trigger for brief/intake/readiness | not built | deliberate decision | Owner decision: a nurse or doctor relays; a trigger needs a producer design |
| 22.6 | Patient visit preparation (own checklist, done flags) | built and tested |  | 0089; visit_prep_db_live; OFF by default |
| 22.7 | Family links (deny by default, verified by front desk) | built and tested |  | 0091/0093; family_links_db_live; OFF by default; rules are counsel's |

### 23. Voice channel

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 23.1 | Gemini Live over a browser WebSocket | not built | needs an external service | No engine by owner decision; session route answers 503 |
| 23.2 | Narrow scope per workflow | built, offline test only |  | scoped prompts in voice.py; mid-session update support unverified (B.15) |
| 23.3 | Safety per 11.6 | built, offline test only |  | see 11.6 |
| 23.4 | Spoken and on-screen consent per session | partial | waiting on a person | `voice_session` purpose (0081); wording is counsel's |
| 23.5 | Degradation and cost rows | built, offline test only |  | mic hidden when engine off; audio price unmeasured |

### 24. Care gap review

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| 24.1 | One panel: overdue items, after-the-fact queue, unresolved barriers | built and tested |  | GET /v1/staff/care-gaps; live tests |
| 24.2 | Not an agent; nothing auto-resolves; age raises an overdue alert | built and tested |  | care_gap_sql.py, 0048 |
| 24.3 | Metrics care_gap_open_count, care_gap_median_age, voice_safety_escalation_rate | built, offline test only |  | metrics-catalog.yaml; dashboard route |

### App. Appendices

| Req | Requirement | Status | Reason | Evidence or note |
|---|---|---|---|---|
| A.1 | No rejected option silently reintroduced | built, offline test only |  | checked by CLAUDE.md rules and reviewer agents; vectors in pgvector is a recorded deviation |
| B.1-B.16 | Verify-at-build items | partial | needs an external service | B.12 and B.13 are resolved by real calls; B.14 not needed on the pgvector path; the other thirteen are unmeasured because the service or engine is not in the stack |
| C.1 | Traceability mapping | built, offline test only |  | documentation |
| C.2 | Scorecard re-run once built | not built | waiting on a person | Every evidence cell empty: NO-GO |
| D.0-D.10 | Phases 0 to 10 in order | partial | waiting on a person | Phases 0 to 9 built as mechanism; phase 10 checkers built, drills not run, DLT not started |
