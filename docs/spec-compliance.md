# BUILD-SPEC.md compliance review (2026-11-14)

One row per requirement of `BUILD-SPEC.md`, section by section including the appendices, checked against the code on `main` plus the `build` branch. **Status words:** **BUILT** (implemented and tested; where it needs a real service the row says what is faked); **FAKE** (built only to a port with a fake or stand-in, no real adapter); **NOT BUILT**; **DECISION** (waits for an owner, clinician, counsel or provider answer, or needs a new decision before it can be built). "Mechanism" means the code, database objects and tests exist but the clinical or legal content that feeds them does not (by design: `CLAUDE.md` rule 1).

How to read it with the other documents: `docs/gap-audit.md` lists only what is not real (the short list), `docs/design/pre-launch-checklist.md` lists what must be true before real patients, and this file lists EVERY requirement. Rows marked BUILT are not approved for real patients: nothing is.

Rows that were buildable under the rule of the 2026-11-14 instruction (no external service, no real clinical or legal content, no new decision) and were not yet built were built in the same change: see "Built in this review" at the end.

## 1. Product overview and scope (spec 1)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 1.1 | Four agents in order: Front Door, Scheduling, Care Journey, Visit Readiness | BUILT (mechanism) | The first two work end to end with fakes; the last two are behind flags with no approved model route | `careflow/front_door.py`, `scheduling_graph.py`, `care_journey.py`, `visit_readiness.py` |
| 1.2 | Care Journey and Visit Readiness behind flags, off by default | BUILT | `FEATURE_CARE_JOURNEY`, `FEATURE_VISIT_READINESS` | `careflow/care_journey_sql.py`, `visit_readiness_sql.py` |
| 1.3 | Voice is an in-app opt-in channel behind a clinic flag AND a per-patient preference | BUILT (mechanism), FAKE engine | Flag, `voice_enabled`, sessions, transcript monitor exist; no real engine (owner decision) | `careflow/voice.py`, `voice_sql.py`, migrations 0045, 0047 |
| 1.4 | Autonomy per clinic: shadow / assisted / autonomous, never a global constant | BUILT | History table, mode switch; `autonomous` is a two-person act and is off until two settings exist | `careflow/autonomy.py`, migrations 0048, 0053, 0054 |
| 1.5 | Default mode is `assisted` | DECISION | Built default is `shadow` (a recorded, owner-accepted departure: the safer direction) | `careflow/autonomy.py` |
| 1.6 | Locked regardless of mode: merge, non-template patient messages, out-of-range readiness answer | BUILT | Merge is two-person; no free-text patient message path exists; a not-done readiness answer reaches a nurse in every mode | `careflow/identity.py`, `mpi.py`, `visit_readiness.py` |
| 1.7 | The fixed-template emergency response is always automatic and a nurse must acknowledge | FAKE | The red-flag nurse task is built and immediate; the patient-facing template text is clinician content (`pending_clinician_template`) | `careflow/rules.py`, migration 0013 |
| 1.8 | The system does not diagnose, treat or free-text reply | BUILT | No such path exists; replies and offers are templates | `careflow/offers.py`, `messages.py` |

## 2. Operating profiles (spec 2)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 2.1 | Every external call and model role declares `data_policy` | BUILT | Registry rows and gateway filter | `careflow/gateway.py`, `registry.py`, migration 0015 |
| 2.2 | `clinical` refuses to report ready without a `phi_approved` route per required role | BUILT | `GET /readyz` | `careflow/app.py` (`readyz`), `gateway.py` |
| 2.3 | Capability table: deterministic features full in both profiles | BUILT | Rules, booking, queue need no model | `careflow/rules.py`, `scheduling.py` |
| 2.4 | Capabilities needing a model degrade explicitly in `clinical` (manual tasks, off) | BUILT | Holds and manual tasks; 503 for unwired routes | `careflow/front_door.py`, `triage_store.py` |
| 2.5 | A real clinical launch needs an approved LLM and vision route | DECISION | Owner and provider approvals; the demo hosted adapters are synthetic-only | `docs/design/phase10-pre-launch.md`, `real-model-demo.md` |

## 3. Architecture (spec 3)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 3.1 | FastAPI async, Pydantic v2, OpenAPI feeds a contract test | BUILT | Snapshot test fails when stale | `careflow/app.py`, `tests/test_openapi_snapshot.py` |
| 3.2 | SQLAlchemy Core async + asyncpg, hand-written SQL | BUILT | | `careflow/db.py` |
| 3.3 | Plain SQL migrations, checksummed ledger, advisory lock, append-only | BUILT | 79 migrations | `careflow/migrate.py`, `db/migrations/` |
| 3.4 | FHIR R4-shaped JSON validated by a FHIR library | NOT BUILT | Results and medications are plain typed rows; no FHIR library (a new dependency, and a modelling decision) | `docs/gap-audit.md` |
| 3.5 | LangGraph with durable Postgres checkpointing | BUILT | Own asyncpg checkpointer | `careflow/checkpointer.py`, migration 0021 |
| 3.6 | Two MCP servers in process: `scheduling` (read+write), `clinical` (read-only) | BUILT, FAKE data | The clinical server has no chart data to read yet | `careflow/mcp_kit.py`, `clinical_server.py`, `scheduling_agent.py`, `scheduling_write_tools.py` |
| 3.7 | Postgres-backed queue behind a `QueuePort`; Redis/SQS adapter stubbed | BUILT / NOT BUILT | Queue built; the stub adapter is deferred until volume needs it | `careflow/queue.py`, `ports.py` |
| 3.8 | React + Vite + TypeScript frontend, Vitest, contract test | OUT OF SCOPE HERE | A separate workstream | `frontend/` |
| 3.9 | Auth: Supabase Auth; Vectors: Qdrant | BUILT / FAKE | Auth built; Qdrant is a port with a fake | `careflow/auth.py`, `ports.py` |

## 4. Use-case and agent inventory (spec 4)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 4.1 | A structural test that the graph topology matches the inventory (no fourth agent; no pause with a side effect) | BUILT | Inspects every graph | `careflow/graph_structure.py`, `tests/test_*_structure.py` |

## 5. Models and decision engines (spec 5)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 5.1 | Model roles in a registry: provider, pinned id, data_policy, priority, price incl. free tier, shadow price | BUILT | Registry table, rows, ports; voice price unit is not wired | `careflow/registry.py`, `gateway.py`, migration 0015 |
| 5.1b | Real adapters for the roles | FAKE | Fakes are the default; hosted Gemini, Jev, Sarvam are merged for the SYNTHETIC demo only | `careflow/adapters/`, `provider_setup.py`, `docs/design/real-model-demo.md` |
| 5.1c | `llm_judge` is a different model from the generator | BUILT | `judge_may_gate` refuses a same-model judge | `careflow/evalmetrics.py` |
| 5.2 | `DecisionPort` with Laya, Jev, LLM adapters; engine and confidence recorded | FAKE | Port and adapters over injectable clients; no local Laya model; Jev is synthetic demo only | `careflow/decision.py`, `adapters/decision.py` |
| 5.2b | Chain by data class: PHI never goes to Jev | BUILT | The gateway filter and the adapter refuse | `careflow/gateway.py`, `adapters/decision.py` |
| 5.2c | Three-engine contract test passes (hard gate) | DECISION | Needs real engines and a clinician-reviewed set | `careflow/gates.py` |
| 5.2d | Laya: at most 20 options per choice; per-question-type calibration; route by script | BUILT (mechanism) | The 20-option cap and script routing are code; calibration needs labelled data | `careflow/decision.py`, `demo_decision.py` |
| 5.2e | Red-flag rules run first on every path; a model can only escalate | BUILT | Every text path calls the engine first | `careflow/rules.py`, `front_door.py`, `messages.py`, `voice.py` |
| 5.2f | Vision may precede the rules only as a transcription; screened first; failure is never clear | BUILT | The narrow carve-out | `careflow/extraction.py`, `adapters/vision.py`, `front_door.py` |
| 5.2g | Safety-net second opinion, fail closed to a nurse | NOT BUILT | Escalate-only path needs a clinician design | `docs/gap-audit.md` |
| 5.3 | Model deprecation handling (listing job, auto-disable, alert) | BUILT (pure check), NOT BUILT (job) | The comparison logic is built, with an unreadable listing never read as "vanished"; the scheduled job needs each provider's listing call (Appendix B, unverified) | `careflow/spend_policy.py` |

## 6. Gateway and caching (spec 6)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 6.1 | In-process gateway: routing, fallback, per-request and per-clinic-day ceilings, breakers at both layers | BUILT / FAKE | In-process gateway and breakers built; the Cloudflare pass-through is an external service not approved | `careflow/gateway.py` |
| 6.1b | Ceilings: 12 model calls (warn 8), 25 tool calls, 40 steps, 40 s per tick, $1 per job; the red-flag path never limited | BUILT | `RunBudget` and the tick clock | `careflow/budget.py`, `tick.py` |
| 6.2a | Prompt-prefix caching: byte-stable prompts, no PHI in a prompt, a CI scan | BUILT | | `careflow/prompts.py`, `prompt_scan.py` |
| 6.2b | Exact-match cache (embeddings, ICD, cards and rules, model lists) | NOT BUILT | Needs an embedding model, an ICD source and a model-list call (external) | `docs/gap-audit.md` |
| 6.2c | Semantic cache of de-identified card queries only | NOT BUILT | Needs embeddings and a vector store | `docs/gap-audit.md` |
| 6.2d | Request-scoped memoisation of tool reads within one run | BUILT | A per-run memo; not yet used by a tool | `careflow/spend_policy.py` (`RunMemo`) |
| 6.2e | The gateway's own response cache disabled for patient text | DECISION | The Cloudflare pass-through is not approved or built | `docs/design/pending-owner-ok-2026-11-04.md` |

## 7. Orchestration (spec 7)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 7.1 | LangGraph; deterministic routing; an LLM only proposes a care-plan task graph | BUILT | The plan parser has no approved model route, so every plan is held for a doctor | `careflow/care_journey.py` |
| 7.2 | Every step checkpoints by `thread_id`; a crashed run resumes | BUILT | Including a run cut after a step saved its work | `careflow/checkpointer.py`, `front_door_runner.py` |
| 7.2b | A node that pauses never has a side effect (structural test) | BUILT | | `careflow/graph_structure.py` |
| 7.2c | Idempotency keys on every external side effect | BUILT | Keys on every write route and definer function | `careflow/app.py`, migrations |
| 7.3 | Ceilings enforced: 40 steps, 25 tools, 12 models, 40 s, 5 retries then dead-letter | BUILT | | `careflow/budget.py`, `queue.py`, `dead_letters.py` |
| 7.4 | Postgres queue (visibility timeout, retries, dead letter), minute tick plus an immediate poke | BUILT | | `careflow/queue.py`, `tick.py`, migration 0006 |
| 7.4b | Outbox with producers (event to job) | BUILT (partial) | Producers: the document run job and a recorded result; other state changes emit no event (a design decision per producer) | `careflow/producers_sql.py`, `producer_sweeps.py` |

## 8. Tools and function calling (spec 8)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 8.1 | Two servers; a read tool cannot write (a server split) | BUILT | | `careflow/mcp_kit.py`, `clinical_server.py` |
| 8.2 | Write pipeline in order: schema, authorization, policy, budget, idempotency | BUILT | | `careflow/scheduling_write_tools.py`, `write_pipeline.py` |
| 8.3 | Explicit-status results (ok / empty / degraded / unavailable), never a bare empty collection | BUILT | `Envelope`; the offer draft is typed with a closed reason word | `careflow/mcp_kit.py`, `app.py` (`OfferDraftOut`) |
| 8.4 | Load-bearing versus decorative declared per tool per workflow | BUILT (mechanism) | Declared in config where tools exist | `careflow/mcp_kit.py`, `clinical_server.py` |

## 9. Memory (spec 9)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 9.1 | No agent-owned patient memory; four durable classes only | BUILT | No such store exists | `docs/design/` (data-flow and memory notes) |
| 9.2 | Last turns verbatim plus a typed state object; priority-ordered context with a token budget; drops logged | BUILT, UNUSED | Pure and tested; no real model call uses it | `careflow/context_assembler.py` |
| 9.3 | Care episodes and recurrence rules as first-class data | BUILT (mechanism) | The rule definitions are a required setting no clinician has written | `careflow/care_gaps.py`, `care_gap_rules.py`, migrations 0044, 0048 |
| 9.4 | Retention defaults and a scheduled, audited deletion job | BUILT (partial) | Report-only job (0071), voice transcript purge, audited erasure; deletion of append-only ledgers is excluded by owner decision; the periods are counsel's and the clinical lead's | `docs/design/retention-and-erasure.md`, migrations 0047, 0063, 0071 |
| 9.5 | Bi-temporal clinical state (results, medications, notes): never overwrite | BUILT (partial) | Results, notes, medications built; vitals and resource history are not; the 30-day correction window is not (every acted-on decision is reviewed) | `careflow/clinical_server.py`, migrations 0050, 0061, 0074 |
| 9.5b | Amendment versus correction declared by the writer, inferred otherwise | BUILT | | `careflow/care_journey_sql.py`, migration 0061 |
| 9.5c | Every decision records the versioned ids it read | NOT BUILT | Only care-plan steps cite a result; a general provenance record needs a design | `docs/gap-audit.md` |
| 9.6 | Notes append-only with addenda; structure-aware splitting; content-addressed embedding cache; sync embed on save | BUILT (append-only), NOT BUILT (rest) | Splitting and embeddings need an embedding model and a vector store | `careflow/notes_sql.py`, migration 0061 |

## 10. Retrieval (spec 10)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 10.1 | Hand-rolled pipeline: BM25 in own code, RRF, hosted reranker | BUILT (BM25, RRF) / FAKE (rest) | Qdrant, embeddings, reranker are ports with fakes | `careflow/retrieval.py` |
| 10.2 | Two corpora; card retrieval text from structured fields only; notes always filtered by patient | BUILT (card text, notes wrapper) / DECISION | Card text builder built; the patient-scoped note-vector wrapper makes an unscoped query impossible (clinic and patient required, current-only), over the fake store; `kb.cards` does not exist (every card is clinician content) | `careflow/retrieval.py`, `careflow/note_search.py` |
| 10.3 | Section-aware chunking, one context line per document, small-to-big | NOT BUILT | Needs Unstructured and an embedding model; the real-PDF spike is open | `docs/design/real-pdf-spike.md` |
| 10.4 | Tiered extraction: pypdf, Unstructured, Gemini; per-page provenance | BUILT (pypdf, ports) / FAKE | Unstructured and the real vision model are not wired (Gemini is a synthetic demo adapter) | `careflow/extraction.py`, `adapters/pdf.py`, `adapters/vision.py` |
| 10.5 | Embeddings: a local primary and a hosted hedge in one collection | FAKE | No model approved or installed | `careflow/ports.py` |
| 10.6 | Reranker with a fallback chain; decorative | FAKE | Fusion order is the live fallback | `careflow/retrieval.py` |
| 10.7 | Metadata split: deterministic fields gate; model-derived only rank | BUILT (principle) | Nothing model-derived gates access | `careflow/retrieval.py` |
| 10.8 | Abstention below a calibrated threshold; version-mismatch discarded at the join | BUILT (mechanism) | The threshold is calibrated data nobody has | `careflow/retrieval.py` (`AbstentionPolicy`) |
| 10.9 | Retrieval quality gate in CI | DECISION | Needs a gold set and thresholds | `careflow/gates.py` |

## 11. Guardrails and safety (spec 11)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 11.1 | Text is quoted data; an instruction-injection classifier flags text addressing an AI | BUILT (heuristics) | Hard gate needs a clinician-authored adversarial set | `careflow/text_guard.py` |
| 11.2 | Tiered grounding: canonical comparison for values, entailment for findings, label for summaries; failed means unverified | BUILT | Never fuzzy-matches a number | `careflow/grounding.py` |
| 11.2b | Guardrails AI custom validators | NOT BUILT | A new dependency not approved; the tiers are own code | `careflow/grounding.py` |
| 11.2c | Source-image crop beside an image-derived value | NOT BUILT | Needs a bounding box from a real vision parser | `docs/gap-audit.md` |
| 11.3 | PII masking, pattern recognizers only; minimisation after identity extraction | BUILT (heuristic) | Presidio itself is a new dependency; not wired to a real retriever | `careflow/text_guard.py` |
| 11.4 | Scope enforcement: clinical questions go to a human; the agent never answers | BUILT (rule routes) | The model classifier is a port | `careflow/reply_interpretation.py`, `voice.py` |
| 11.5 | Upload size and type allowlists; malware scan before real use | BUILT / FAKE | Allowlists built; ClamAV client and rescan worker built, no daemon installed or wired; real uploads refused outside demo | `careflow/documents.py`, `malware.py`, `scanner_worker.py` |
| 11.5b | Per-account message rate limit | BUILT (staff-written messages) | A required setting `CAREFLOW_MESSAGE_MAX_PER_MINUTE` (no default: unset means no limit, stated in the code) limits the two routes that write a patient-facing message (offer send, recorded reply) with a fixed 429; patient-sent messages wait on the patient messaging routes (frontend request 29) | `careflow/app.py` (`message_limiter`), `tests/test_message_limit.py` |
| 11.6 | Voice: the same rules on the live transcript, in parallel; interrupt to the fixed safety response; nurse task | BUILT (mechanism) | The interrupt gap is unmeasured (no engine) | `careflow/voice.py` (`StreamEvaluator` in `rules.py`) |

## 12. Human approval (spec 12)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 12.1 | Approval matrix per mode and action class | BUILT | Config, not code | `careflow/autonomy.py`, `scheduling_agent.py` |
| 12.2 | Reviewer view with proposal, evidence, alternatives, engine and confidence; reason picklist plus optional note | BUILT (partial) | Picklist built (a required setting); the free-text note is not stored by owner decision; the image crop is not built | `careflow/review_console.py`, `app.py` |
| 12.3 | Deadlines and escalation up the chain; nothing approved by timeout | BUILT | Email nudge is a fake channel | `careflow/review_console.py`, `watchdog.py` |
| 12.4 | Canaries, silent alert on approval; rubber-stamp monitoring | BUILT | | `careflow/canary.py`, `reviewer_stats.py`, migrations 0039, 0040, 0042 |
| 12.5 | Two-person rules: merge; publish or retire a rule (author cannot approve); autonomous (admin and doctor); erasure | BUILT | Rule approval, withdrawal and retirement 0072, 0076 to 0079; protocol-card publishing does not exist (no cards) | `careflow/rule_approval_sql.py`, `two_person.py`, migrations 0029, 0032, 0054 |
| 12.6 | Human decisions become labels; auto-approved never; pseudonymised; ~5% after-the-fact sample | BUILT (mechanism) | No export or training job; no auto-approval path so nothing real is sampled; never from a demo deployment | `careflow/learning_labels.py`, migration 0048 |

## 13. Observability (spec 13)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 13.1 | Langfuse traces, profile-gated export (metadata only for clinical) | FAKE | An in-memory sink; owner decided the hosted services stay fakes | `careflow/observability.py` |
| 13.2 | Grafana via OpenTelemetry; label discipline; daily rollup | FAKE | Label checks and generated rules exist; no panel has a query | `careflow/observability.py`, `scripts/generate_dashboard.py` |
| 13.3 | An in-app watchdog independent of third parties | BUILT | Red-flag overdue, queue age, dead letters | `careflow/watchdog.py` |
| 13.4 | Alert routing; content is class, count, opaque id only | BUILT / FAKE | In-app alerts built; email and chat senders are ports with fakes | `careflow/watchdog.py`, migration 0039 |
| 13.5 | Per-call, tool and step capture | BUILT | Closed fields, no bodies | `careflow/observability.py` |
| 13.6 | Origin tags and a Live / Test split with an explicit origin filter in every Live query | BUILT (API) | The dashboard route reads measured values; there is no page (frontend); the catalog declares origins | `careflow/metrics_catalog.py`, `app.py` (dashboard), `metrics-catalog.yaml` |

## 14. Evaluation (spec 14)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 14.1 | Gold set: reviewed, tuning and frozen held-out splits, Tamil separate; unreviewed never gates | BUILT (mechanism) | No case exists | `careflow/goldset.py`, `eval/gold/` |
| 14.2 | pytest gates; DeepEval; Langfuse experiments | BUILT (pytest) / NOT BUILT | DeepEval and Langfuse are new external dependencies | `careflow/evalmetrics.py` |
| 14.3 | Hard gates including red-flag recall 100% | BUILT (runner) / DECISION | The runner scores the real rule engine and writes evidence; bars, cases and rules are clinician-owned | `careflow/eval_runner.py`, `scripts/gold_run.py`, `gates.py` |
| 14.3b | Threshold gates with a variance band (mean minus 2 SD over 3 runs) | BUILT | Bars are required settings | `careflow/evalmetrics.py` |
| 14.4 | Metric source discipline; grounding rate; latency in three parts; two reason codes for escalation accuracy | BUILT (partial) | Grounding-rate and three-part latency functions built; the two escalation reason codes need a data design (a new column on reviewed tasks) and measured data | `careflow/spend_policy.py`, `metrics_catalog.py` |
| 14.5 | Business KPIs | NOT BUILT | Needs a baseline and owner-set targets | `docs/gap-audit.md` |
| 14.6 | Pre-deployment go/no-go and scorecard | UNMEASURED | The checker is built; every evidence cell is empty, so the answer is NO-GO | `careflow/predeploy.py`, `gates.py`, `drills.py` |
| 14.7 | Automatic demotion; promotion evidence | BUILT (demotion) / UNMEASURED | Promotion needs months of data | `careflow/autonomy.py`, `reviewer_stats.py` |

## 15. Cost (spec 15)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 15.1 | Cost canvas from real metrics, actual and shadow columns | BUILT | F and the total are owner-null; fewer than 7 days reads insufficient data | `careflow/cost_canvas.py` |
| 15.2 | Job cap, clinic daily cap, soft threshold at 80% shedding decorative features in order, safety never limited | BUILT | The gateway applies the caps and reports the plan (`Gateway.feature_plan`); the features it names (shortlist ensemble, reranker, judge sampling) have no live callers yet because retrieval and judging are fakes | `careflow/spend_policy.py`, `gateway.py`, `budget.py` |
| 15.2b | Durable spend ledger | BUILT | Worker ledger (0016) and the session ledger (0075) | `careflow/spend.py`, `session_spend.py` |
| 15.3 | Cheap-then-expensive cascade, billed per failed page | BUILT (decision tier) / NOT BUILT | Only the gateway cascade for `decision` | `careflow/gateway.py` |
| 15.4 | Attribution tags, failure multiplier, weekly tail review to the admin | BUILT (functions) / NOT BUILT (delivery) | Multiplier and tail selection built; the weekly Slack summary needs the chat sender | `careflow/spend_policy.py`, `cost_canvas.py` |

## 16. Reliability, security, deployment (spec 16)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 16.1 | Backoff with jitter, 3 sync attempts, 5 queue attempts, per-dependency breaker, timeouts | BUILT | | `careflow/gateway.py`, `ports.py`, `queue.py` |
| 16.1b | An idempotency gap blocks a release | BUILT | Every write path has a key; tests enforce | `tests/` (chaos and idempotency suites) |
| 16.2 | Degradation matrix | BUILT (mechanism) | Each row degrades visibly; the static emergency notice is frontend | `careflow/gateway.py`, `front_door.py` |
| 16.3 | Split topology; model workers on the owner's machine; SLOs | DECISION | Production hosting, region and the worker machine are the owner's | `docs/design/phase10-pre-launch.md` |
| 16.4 | Rollout ladder with change control and an on-call | DECISION | Process, not code | `docs/runbooks/` |
| 16.5 | Secrets never in prompts, logs or VCS; secret scanning; least privilege; append-only audit | BUILT | gitleaks in CI; restricted role; audit triggers ENABLE ALWAYS | `.github/workflows/ci.yml`, migrations 0030, 0035, 0068 |
| 16.5b | Data residency: India region; PHI only to local or approved calls | DECISION | Dev and CI are Tokyo and synthetic; production region is the owner's | `docs/design/pre-launch-checklist.md` |
| 16.5c | Backups, point-in-time restore, quarterly drill | UNMEASURED | Checkers and runbooks built; no drill run | `careflow/drills.py`, `docs/runbooks/` |
| 16.5d | Breach runbook with the 72-hour window | DECISION | Skeleton; contacts and wording are the owner's and counsel's | `docs/runbooks/breach.md`, `docs/drafts/breach-notification-wording-draft.md` |
| 16.6 | Vector storage under an agreement in `clinical`; ids-only payloads; a query wrapper; erasure verifies deletion | BUILT (wrapper, erase and verify) / FAKE / DECISION | The wrapper, ids-only points and a verified delete are built over the port and its fake; the real store, its agreement and the tenancy spike are open | `careflow/note_search.py`, `ports.py` |

## 17. Data model, identity, infrastructure (spec 17)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 17.1 | Eleven schema families with their tables | BUILT (partial) | `kb.cards`, card versions, match log, calibration, ICD cache do not exist (clinician content); `clinical` lacks vitals and resource history; the double-booking exclusion constraint exists | `db/migrations/` |
| 17.1b | RLS keyed on `auth.uid()`; forced RLS; restricted role | BUILT | Role-matrix test against the real restricted role | `tests/test_api_role_matrix.py`, migrations |
| 17.2 | MPI: encrypted demographics, keyed HMAC tokens, confident link, near-match flag, DOB required, claim needs both factors, logical merges and unmerge | BUILT | | `careflow/mpi.py`, `identity.py`, `crypto.py`, migrations 0002, 0003, 0008, 0029 |
| 17.2b | Key versioning in the schema; rotation as a documented manual procedure | BUILT | | `careflow/rotation.py`, `docs/runbooks/key_rotation.md` |
| 17.3 | Documents pipeline: signed upload, server re-verification, registration, extraction, grounding, identity, match, triage, approval | BUILT (stand-ins) | Storage is Supabase Storage for synthetic data; the signed-upload overwrite behaviour is unverified (Appendix B) | `careflow/documents.py`, `front_door.py`, `adapters/storage.py` |
| 17.4 | Prompts versioned with a hash; calibration in the database with an approver | BUILT (prompts) / DECISION | Calibration data does not exist | `careflow/prompts.py` |
| 17.5 | Messaging topics (care, appointments, safety, readiness) | BUILT | | migrations 0011, 0050 |
| 17.5b | Account states enforced server-side per feature | BUILT (partial) | States derived by the database; only the data export is gated; no patient feature routes exist to gate | `careflow/portal_sql.py`, migration 0062 |
| 17.6 | The data-flow register as a literal table | BUILT | Checked by a test | `data-flow-register.yaml`, `tests/test_data_flow_register.py` |
| 17.7 | Local versus hosted split | FAKE | No local model is installed; every hosted service is a port | `careflow/ports.py` |

## 18. Authentication and authorization (spec 18)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 18.1 | JWT verified per request by `kid`, claims set with `set_config(..., true)`; the transaction-mode pooler with no driver-side prepared statements | BUILT | | `careflow/auth.py`, `db.py`, `scripts/verify_app_role.py` |
| 18.1b | Staff invitation-only, mandatory second factor, step-up for sensitive actions | BUILT (API and database) / DECISION | TOTP enforced in the API and in the database for tables since 0047; older tables are an accepted residual; passkeys excluded by the owner; invitation needs the unwired Auth admin call | `careflow/auth.py`, `auth_admin.py`, migration 0047, 0065 |
| 18.1c | Staff recovery through two admins | BUILT, port unverified | The factor reset goes through the Auth admin port (a fake) | `careflow/recovery_sql.py`, migration 0060 |
| 18.1d | Patient OTP, device list, rate limits, CAPTCHA, guardian accounts | DECISION | Hosted Auth project settings; guardian consent is counsel's | `docs/frontend-backend-requests.md` items 21, 23 |
| 18.2 | SMS port; DLT registration; mitigations; sandbox sink in demo | FAKE / DECISION | Send guard and an empty template registry built; nothing registered, no provider | `careflow/sms.py`, `config/sms-templates.yaml` |
| 18.3 | Email port with two adapters; free fallback default; failure alert | FAKE | Port only | `careflow/ports.py` |
| 18.4 | RBAC at three layers; deny by default | BUILT | API, RLS, tool-server scopes | `careflow/active_role.py`, migrations |
| 18.4b | Break-glass: reason, audited, reviewed | BUILT (narrow) | Messages only; limits held by the database | `careflow/break_glass_sql.py`, migrations 0058, 0062, 0066 |
| 18.4c | Short-lived signed tokens per job between agents and tool servers | NOT BUILT | The servers run in process behind the worker identity; a signed token needs separate processes | `docs/gap-audit.md` |
| 18.4d | A CI test exercises every route against every role; the harness is populated phase by phase | BUILT | Every route has cases in the same change | `tests/test_api_role_matrix.py`, `test_active_role_matrix_live.py` |

## 19. UI/UX (spec 19)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 19.1 to 19.4 | Design system, landing page, profile indicator, sign-up fields | OUT OF SCOPE HERE | The frontend is a separate workstream | `frontend/` |
| 19.3b | Clinic name, LIVE / TEST badge, operating profile, clinic time zone from the server | BUILT | `GET /v1/iam/me` | `careflow/app.py` (`Me`) |
| 19.4b | Patient sex, preferred language, name; staff name, mobile, council number; optional email | DECISION | New personal data fields: the privacy owner's field list and counsel first | `docs/frontend-backend-requests.md` items 20, 22, 25 |

## 20. Consent, DPDP, clinical content governance (spec 20)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 20.1 | Independent, withdrawable consent purposes; versioned notices kept permanently; withdrawal in one click | BUILT (mechanism) | Notice texts are counsel's | `careflow/consent_settings.py`, migration 0049 |
| 20.1b | Rights through the grievance channel with tracking; download my data; erasure | BUILT (partial) | Grievances, export and erasure built; correction and nominee are not separate types (counsel); a public grievance route is undecided | `careflow/rights_sql.py`, migration 0059 |
| 20.1c | Under-18: guardian consent and a proxy trail | DECISION | Counsel confirms the specifics first | `docs/drafts/` |
| 20.1d | Training labels only from de-identified, clinician-confirmed labels with consent | BUILT (mechanism) | | `careflow/learning_labels.py` |
| 20.2 | Seeded cards, rules, templates are `draft_unreviewed` and unusable in `clinical` until two-person approval; no invented clinical content | BUILT | Rule authoring (drafts only), approval, withdrawal and retirement; cards and templates have no tables | migrations 0007, 0072, 0076 to 0079 |
| 20.2b | An admin card editor with embedded test cases | DECISION | Protocol cards are clinician content; no card schema table exists; frontend request 27 | `docs/frontend-backend-requests.md` item 27 |

## 21. Testing strategy (spec 21)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 21.1 | GitHub Actions; hosted Supabase for CI; the restricted role | BUILT | Live shards | `.github/workflows/ci.yml` |
| 21.2 | Unit and graph tests with fakes for every port; the structural test | BUILT | About 2,360 offline tests | `tests/` |
| 21.3 | Integration: 200 concurrent bookings, idempotent retries, cross-clinic isolation incl. child tables, immutable audit, resume without repeating, dead-letter | BUILT | | `tests/test_scheduling_concurrency_live.py`, `test_rls_child_tables_live.py`, `test_dead_letters_live.py` |
| 21.4 | End-to-end flows over HTTP | BUILT (partial) | Referral to review, cancel to booking, red-flag, plus a scripted scheduling sequence; the care-plan flow is in-process | `tests/test_phase2_e2e_live.py`, `test_e2e_demo_flow_live.py` |
| 21.5 | Contract test and a stale-snapshot check | BUILT | | `tests/test_openapi_snapshot.py` |
| 21.6 | Dependency and secret scanning; visual regression; red-team and load suites | BUILT (partial) | pip-audit is report-only; no visual regression; load and chaos run in process with a placeholder peak | `.github/workflows/ci.yml`, `tests/test_chaos_and_load.py` |

## 22. Visit Readiness (spec 22)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 22.1 | Pre-visit brief for the clinician, grounded, decorative | NOT BUILT | The brief narrative needs a model route and chart data | `docs/gap-audit.md` |
| 22.2 | Adaptive intake: the card's declared facts, only the missing ones asked, clinician-worded questions, rules on every reply, intent classification | BUILT (mechanism), FAKE models | Questions are the required setting `CAREFLOW_VISIT_READINESS_CONTENT`; with no model route every reply is unclear, re-asked once, then front desk | `careflow/visit_readiness.py`, `visit_readiness_content.py`, migration 0052 |
| 22.3 | Readiness checklist; any not-done or unclear reaches a nurse in every mode | BUILT | | `careflow/visit_readiness.py` |
| 22.4 | Barrier classification; rebooking hands off to Scheduling; social barriers to front desk | BUILT (partial) | The classifier is a port; the rebooking hand-off raises a front-desk task | `careflow/visit_readiness.py`, `care_gaps.py` |
| 22.5 | No new tool server; a `readiness` topic | BUILT | | migration 0050 |
| 22.x | Lead-time trigger; a patient-facing channel for replies | NOT BUILT / DECISION | Owner decision: a nurse or doctor relays replies; the trigger is a producer design | `docs/design/phase7-visit-readiness.md` |

## 23. Voice (spec 23)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 23.1 | Gemini Live over a browser WebSocket | FAKE | No engine by owner decision; the session route answers 503 | `careflow/voice.py` |
| 23.2 | Narrow scope per workflow; mid-session instruction updates | BUILT (scoped prompts) / UNMEASURED | Whether Gemini Live accepts mid-session updates is unverified (Appendix B) | `careflow/voice.py` |
| 23.3 | Safety per 11.6 | BUILT (mechanism) | | `careflow/voice.py` |
| 23.4 | Spoken and on-screen consent per session | NOT BUILT / DECISION | Counsel's wording; a consent event type per session | `docs/drafts/consent-notices-draft.md` |
| 23.5 | Degradation and cost rows | BUILT / UNMEASURED | The mic button hides when the engine is off; the audio shadow price is unmeasured | `careflow/voice.py`, `cost_canvas.py` |

## 24. Care gap review (spec 24)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| 24.1 | One panel: overdue items, after-the-fact review queue, unresolved barriers | BUILT (API) | `GET /v1/staff/care-gaps`; no page (frontend) | `careflow/care_gaps.py`, `app.py` |
| 24.2 | Not an agent; nothing on it auto-resolves; an age raises an overdue alert | BUILT | Worker raises the overdue alert from a required age setting | `careflow/care_gap_sql.py`, migration 0048 |
| 24.3 | Metrics `care_gap_open_count`, `care_gap_median_age`, `voice_safety_escalation_rate` | BUILT | Measured on the dashboard route | `metrics-catalog.yaml`, `app.py` |

## Appendix A: rejected options (spec Appendix A)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| A.1 | None of the rejected options is silently reintroduced (custom `app.clinic_id`, event sourcing, a broker as the primary queue, Mem0, prose summaries, a document-level vector, the two rejected embeddings, verbatim-quote grounding, Presidio with a name model, a global tool-health flag, pgvector notes, a fifth agent, open-ended voice, telephony, a managed voice platform) | BUILT | None is present; the claim is checked by the 13 rules and the reviewer agents; the Gemini Live "managed platform" is not used | `CLAUDE.md`, `docs/design/` |

## Appendix B: verify at build (spec Appendix B)

| Req | Item | Status | Reason |
|---|---|---|---|
| B.1 | Laya CPU latency | UNMEASURED | No local model installed |
| B.2 | Jev request shape | UNMEASURED | The adapter follows the documentation; the owner-run live check (`scripts/provider_live_check.py`) has not been run |
| B.3 | Reranker hosted availability | UNMEASURED | Not wired |
| B.4 | DeepEval custom judge | UNMEASURED | Not adopted |
| B.5 | Guardrails AI Provenance-NLI | UNMEASURED | Not adopted |
| B.6 | SMS provider hook with Supabase Auth | UNMEASURED | No provider chosen |
| B.7 | Free e-mail tiers | UNMEASURED | No provider chosen |
| B.8 | Sarvam limits and price | UNMEASURED | The live check covers the 1,000-character input only; prices are placeholders |
| B.9 | Uploadcare scanning | DECISION | Supabase Storage plus a ClamAV client chosen instead; no daemon |
| B.10 | Hosted embedding output shape | UNMEASURED | Not wired |
| B.11 | Passkeys | DECISION | Excluded by the owner |
| B.12 | JWT signing method and JWKS | BUILT | Resolved on dev and CI by real calls |
| B.13 | Advisory-lock connection mode | BUILT | Resolved by real calls and a CI run |
| B.14 | Qdrant per-clinic access control | UNMEASURED | A spike is open |
| B.15 | Voice interrupt gap; Gemini Live limits; mid-session instruction updates | UNMEASURED | No engine |
| B.16 | Storage signed-upload overwrite and validity | UNMEASURED | The spike is open; documents are demo-only until it is closed |

## Appendix C: traceability (spec Appendix C)

| Req | Requirement | Status | Reason | Where |
|---|---|---|---|---|
| C.1 | Each of the 17 reference layers maps to a section with its stated departure | BUILT | The mapping is documentation; every departure listed there is implemented as written (the `decision` tier, the in-process gateway, the two MCP servers, no RAG framework, no agent memory, tiered grounding, origin tags, the split topology, two-person rules, four named cache mechanisms) | `BUILD-SPEC.md`, this file |
| C.2 | The scorecard is re-run once built; a self-assessment is not evidence | UNMEASURED | Every evidence cell is empty: NO-GO today | `careflow/gates.py` |

## Appendix D: phased build plan (spec Appendix D)

| Phase | Status | Reason |
|---|---|---|
| 0 Foundations | BUILT | Ledger, 11 schemas, auth, role-matrix harness, data-flow register |
| 1 Deterministic core | BUILT | The red-flag gold-set regression is a mechanism only: no clinician rules exist |
| 2 Front Door | BUILT (fakes) | The real PDF spike, the three-engine contract test and the retrieval gold set are open |
| 3 Scheduling agent | BUILT (fakes) | Reply interpretation has no approved model route; delivery is by hand |
| 4 Approval, observability, cost | BUILT (fakes) | Langfuse and Grafana are fakes by owner decision; the cost canvas needs a real week |
| 5 Evaluation and gates | BUILT (mechanism) / UNMEASURED | No reviewed case; the scorecard is NO-GO |
| 6 Care Journey (flagged) | BUILT (mechanism) | The plan parser has no model route; task priority and role settings unset |
| 7 Visit Readiness (flagged) | BUILT (mechanism) | Questions, checklists, lead times are clinician content |
| 8 Voice (flagged) | BUILT (mechanism) / FAKE engine | No engine; the gap is unmeasured |
| 9 Care gap review | BUILT | Recurrence rule definitions are a clinician setting |
| 10 Pre-launch | BUILT (checkers) / UNMEASURED | Runbooks drafted; drills not run; DLT not started; the `clinical` capability sheet is blank |

## Built in this review

- `careflow/spend_policy.py`: the 80% soft-threshold plan with the spec's order and the never-limited safety path (15.2), the failure multiplier and the weekly tail selection (15.4), the grounding rate (14.4), the model-deprecation comparison that never reads an unreadable listing as "vanished" (5.3), and request-scoped memoisation of tool reads (6.2). Tests: `tests/test_spend_policy.py`.
- The patient-scoped note-vector wrapper with a verified erase (10.2, 16.6): `careflow/note_search.py`, `tests/test_note_search.py`.
- The per-account message rate limit on the two staff routes that write a patient-facing message (11.5), the three-part latency function (14.4) and the gateway's feature plan (15.2).
- The typed offer-draft response (`OfferDraftOut`: a closed reason word, the missing settings in their own field, typed data) for the frontend request about typed envelopes (8.3).

## What remains and why (the short list)

Everything NOT BUILT or DECISION above falls into five groups: (1) needs a real service or a new dependency not yet approved (embeddings, Qdrant, Unstructured, Presidio, Guardrails AI, DeepEval, Langfuse, Grafana, e-mail, SMS, malware daemon, Cloudflare pass-through, Gemini Live); (2) needs clinician content (cards, rules, intake questions, thresholds, recurrence rules, medication codes, gold-set cases, calibration); (3) needs counsel or the privacy owner (consent texts, retention periods, guardian rules, breach wording, new personal data fields, the public grievance route); (4) needs owner decisions on hosting, region, backups, DR drills and roles; (5) needs measurement with real engines or real traffic. The patient-facing routes (frontend requests 28 to 30) wait on the account-stage and consent decisions.
