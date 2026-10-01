# CareFlow — Build Specification

**For:** Claude Code, building this project from scratch.
**Status:** Fresh specification. Do not reference any prior implementation. Every decision below was made deliberately, with rejected alternatives recorded in Appendix A — do not silently re-open a closed decision; if something here seems wrong, ask rather than substitute your own default.

**Read order:** This document, then `CLAUDE.md` (root rules), then `metrics-catalog.yaml` (metric definitions). Build in the phase order given in Appendix D.

---

## 0. How to use this document

- Every numbered layer below is a section of the system, not a sprint. Build order is Appendix D.
- **"Verify at build"** appears inline wherever a fact (API shape, pricing, library behavior) was researched but not hands-on tested. Treat these as required spikes before depending on them, not as settled.
- **Never invent clinical content.** Thresholds, doses, red-flag phrases, protocol rules — anywhere a value would need clinical authority, insert a clearly labeled placeholder (`# CLINICAL-PLACEHOLDER: needs clinician sign-off`) and stop. Do not guess a plausible-sounding number.
- Two **operating profiles** run through every layer: `demo` (free/synthetic, every feature on) and `clinical` (approved endpoints only, features degrade gracefully). Layer sections state what changes per profile where it matters.
- Everything durable carries an **origin tag**: `live`, `test`, `eval`, `canary`. This is load-bearing for the dashboard (Layer 10) and must be threaded through the schema from migration 0001.

---

## 1. Product overview and scope

**What this is.** An agentic patient-access and care-coordination platform for a single outpatient clinic (multi-clinic-ready via row-level isolation, not required for v1). It reads referrals, screens for danger, matches clinical protocols, schedules and re-prioritizes appointments, and follows a care plan through to its conditional close-out.

**What this deliberately is not.** It does not diagnose, does not recommend treatment, does not free-text reply to a clinical question, does not prescribe. Every clinical judgment is either a deterministic rule a clinician authored and versioned, or a human decision. The AI's job is to read, extract, classify, and propose — never to decide anything a wrong answer could hurt someone over.

**Users:** patient, guardian (proxy for a dependent), front desk, nurse, doctor, clinic admin, business owner (aggregate view only), auditor (read-only audit access).

**Four agents, built in this order:**
1. **Front Door** — referral intake, safety screening, extraction, identity resolution, protocol matching, triage.
2. **Scheduling** — slot search/hold/offer/book, reply interpretation, cancellation backfill by clinical priority.
3. **Care Journey** — free-text plan → task graph, doctor confirmation, result-waiting, conditional close-out. **Ship behind a feature flag** (`FEATURE_CARE_JOURNEY`), off by default, so Layers 2–13 are proven against the first two agents before the third lands.
4. **Visit Readiness** — pre-visit clinical brief, adaptive intake conversation, procedure-readiness confirmation, no-show barrier resolution. **Ship behind a feature flag** (`FEATURE_VISIT_READINESS`), off by default, after Care Journey (§22).

**Voice is an in-app channel a patient opts into, not a fifth agent and not a phone line** — a mic button inside the already-authenticated patient portal, routing into the Scheduling and Visit Readiness agents' existing decision paths (§23), behind `FEATURE_VOICE_CHANNEL` at the clinic level **and** a per-patient `voice_enabled` preference (§19.4) — both must be on.

**Autonomy is a per-clinic setting with three modes**, never a global constant:

| Mode | Behavior | Default |
|---|---|---|
| `shadow` | Agent proposes everything; a human decides every action; used to collect labels before trusting automation | — |
| `assisted` | Auto-approves a single confident, low-risk protocol match at `routine`/`soon` priority; everything else needs a human | **default** |
| `autonomous` | Wider auto-approval, per clinic opt-in, gated by the evidence thresholds in §14.7 | opt-in only |

**Locked regardless of mode — never automated without a human deciding first:**
- Any identity change (patient record merge).
- Any free-text, non-template message to a patient.
- An out-of-range procedure-readiness answer (§22.3) — always escalates, never auto-resolves to "proceed."

**The one item that runs in the opposite direction — always automatic, never waits for a human first:** the fixed-template emergency safety response (§9.1), on any channel including voice (§11.6). This is not an exception to the three rows above; it is a different kind of lock entirely, and conflating the two under one heading is a mistake this spec made once already — see §12.1's approval matrix, which states both correctly and separately. **§12.1 is the source of truth if this list and that one ever diverge**, since §12.1 is the one updated in place whenever an agent or channel adds a new locked action.

**Mode-dependent gating** (human-required in `shadow`/`assisted`; releasable only in `autonomous` with explicit clinic opt-in):
- Records requests / referrals leaving the clinic.
- Care-plan tasks and conditional-referral close-outs.

Emergency safety advice is the one exception that sends automatically in every mode, from a fixed pre-approved template, with a mandatory nurse acknowledgment afterward (§9.1).

---

## 2. Operating profiles

Every external call and every model role declares a `data_policy`: `synthetic_only` or `phi_approved`. The container refuses to report ready in `clinical` profile if a required role has no `phi_approved` candidate. This is not a formality — it is the mechanism that stops free-tier infrastructure from quietly handling real patient data.

| Capability | `demo` profile | `clinical` profile without an approved LLM+vision route |
|---|---|---|
| Red-flag rules, booking, scheduling, review queue, dashboards | Full | Full — these are deterministic and local |
| Message intent, reply interpretation, safety second opinion (Laya) | Full | Full — Laya runs locally |
| Card matching | Full, with reranker | Works, reranker off (would send masked patient text externally) — fusion order used |
| Reading typed PDF referrals | Full | Text read locally (Unstructured); **extraction needs an LLM** — without an approved one, front desk keys facts in manually |
| Photographed referrals (Gemini vision) | Full | Off — manual task with the image shown |
| Tamil translation (Sarvam) | Full | Off — native Tamil red-flag rules still run |
| Care-plan free-text parsing | Full | Off — doctor enters tasks by hand |
| Pre-visit brief generation, adaptive intake conversation | Full | Off — front desk conducts intake by phone/in person, using the card's question list as a script |
| Voice channel (Gemini Live) | Full | Off — the mic button doesn't appear; patient continues in text, every other channel unaffected (§17) |

**This table is the honest statement in the spec: a real clinical launch needs at least one LLM and one vision route approved for patient data under an agreement.** A CPU-only machine cannot usefully self-host either. Free/local infrastructure is a `demo` and portfolio configuration; treat the `clinical` column as the pre-launch blocker list, not a defect.

---

## 3. Architecture overview

```
┌─ Patient portal (React) ──┐   ┌─ Staff console (React) ─┐
└──────────┬─────────────────┘   └───────────┬─────────────┘
           │  FastAPI: /v1 (portal) · /v1/staff · /internal (jobs)
           ▼
   Auth (Supabase) ── RBAC + RLS per clinic
           │
   Deterministic Supervisor (event → job, no model)
           │
   Postgres-backed queue (own tables, not a broker) ── QueuePort
           │
   AgentRunner → LangGraph graphs: front_door · scheduling · care_journey
           │                 (checkpointed to Postgres every step)
   ┌───────┴────────┐
   │                │
MCP: scheduling   MCP: clinical (read-only, field-filtered, audited)
(read+write)         │
   │                 ▼
   └──────────► Postgres (source of truth: clinical record, operational
                state, audit, outbox, queue, checkpoints, versioned
                knowledge) + Qdrant (card vectors + patient-note vectors)
```

**Confirmed tech stack:**

| Concern | Choice |
|---|---|
| API framework | FastAPI, async, Pydantic v2 request/response models, OpenAPI auto-generated (feeds the frontend contract test) |
| Data access | SQLAlchemy Core async + asyncpg, hand-written SQL (not the ORM) |
| Migrations | Plain `.sql` files, checksummed ledger table, advisory lock, append-only — no Alembic autogenerate |
| Validation | Pydantic v2 everywhere: API, model structured output, config |
| Clinical data shape | FHIR R4-shaped JSON validated by a FHIR library (e.g. `fhir.resources`), stored in Postgres JSONB — no standalone FHIR server |
| Orchestration | LangGraph, durable Postgres checkpointing. LangChain is **allowed** beyond orchestration (document loaders, chunking integrations) |
| Tool boundary | MCP, two servers mounted in-process: `scheduling` (read+write) and `clinical` (read-only) |
| Queue | Postgres-backed, own tables, behind a `QueuePort` interface; a Redis/SQS adapter is written but not wired (§7.4) |
| Frontend | React + Vite + TypeScript strict |
| Testing | pytest + real Postgres in CI; Vitest + Testing Library on the frontend; a contract test between the generated OpenAPI schema and the frontend client |
| Auth | Supabase Auth |
| Vectors | Qdrant (cards; patient-note vectors too — see §17.6) |

---

## 4. Layer 1 — Use-case and agent inventory

Already covered in §1. Additional build requirement: **a structural test that asserts the graph topology matches this inventory** — no fourth agent, no node added to Care Journey while its flag is off, no node that both performs a side effect and pauses for a human (this is the single most common source of duplicate bookings/messages in agent systems — see §7.2).

---

## 5. Layer 2 — Models and decision engines

### 5.1 Model roles

| Role | Purpose | Notes |
|---|---|---|
| `llm_large` | Extraction, plan parsing | |
| `llm_planner` | Care-plan task parsing (the one place an LLM plans; see §4 orchestration philosophy) | |
| `llm_fast` | Fallback classifier tier when `decision` engines are unavailable | |
| `vlm` | Photographed-document reading | Gemini 2.5 Flash |
| `embed` | Card and note retrieval | See §7.4 |
| `rerank` | Card-match reranking | See §7.4 |
| `llm_judge` | Evaluation judging only | **Must be a distinct model from `llm_large`** — never judge with the generator |
| `decision` | Closed-set classification, calibrated probabilities | Laya (local) → Jev (OpenRouter) → `llm_fast` fallback chain |
| `voice_live` | In-app spoken conversation (booking replies, readiness/intake confirmations spoken instead of typed) | **Google Gemini Live API** — native speech-to-speech over a browser WebSocket, no telephony, no phone number. `synthetic_only` at launch (patient speech leaves the browser for Google's API); no fallback engine — if unavailable, the mic button is simply absent and the patient continues in text, since voice is decorative relative to the text channel it sits beside (§17 degradation). Full design in §23; the guardrail this role needs that no other role does is in §11.6 |

Every registry row carries: provider, model id (pinned, not "latest"), `data_policy` (`synthetic_only`/`phi_approved`), priority order within its role, and **price per 1k input/output tokens — including free-tier rows**, which get a real cost of ₹0 and a "shadow price" (what the equivalent paid model would cost) so cost-per-outcome answers both "what does this cost today" and "what would this cost at 1,000 referrals/month." `voice_live` is priced per audio token (input and output separately, not per-1k-text-token like the other roles) — its registry row needs its own unit, not a forced fit into the others' shape.

### 5.2 The `decision` engine tier (Laya / Jev / LLM)

Three engines answer the same typed-decision questions (`choice`, `score`, `noul`-style accept/verify/reject): a local model, a hosted zero-shot model, and a general LLM as the last resort. They are **not interchangeable defaults** — routing differs by data class:

| Data class | Chain |
|---|---|
| Synthetic | Laya (local) → Jev (OpenRouter, `synthetic_only`) → `llm_fast` |
| Patient (`phi`) | Laya (local, `phi_approved` since self-hosted) → `llm_fast` (Jev skipped — third-party, not approved) |

**Build a `DecisionPort` interface** with three adapters (`LayaAdapter`, `JevAdapter`, `LlmAdapter`). Selection is config plus per-role `enabled`/`data_policy` on the registry. **Every decision call records which engine answered and its confidence** — an eval number is meaningless if you can't tell which engine produced it.

**Contract test (must exist before any engine ships):** the same golden inputs run through all three adapters; assert they agree within a stated tolerance. This is how "configure with and without Jev/Laya" stays a config change rather than a behavior change.

**Laya specifics** (self-hosted, CPU only, no GPU assumed — benchmark p50/p95 on the actual build machine before anything depends on it):
- Checkpoints: English (ModernBERT-large, 421M), multilingual (mmBERT-base, 322M, 100+ languages, 1024 tokens), typed-decisions (ModernBERT-large, 421M, 1024 tokens).
- **Hard rule: never more than 20 options in one `choice` call.** Accuracy degrades sharply above that (measured drop from ~0.87 to ~0.43 going from 8 to 77 options in the vendor's own benchmark).
- It is not zero-shot-strong. Base-model score on the vendor's own benchmark is near random (~0.35); usable accuracy needs per-question-type calibration (temperature scaling) fitted on labeled data — which is exactly what `shadow` mode produces. Store calibration per `(engine, question_type)`, not one global threshold.
- It cannot detect a script it wasn't trained for and will return a confident wrong answer on it (measured: English checkpoint scored 0.10 on Hindi and 0.00 on Khmer at >0.9 confidence). **Route by detected script before calling, never by confidence after.**
- Tamil/code-mixed text → the multilingual checkpoint, with the original text always retained. Test both the multilingual-checkpoint route and a Sarvam-translate-then-English-checkpoint route against a Tamil gold set; do not assume which wins.

**Where `decision` is used** (with fallback chain and data-class routing from above): red-flag second opinion (escalate-only — see hard rule below), reply interpretation, message intent classification, instruction-injection flagging, claim/entailment checking, card shortlist ranking (ensemble member, not sole decision-maker), care-plan loop-closure completion check, ingestion metadata tagging (specialty/body-system/acuity/language — **model-derived tags never gate access, only rank/narrow**), and evaluation-judge screening.

**Absolute rule: the red-flag deterministic rules run first, on every path, before any `decision` engine call. A `decision` engine's safety-net opinion can only escalate a case, never de-escalate one the rules already flagged.**

### 5.3 Deprecation handling

Model IDs pinned exactly. A scheduled job calls each provider's model-listing endpoint, compares against pinned IDs, auto-disables a vanished model, and alerts. Verify each provider's listing endpoint shape at build.

---

## 6. Layer 3 — Gateway and caching

### 6.1 Gateway

In-process gateway module (routing, fallback, per-request/per-clinic-per-day spend ceilings) with **Cloudflare AI Gateway** as a pass-through in front (free tier, edge analytics, provider rate-limit smoothing). Circuit breakers exist at **both** layers; the in-process ones are tighter and are the ones that must hold if the external gateway is bypassed or misconfigured.

Spend ceilings (in-process, non-negotiable): max model calls/job = 12 (warn at 8), max tool calls/job = 25, max steps/job = 40, max wall-clock/tick = 40s, absolute hard-stop cost/job = $1 equivalent. **The red-flag path and the safety net are never budget-limited.**

### 6.2 Caching — four distinct mechanisms, not one

| Mechanism | What | Where | TTL / staleness rule |
|---|---|---|---|
| Provider prompt-prefix caching | System prompt + tool defs kept byte-identical, variable content last | Provider-side | No PHI, no timestamps, no names ever interpolated into a system prompt — **enforced by a CI test that scans prompt templates** |
| Exact-match | Card-text embeddings (`hash(text+model_id)` key), WHO ICD lookups, published card bodies + red-flag rules, provider model lists | Postgres/in-process | Embeddings: permanent, invalidated by version/model change. ICD: 30 days. Cards/rules: 5 min. Model lists: 24h |
| Semantic | **De-identified card-shortlisting queries only** | Vector store | Cosine ≥ 0.97, TTL 1 hour, cache key includes card-library version. **Never on anything containing patient text** |
| Request-scoped memoization | Tool reads within one agent run (don't call `get_recent_results` three times in one job) | In-memory, per run | Life of the run |

**Cloudflare Gateway's own response cache is keyed on request body — enable it only for embedding/card-library calls, disable it for any request carrying patient text.** This must be a per-request flag the adapter sets, not a blanket setting.

---

## 7. Layer 4 — Orchestration

### 7.1 Framework and control-flow philosophy

**LangGraph**, chosen specifically for durable checkpointing — the deciding property is that this system's work *waits*, sometimes for weeks (an echo result), and any framework that can't survive a restart mid-wait is disqualified on a host that sleeps.

**Who decides the next step:** deterministic code routes almost everything. The one exception is turning a doctor's free-text care plan into a task list — genuinely open-ended prose, so an LLM proposes a task graph and a doctor confirms it before anything executes. No other step lets a model choose the path.

### 7.2 State and durability

Every step checkpoints to Postgres, keyed by a stable `thread_id`. A crashed or restarted run resumes from its last completed step — never replays from scratch, never silently drops.

**Structural rule, enforced by a test that inspects every graph definition: a node that pauses (calls `interrupt`/waits for a human or an external event) must never also perform a side effect.** A paused node re-executes from its start on resume; if it also sent a message or made a booking, that action repeats. Side effects go in the step immediately *before* the pause.

**Idempotency keys on every external side effect** (booking, patient message, referral dispatch, file registration) — this is required regardless of the state model, because at-least-once queue delivery and retries cause the same double-execution risk that a replay-based design would. Rejected: full event-sourcing (replay-rebuilt state) — the lineage benefits it would provide are already delivered by the checkpoint history, the append-only audit log, and per-decision provenance (§17.2); event-sourcing's cost (schema-as-immutable-contract, dedup on every replayed effect) isn't worth paying twice.

### 7.3 Ceilings (repeated from §6.1 for visibility — these are the guardrail, not a suggestion)

40 steps/run · 25 tool calls/run · 12 model calls/run (warn at 8) · 40s wall-clock/tick · 5 retry attempts then dead-letter.

### 7.4 Queue

**Postgres-backed**, own tables (visibility timeout, retry count, dead-letter table), drained by a scheduled minute tick **and** an immediate poke from the API on enqueue (so dispatch is near-instant in the common case, with the tick as the safety net for missed pokes). Build behind a `QueuePort` interface (`enqueue`, `claim`, `acknowledge`, `dead_letter`) with a second, unwired Redis/SQS-style adapter stubbed for a future migration if volume ever demands it — this workload (jobs that wait days) does not.

---

## 8. Layer 5 — Tools and function calling

**Two MCP servers, not one, mounted in-process:**
- `scheduling` — read and write (find/hold/book/cancel slots, join waitlist).
- `clinical` — **read-only**, field-filtered (returns only what the caller's declared scope needs), every call audited.

A read tool must be physically incapable of writing — this is a server split, not a permission flag on one server.

**Before any write tool executes, in order:**
1. Schema validation of arguments (types, ranges, enums).
2. Authorization: does this actor have rights on *this specific row*, not just this tool class.
3. Policy check: is this action allowed under the clinic's current autonomy mode.
4. Budget check: has this job hit its call/cost ceiling (§6.1).
5. Idempotency key present and honored.

**Tool results are explicit-status, never a bare empty collection:** `ok` / `empty` / `degraded` / `unavailable`, and the calling step must branch on the status. A `200 OK` with `{results: []}` when the underlying source is actually unreachable is exactly the "confident and wrong looks identical to confident and right" failure this rule exists to prevent.

**Load-bearing vs. decorative is declared per tool *per workflow*** in config, not as one global label per tool — a chart read is load-bearing for triage (no data → refuse, escalate to human) but decorative for a courtesy reminder (no data → proceed, note the gap). See §16.2 for the full degradation matrix.

---

## 9. Layer 6 — Memory

### 9.1 No separate agent memory store

Agents hold no free-text notes of their own about patients. Everything durable is one of four classes:

| Class | What | Retention |
|---|---|---|
| **Clinical record** | Identity, encounters, diagnoses, prescriptions, results, documents, notes | Clinic policy, see table below — this is the legal record, outlives any agent or model |
| **Operational state** | Appointments, holds, waitlist, message threads, review tasks, consent | Active, then archived |
| **Agent working state** | Checkpoints, queue rows, trace metadata, prompt/response bodies | 90 days (checkpoints/traces) / 30 days (prompt+response bodies) |
| **Institutional knowledge** | Protocol cards, red-flag rules, visit types, templates | Permanent, versioned |

**Memory families actually used** (mapped from the standard taxonomy): **entity memory** (typed clinical rows — precise, queryable, this is the primary mechanism), **episodic memory** (audit log, permanent; run traces, 90 days), **narrow vector memory** (clinical notes only, for passage retrieval — never card-vs-card "similar patient" retrieval), **human-authored semantic memory** (protocol cards and rules — curated and versioned by a clinician, not model-extracted "learned facts"). **Not used:** knowledge-graph memory (complexity the entity model already answers for), agent-owned free-text/vector memory of patients (a second, unapproved, unaudited medical record).

**Procedural memory is explicitly deferred to v2** (reserve the interface, do not build): the risk is that "what worked" and "what was clinically right" diverge, and a learned heuristic has no clinical author or review date the way a protocol card does. If built later, it is scoped *only* to operational parameters (offer-acceptance patterns, extraction-source hints) as typed config, never anything touching urgency, protocol choice, or escalation thresholds.

**Why not an off-the-shelf memory framework (e.g. Mem0):** the facts worth remembering about a patient are already typed clinical rows written through validated, audited, versioned paths. A model-extracted second copy of that would be unversioned, unapproved, outside retention rules, and a second erasure path to maintain — record this as a considered-and-rejected option (Appendix A), because it is the obvious first question a reviewer asks.

### 9.2 Working context (per model call)

Last ~6 turns of the current thread **verbatim**, plus a **structured, typed state object** (`{offered: [...], awaiting: "reply", deadline: ...}`) — not a prose summary. Structured state is exact and cheap; LLM-generated summaries of clinical conversation drift, and drift here means a lost or misremembered clinical fact.

**Context assembly is priority-ordered with a declared token budget per step:** task instruction → patient facts → retrieved evidence → history, added in that order; anything that doesn't fit is dropped from the lowest priority up, and **what was dropped is logged in the trace.** No framework — build a small, owned `ContextAssembler` module (~150 lines). No RAG framework either (§10.1) — this pipeline is short enough, and the permission filter is a safety control that must stay visible in code rather than buried in a library's retriever abstraction.

### 9.3 Care episodes and recurrence rules — first-class data in v1

A **care episode** (pregnancy, chronic condition, post-op follow-up) is an explicit container: start date, expected schedule, visits completed/outstanding, governing protocol. Without this, "she's missed her 28-week check" is unanswerable — each visit would be an unconnected event.

A **recurrence rule** ("annual review due each March") generates reminders and, if ignored, an overdue task — the same mechanism as an overdue echo result, different clock.

Episode content (e.g. an antenatal schedule) is **clinician-authored data**, loaded the same way protocol cards are; the engine that runs episodes is generic.

### 9.4 Retention (defaults — configurable per clinic; these are starting values, not fixed law)

| What | Default | Note |
|---|---|---|
| Clinical record, adult | 8 years after last encounter | Clinic policy governs; confirm against applicable state/professional-council rules |
| Clinical record, minor | Until age 21, min 8 years | Limitation periods commonly run from majority |
| Pregnancy/childbirth record | 21+ years | Claims can surface late |
| Consent records and notices | Permanent | Every published notice version kept — a consent is meaningless without the exact text shown |
| Audit log | Permanent (or ≥8 years per policy) | |
| Protocol/rule versions | Permanent | Must reconstruct any past decision |
| Agent checkpoints, run traces (metadata) | 90 days | Incident-investigation window |
| Prompt/response bodies containing patient text | 30 days | Highest risk, lowest long-term value — kept in Postgres, admin-only, every view audited |
| De-identified eval samples | Indefinite | No personal data |
| Erasure request | Identifiable fields removed; audit entries retained with identity tokenized | Erasure and statutory record-keeping coexist — do not delete the audit trail itself |

A **scheduled, audited deletion job** enforces this. A retention policy nobody runs is worse than none, because it was claimed.

### 9.5 Bi-temporal clinical state (correction vs. amendment)

Every clinical domain row (`vital_signs`, `lab_results`, `current_medications`, `clinical_notes`) carries `domain_tag`, `version_id`, `is_current`, `valid_from`, `valid_to`. **Never delete, never overwrite** — close the old row, insert the new one, in one transaction.

- **Amendment** (a new measurement — e.g. a later echo): the old value was true at the time. Supersede; do not reopen anything.
- **Correction** (the old value was wrong — a typo): every decision that consumed that value needs review.

**Who declares which:** the writer is asked ("new measurement, or correcting an error?") wherever a human is present; automated paths that can't ask fall back to code inference (same test + same effective date ⇒ correction; new date ⇒ amendment).

**On a correction:** any decision not yet acted on is blocked before execution. Any decision already acted on **within the last 30 days** gets a review task naming the old value, the new value, and what was decided — never silently reversed, never silently ignored. (Automatically reopening older closed loops was considered and rejected as disproportionate; 30 days balances recency against alert fatigue — tune with clinical input.)

**Every agent decision records provenance**: the exact resource ids **with version numbers** it read, the card version, the rule ids, and the engine/model id that answered. This is what makes the correction-fanout above possible — decisions are queried by "what versioned facts fed this," not guessed at.

### 9.6 Notes: append-only, structure-aware, content-addressed

Clinical notes are **append-only** — a correction is a new addendum, never an edit to old text. This is both the legally normal way clinical notes work and the thing that makes re-embedding cheap: nothing shifts, so nothing needs re-splitting.

Where a note is long enough to need splitting: **structure-aware first** (paragraph/heading boundaries from the source), semantic splitting only where structure runs out. A **content-addressed embedding cache** (`sha256(normalized_text) + model_id` → vector) means an untouched paragraph is never re-embedded, regardless of what changed elsewhere in the document.

**Single-note edits/addenda embed synchronously on save** (~300ms budget — acceptable for one clinician's one save). **Bulk re-embedding and reindex jobs run asynchronously**, off-peak.

---

## 10. Layer 7 — Retrieval (RAG)

### 10.1 No RAG framework; four owned libraries

Hand-rolled pipeline (the permission filter is a safety control and must stay visible in code, not wrapped in a framework retriever): **Qdrant** client with HNSW indexing and named vectors; **BM25 computed in our own code**, stored as Qdrant sparse vectors (one store to query, not two to reconcile); **Reciprocal Rank Fusion**, server-side in Qdrant (parameter-free); a **hosted cross-encoder reranker** via the model registry. LangChain's document-loading/chunking integrations are permitted here (§3); its retriever/memory abstractions are not used.

### 10.2 Two corpora, different treatment

**Protocol cards** (clinician-authored, versioned, no patient data): retrieval text is built from the card's structured fields (title, description, synonyms, patient-phrases, codes) — **never the card's rules**, which are evaluated, not searched. No chunking; one card = one retrieval unit.

**Clinical notes** (patient-scoped, free text): chunked per §9.6, filtered by `patient_id` and `is_current`/`as_of` on every query with no exception path — enforced at a query-wrapper layer so an unfiltered search is not physically possible to issue, and a missing patient scope raises rather than defaulting to "search everything."

### 10.3 Chunking (documents and reports)

For referral letters and reports (1 to 5+ pages, with images):

1. **Section-aware first.** Unstructured.io partitions into headed elements (title, narrative, table); a section (heading + its text) is the retrieval unit. Tables stay whole with their caption.
2. **Semantic splitting only where structure runs out** — a section too long for the embedding window, or a document with no headings (a phone-photo letter, unstructured free text).
3. **One context line prepended per document before embedding**, generated once: document type, date, specialty, principal diagnosis — **diagnosis only, never a value** (a number like "EF 25%" baked into an embedding header could outlive the corrected fact). The line is for embedding only; never shown to a clinician, never used as evidence. Regenerated only when the diagnosis field changes, so unrelated chunks keep their cache hits.
4. **Small-to-big retrieval:** search matches the chunk; the parent section's full text is read fresh from Postgres and returned.
5. **No separate document-level vector** (a two-level document+chunk index was considered — the single context-line-per-chunk approach gets the same cross-section context recall at lower cost and complexity; see Appendix A).
6. **Late chunking** (embedding a full document once, then slicing token-level vectors, avoiding all boundary-effect issues in principle) is recorded as a **v2 upgrade**, contingent on a self-hosted embedding endpoint that exposes token-level vectors — verify hosted-inference APIs return only pooled vectors before ruling this in or out definitively.

### 10.4 Extraction pipeline (documents and photographed pages)

Tiered, cheapest-first, heavy work in the worker never the request path:
1. Native PDF text extraction (`pypdf`) — handles most digital referrals in milliseconds.
2. **Unstructured.io** for layout-aware partitioning — tables, multi-column labs, complex reports. Runs locally (self-hosted is acceptable per the resource rule in §17.7).
3. Any element with no text layer, plus every embedded image and scanned page → **Gemini 2.5 Flash** for extraction (external call — see §2 profile table).
4. Merge results back in document order, carrying page/element provenance so a citation still points at a physical page.

### 10.5 Embeddings

| Slot | Model | Where | Notes |
|---|---|---|---|
| Primary | `NeuML/bioclinical-modernbert-base-embeddings` | **Local** (the one embedding model run on-machine, per the resource rule) | 768-dim, 8K context, pretrained partly on clinical notes — the best domain match found; **verify at build** (its own benchmark evaluation has acknowledged train/test overlap, so treat as leading candidate, not proven winner) |
| Hedge | `google/embeddinggemma-300m` | HF hosted inference (API) | 768-dim, multilingual — fallback if Tamil/code-mixed text needs to hit the index directly |

Both indexed as **named vectors in one Qdrant collection**; the gold set (§15.1), not preference, decides primary weighting. **Rejected:** `embeddinggemma-300m-medical` (literature-QA-tuned, English-only, wrong task shape for referral-shorthand-to-protocol matching), MedCPT bi-encoder (64-token query cap — unusable for full referral letters).

### 10.6 Reranker

`NeuML/biomedbert-base-reranker` (cross-encoder, PubMed-trained) via HF hosted inference. **512-token limit per pair** — rerank on a card summary plus the referral's key excerpt, never full documents; we control both sides of that pair, so this is a formatting rule, not a limitation. **Fallback chain:** Laya listwise → LLM listwise → fusion order (no reranking) if all engines are unavailable. **Role: decorative** — reranker failure degrades ranking quality, never blocks retrieval (§16.2).

### 10.7 Metadata

Assigned at ingestion, split by trust level:
- **Deterministic** (clinic, version, status, author, review date, source document, codes, dates, patient id, domain tag) — never model-derived, **these are the only fields allowed to gate access**.
- **Model-assisted** (specialty, body-system, acuity band, letter language, synonym expansion) — Laya/Jev/LLM `choice` over a fixed taxonomy, used to **rank and narrow only, never to gate**.

Filtering happens at the query layer as a pre-filter, not a post-filter on returned results.

### 10.8 Abstention and freshness

Below a calibrated confidence threshold on the top candidate: **return nothing, route to a human, log the abstention as a valid outcome** — not an error. Publishing a new card version triggers reindexing of that card only; a retrieved result whose version no longer matches Postgres is discarded at the join (the index is disposable and rebuildable, Postgres is truth); a nightly full rebuild is the backstop.

### 10.9 Retrieval quality gate (feeds Layer 11)

Gold set of referral letters labeled with the expected card, plus deliberately out-of-scope letters expecting no match. Metrics: top-1, recall@3, correct-abstention rate, false-routing rate. Gated in CI — see §15.3 for thresholds.

---

## 11. Layer 8 — Guardrails and safety

### 11.1 Input isolation

Document and message text is always passed as **quoted data, never as instructions** — this structural isolation is the primary protection, not a filter. On top of it, a light `decision`-engine classifier flags text that appears to address an AI directly (a prompt-injection attempt); a flag routes the document to human review. Classifier role: **decorative** — if it's down, structural isolation still holds and the gap is noted in the trace.

### 11.2 Output validation — tiered grounding, not verbatim quote matching

Exact-quote matching was tried and rejected: it fails whenever a model correctly normalizes something (a date format, a medical abbreviation), producing false alarms that train reviewers to rubber-stamp. Verification is tiered by fact type instead:

| Tier | Fact type | Example | Check |
|---|---|---|---|
| 1 | Structured values | DOB, phone, EF, BP, dose | Parse both the source quote and the extracted value to canonical form, compare canonically — **never fuzzy-match a number** |
| 2 | Interpretive findings | "abnormal ECG", "exertional" | Entailment check: does the cited passage support the claim? `decision` engine (Laya/Jev `noul`-style accept/verify/reject), LLM fallback |
| 3 | Free-text summaries | Clinical summary paragraph | No check — labeled model-generated, never feeds a rule |

**A failed check means "unverified," never "false."** An unverified fact simply satisfies no rule (§ unknown-is-not-a-value, consistent throughout); it only triggers human review if a matched card's rules actually read that specific fact — this targeting is what prevents a review-queue flood.

Compose these tiers as **Guardrails AI** (Apache 2.0) custom validators — do not use its bundled Provenance-NLI validator as-is (it ships local NLI checkpoints that would violate the local-footprint rule in §17.7 unless the NLI step is explicitly redirected to a hosted endpoint or the `decision` engine).

**Image-derived fields that drive a decision** (DOB, dose, EF, BP, allergies extracted from a photographed page): the review console shows the **cropped source region of the original image** next to the extracted value, wherever the parser can supply a bounding box. Rationale: every check above verifies against the vision model's own transcript — if the vision model misread the image, the transcript-based check passes regardless. Only the human, looking at the actual pixels, catches that class of error.

### 11.3 PII handling

**Presidio, pattern-recognizers only** (Aadhaar — with a custom recognizer covering spaced/hyphenated formats — PAN, phone, email, PIN code, UPI id). **No name-detection model** (would require a local spaCy/transformer model, which is out of scope per the local-footprint rule). Known gap, accepted: third-party names (referring doctor, relatives) are not automatically masked.

**Data minimization:** the identity-extraction step alone receives the full, unmasked letter (it needs the name and DOB). Every later step receives the text with the patient's own already-known values (name, DOB, phone) masked.

### 11.4 Scope enforcement

A patient message classified as a clinical question, refill request, or results question is **routed to a human**; a fixed template acknowledgment is sent meanwhile. **The agent never answers a clinical question itself, with or without a disclaimer** — a disclaimer does not change what leaves the building.

### 11.5 Abuse limits

Per-account message rate limits; server-enforced upload size/type allowlists; per-clinic daily model-spend caps (ties into §6.1/§16.2). **Malware scanning on uploads is required before real (non-demo) use** — verify Uploadcare's scanning add-on or equivalent at build; this is a pre-launch blocker, not optional.

### 11.6 Real-time transcript monitoring — the guardrail voice needs that no other channel does

Every other channel in this spec can run the tiered grounding checks (§11.2) *before* anything reaches the patient, because a queued job or a message reply has no deadline measured in milliseconds. A live spoken conversation does — the `voice_live` role (§5.1, Google Gemini Live) is a single model having the whole exchange end-to-end, and it does not pause to ask permission before every sentence the way a queued job pauses at a checkpoint. **This is a materially different guarantee from every other guardrail in this spec, and it is written out explicitly here rather than assumed to work the same way:**

- **First line of defense: scope, not detection.** The session's system instructions restrict what `voice_live` is permitted to discuss at all — reading out slot offers, asking the specific fact-gap questions a card declares (§22.2), reading a readiness-checklist item (§22.3). It is not given a general "help the patient" instruction, because a narrowly scoped model has far less surface on which to go wrong than one has to be caught by a monitor after the fact.
- **Second line: the deterministic rules run against the live transcript, in parallel, as each utterance is transcribed** — not after the session ends. This is the same phrase/value rule engine used everywhere else in the system (§5.2's absolute ordering rule applies without exception for this channel), reading a streamed transcript instead of a stored message.
- **On a match: interrupt, don't wait.** The session is forced into the fixed safety response (read from the same pre-approved, pre-translated template as every other channel — never composed live) and the same nurse-acknowledgment task is raised as any other red-flag escalation (§12.1).
- **The honest limitation, stated rather than hidden:** there is necessarily a small window between the patient finishing a concerning sentence and the interrupt landing, because the check runs *alongside* the model rather than *in front of* it. This is exactly why the scope-restriction is the first line of defense and not a redundant afterthought — a model that was never going to discuss anything beyond slot offers and fixed questions has very little room to say something harmful in that window regardless. **The actual size of that window must be measured at build time** (Appendix B), not assumed.
- Everything a `voice_live` session hears or says is treated as working context exactly like a text turn (§9.2) — last-turns-plus-structured-state, 30-day retention for the transcript body, no separate voice memory of any kind (§9.4).

---

## 12. Layer 9 — Human approval

### 12.1 Approval matrix

Per-mode, per-action-class (config, not code):

| Action class | `shadow` | `assisted` (default) | `autonomous` |
|---|---|---|---|
| Bookings, holds, cancellations | Human | Automatic | Automatic |
| Records request / referral leaving the clinic | Human | Human | Human, unless clinic opts in |
| Triage approval, care-plan tasks, conditional referrals | Human | Human, except auto-approve of one confident low-risk card | Human, unless clinic opts in |
| Patient merge, uncertain-identity registration | Human | Human | Human |
| Any non-template patient message | Human | Human | Human |
| Emergency advice (fixed template) | Automatic + nurse must acknowledge | Same | Same |
| Readiness-check answer outside the safe range (e.g. a blood thinner stopped too late or not at all) | Human | Human, automatic escalation | Human, automatic escalation |
| Barrier classified as resolvable by rebooking | Human confirms | Automatic (reuses the booking approval row above) | Automatic |
| Barrier classified as social/resource (transport, cost, childcare) | Human | Human (front desk) | Human (front desk) |
| A red flag detected during an in-app voice session (§11.6) | Automatic safety response + nurse must acknowledge | Same | Same |

**Locked rows, never automated without a human deciding first, regardless of clinic configuration:** patient merge, non-template patient messages, an out-of-range readiness answer.

**One row runs the other way, and is locked in the opposite sense — always automatic, never waits for a human first:** a red flag detected on any channel, including voice. These are two different kinds of "locked," not one — see §1.

### 12.2 Reviewer view

Every review task shows: the proposal; the evidence, with the source-image crop for image-derived fields; the alternatives considered, with their probabilities; what changes on approve vs. reject; which engine answered and its confidence; the specific rule that triggered review. Reason capture is a **picklist of reason codes plus an optional free-text note** (not free-text alone — picklists become clean, aggregable training labels).

### 12.3 Deadlines and escalation

Deadlines are set per task class. An overdue task escalates up the chain (nurse → doctor → admin) and raises an alert. **Nothing clinical is ever approved by timeout.** Notification is in-app (primary) plus email (nudge only, no health details).

### 12.4 Catching rubber-stamping

**Canary tasks:** 1–2% of the review queue is a synthetic, clearly fabricated referral with a planted error (e.g. an implausible DOB). No side effects, never touches a real patient. **Approving a canary alerts the admin, silently — the reviewer is never told.** Combined with per-reviewer approval-rate and time-to-decide monitoring.

### 12.5 Two-person rules

Patient-record merges; publishing or retiring a protocol card or red-flag rule (**the author cannot be the approver**); switching a clinic to `autonomous` mode (requires an admin **and** a doctor); processing an erasure request.

### 12.6 Learning loop

Human decisions and their reason codes become calibration/Laya fine-tuning labels. **Auto-approved outputs never become labels** (prevents the model confirming itself). Reviewer identity is pseudonymized in any training export. **~5% of auto-approved cases get an after-the-fact review** — otherwise the error rate of the automatic path is never actually measured, only assumed.

---

## 13. Layer 10 — Observability

### 13.1 Trace store: Langfuse Cloud

Via API key (not self-hosted — self-hosting needs ClickHouse + Redis + blob storage, which breaks the "only Laya, Unstructured, one embedding run locally" rule). Hobby tier (free, 50k units/month hard cap, 30-day retention) for development; **Core ($29/mo, 100k units, 90-day retention) is the expected tier once real clinic volume starts** — budget for it, don't assume Hobby survives production traffic.

**Export policy is profile-gated:**
- `demo`/synthetic: full traces to Langfuse.
- `clinical`/patient data: **metadata only** to Langfuse (opaque ids, engine, model, token counts, cost, latency, status, prompt version, hashed arguments) — **prompt and response bodies never leave Postgres**, where they sit 30 days, admin-only, every view audited.

Sampling under the cap: 100% of errors, escalations, fallbacks, and canaries kept; ~20% of ordinary successes sampled. **Tracing must never block the app** — async export, bounded queue; the audit trail (who-approved-what) lives permanently in Postgres regardless of Langfuse's availability or cap status.

### 13.2 Metrics: Grafana Cloud (free), pushed via OpenTelemetry

No self-hosted Prometheus. Free tier: 10,000 active series, 14-day retention — so **label discipline is mandatory** (workflow, engine, model, status-class, clinic only; never patient/thread/job id as a label, or the series budget is blown immediately). A **daily rollup table in Postgres** extends history past the 14-day window.

### 13.3 Independent in-app watchdog

The minute tick also checks: any red-flag task past due, the oldest queued job's age, any dead-lettered job — and escalates in-app plus email **independent of Grafana or Langfuse**. Critical safety alerting must not depend on a third-party free tier's uptime.

### 13.4 Alert routing

| Alert class | Channel |
|---|---|
| Role-specific task (red-flag, triage, care-plan) | In-app, pinned, to every on-role user; first to claim owns it |
| Escalation past due | Next role up the chain, in-app + email nudge |
| Runaway spend, dead-letters, fallback spikes, tracing-cap reached, delivery failures | Admin Slack channel + in-app banner |
| Canary approved by a reviewer | Admin only, in-app + Slack — **never shown to the reviewer** |

Slack/email content is **class + count + opaque task id + sign-in link only** — never a name, diagnosis, or value; both are third parties. A failed delivery on any channel raises an admin banner, so a dead channel is never silently dead.

### 13.5 Per-call/tool/step capture

Model call: engine, model id, prompt version+hash, tokens in/out, latency, real+shadow cost, cache hit. Tool call: name, argument hash, status (`ok`/`empty`/`degraded`/`unavailable`), latency, idempotency key. Step: duration, retry count.

### 13.6 Test vs. live dashboard (admin page)

Every durable record carries an **origin tag**: `live`, `test` (a flagged sandbox clinic in the same database, under the same row-level isolation — never visible to live queries), `eval` (golden-set runs), `canary` (synthetic tasks inside a live queue, excluded from live volume/cost/KPI figures, shown in their own panel).

Two tabs, no combined view (except a small spend-by-origin panel):
- **Live** (`origin=live`): reliability, safety, cost-per-completed-task, labeled quality, business KPIs, current autonomy mode and progress toward promotion gates (§14.7), plus a separate canary panel. Banner: "LIVE: real patients." Admin/business-owner see aggregates; nurses/doctors see safety views; drill-down to case level is audited.
- **Test** (`origin=test`+`eval`): golden set vs. variance band over time, retrieval/calibration/red-team/judge-agreement results, engine comparison table (Laya vs. Jev vs. LLM on agreement/latency/cost-per-correct-decision), the Tamil subset, the release checklist and scorecard, evaluation spend. Banner: "TEST: synthetic data."

**Two enforcement guards:** the metrics catalog (`metrics-catalog.yaml`) declares which origins each metric applies to; a **CI check fails the build if any Live-tab panel query lacks an explicit origin filter.**

---

## 14. Layer 11 — Evaluation

### 14.1 Evaluation data

Referral letters (typed PDF and phone-photo scans), Tanglish/Tamil messages, care-plan and result cases, and an adversarial set (hidden instructions, out-of-scope letters, conflicting values, missing DOB). Each case is labeled with expected card, priority, facts, red flags, identity outcome. **LLM-authored, clinician-reviewed — an unreviewed case cannot gate a release.** Tuning split and a frozen held-out split (never used for tuning). Starting size ≈150 cases (60 referral, 40 message, 30 adversarial, 20 care-plan); Tamil subset reported separately.

### 14.2 Framework

pytest for deterministic gates (hand-written); **DeepEval** for judged agent/RAG metrics (task completion, tool correctness, faithfulness, etc. — **verify custom-judge-model support at build**); **Langfuse experiments** for storing/comparing runs on synthetic data; RAGAS only if retrieval specifically needs diagnosing beyond what DeepEval covers.

**Judge model must differ from the generator** (`llm_judge` role, §5.1). Laya screens first, the LLM judge re-scores a sample. Judge agreement with human labels is measured on 30–50 cases; **a judge below the agreement bar cannot gate anything** until recalibrated.

### 14.3 Gates

**Hard gates (zero tolerance — block every release):**
- Red-flag recall = 100%. Urgent-case recall = 100%.
- No injected/adversarial text changes a priority or extracted value undetected.
- No unverified fact reaches a rule.
- No wrong patient merge on the labeled identity-matching pairs.
- Clinic-isolation, double-booking, and idempotency tests all pass.
- The three-engine (Laya/Jev/LLM) contract test passes.
- **Voice safety escalation rate = 100%** on the voice gold-set (`voice_safety_escalation_rate`, `metrics-catalog.yaml`) — every scripted red-flag utterance in a test `voice_live` session must trigger the spoken safety response and nurse acknowledgment (§11.6). This gate did not exist when Layer 11 was first written, because the voice channel didn't exist yet; it is added here specifically so the metrics catalog's own hard-gate marking is honored rather than silently unchecked.

**Threshold gates** (fail only if a result drops below the **variance band** = baseline mean − 2 standard deviations over 3 runs, not on any single-run dip): card recall@3 ≥ 0.80; correct-abstention rate ≥ 0.90; priority accuracy ≥ 0.95. Starting values — a clinical lead adjusts them.

**Reported, not yet gating:** cost per outcome, latency, judged quality (until the judge is calibrated per §14.2).

### 14.4 Metric source discipline

Not every number on the dashboard is "live" in the same sense — the catalog (deliverable #3) tags each metric's source:

| Source | Examples | Freshness |
|---|---|---|
| Live, computed | Uptime, error rate, p95/p99, cost vs. budget, cost per completed task, loop/failure rate, step efficiency, tool-status mix, guardrail flag rate, index freshness lag, audit-log completeness, rollback frequency, queue depth/oldest-age, review-queue age, grounding rate | ≤60s |
| Live, human-labeled | Top-1 card correctness, priority agreement, escalation accuracy, guardrail false-positive/negative rate, clarification quality | Lags by review time |
| Offline, needs gold answers | Context recall/precision/relevance, answer correctness/similarity, tool-call accuracy, trajectory match, red-team pass rate, golden-set vs. band | Last nightly/pre-release run date shown |
| Checklist | The 12 pre-deployment gates (§14.6) | Status + evidence link |
| Business | Adoption, automation rate, KPI delta | Monthly, needs a baseline |

**Grounding rate** (share of decision-relevant extracted facts passing the §11.2 tiered checks) stands in for faithfulness/hallucination-rate/citation-accuracy at once, computable live since no free-text answers reach patients. **Latency per completed task is reported in three parts, never blended**: machine time (vs. SLA), human wait, external wait (patient reply, pending result) — jobs here legitimately wait days, and a blended average would be meaningless.

**Escalation accuracy needs two reason codes on every reviewed task**: "unnecessary escalation" (over-escalation) and "missed by system" (under-escalation, surfaced via the 5% after-the-fact review and canaries — it never shows up in ordinary live traffic).

### 14.5 Business KPIs (clinic-specific; needs a baseline, simulated + labeled in demo mode)

Referral-to-appointment time; urgent cases seen within target; backfill fill rate; follow-ups closed vs. lost; no-show rate. The business owner (clinic administrator role) sets numeric targets — these are not engineering decisions.

### 14.6 Pre-deployment gate (go/no-go, once, before first real traffic)

- Production-readiness scorecard ≥ **70/100**, no category below 12/20 (5 categories × 20 pts: Architecture, Observability, Security, Compliance, Operations).
- Load test at N× expected peak concurrency passes without SLA degradation.
- Security scan: zero open critical/high findings.
- Guardrail red-team pass rate: 100% of known attack patterns blocked or safely handled.
- Compliance checklist: 100% of applicable items.
- Rollback drill: executed end-to-end, under 5 minutes.
- Cost projection vs. dry-run actual: within ±20%.
- On-call rotation staffed, runbooks written — hard gate, yes/no.
- Golden eval set + variance band established — hard gate, yes/no.
- Data residency / access-control audit passes.
- Harness, contract, and chaos test suite: 100% pass.
- Named business owner and success definition signed off — hard gate, yes/no.

### 14.7 Autonomy evidence gates

**Automatic demotion to `shadow`** (no approval needed — more human review is always the safe direction) if: after-the-fact review error exceeds 2%, or any canary is approved. Fires an alert, does not wait for a decision.

**Promotion to `autonomous`** needs, together: ≥3 months of `assisted` operation; ≥200 reviewed triage decisions; review error <2%; zero missed urgent cases; canary catch rate ≥95%; two-person approval (§12.5). Starting thresholds — a clinician sets the real bar.

---

## 15. Layer 12 — Cost

### 15.1 The cost canvas: `Monthly cost ≈ W × A × U + P + F`

| Term | Meaning here |
|---|---|
| **W** — workload | Referrals/messages/results per day (interactive) vs. re-embedding/eval/rollups (batchable, run off-peak) |
| **A** — amplification | Model+tool calls per workflow; ceiling 12 (§6.1), expected 5–8 — **measure in week one, don't assume** |
| **U** — unit economics | Tokens, cache-hit share, model tier; Laya and the local embedding cost ≈₹0 per call |
| **P** — platform floor | Hosting, Langfuse Core, Grafana (free), email/SMS platform fees |
| **F** — failure tax | Retries, timeouts, escalations, **reviewer minutes × a configurable per-role rate** (the largest line item in a system this human-in-the-loop-heavy) |

Because jobs resume from checkpoints (§7.2), a retry re-runs one step, not the whole workflow — this keeps F smaller than in most agent systems.

**The canvas is computed from real metrics, not a spreadsheet estimate.** Every price gets two columns: actual (mostly ₹0 today) and at-scale shadow price. A **dry run must land within ±20% of the estimate before launch** (§14.6). **The `voice_live` role prices by audio tokens, input and output separately** (§5.1) — under Google AI Studio's free tier, actual cost is ₹0 and rate-limited; the shadow-price column still needs a real per-minute figure recorded at build (Appendix B), since a free rate-limited tier and a production per-minute rate are not the same number and the canvas must not conflate them.

### 15.2 Budgets and graceful degradation

Beyond the call/step/time ceilings (§6.1): **job cost cap** — alert at 3× the rolling average, absolute hard-stop at $1-equivalent per job. **Clinic daily and monthly caps**, set from measured data. **Soft threshold at 80% of any cap** turns off decorative features first, in this order: card-shortlist ensemble member → reranker (fall back to fusion order) → judge sampling. **At the hard cap**, new non-urgent jobs queue until the period resets. **Never budget-limited, under any circumstance: the red-flag path and the safety net.**

### 15.3 Cheap-then-expensive cascade

Extraction: native PDF text → Unstructured → Gemini, **billed per page, only for pages that actually failed the earlier tiers** (never per whole document). Classification/extraction: fast tier first; escalate to the large tier only on a single retry, and only if grounding verification (§11.2) failed. Batchable work runs off-peak.

### 15.4 Attribution

Every model call tagged: workflow, step, engine, origin, clinic, clinic-visible feature. Tracked: cost per successful workflow, first-pass success rate, cache-hit rate, and a **failure multiplier** (effective cost per success ÷ nominal cost per call) — this is how a "cheaper" model that's wrong more often gets correctly identified as costing more overall. **Weekly tail review:** the top 5% costliest jobs go to the admin as a Slack summary.

---

## 16. Layer 13 — Reliability, security, and deployment

### 16.1 Retries, timeouts, breakers

Exponential backoff with jitter; max 3 attempts on a synchronous external call; queue jobs get 5 attempts then dead-letter (§7.3/7.4); a retry replays only the failed step, never the whole run. Starting timeout budgets (tune by measurement): Laya 2s, local embedding 1s, database 2s, Slack/email/Sarvam 5s, hosted LLM 20s, vision-per-page 60s (worker only). One circuit breaker per external dependency: opens after 10 consecutive failures, probes again after 60s. **Idempotency key on every write** — an idempotency gap on any write action blocks a release outright.

### 16.2 Degradation matrix

| Dependency | Role | Behavior when unavailable |
|---|---|---|
| Red-flag rules | Load-bearing, always on | No model dependency at all — cannot go down with anything else |
| Laya | Decorative, except the safety second opinion | Falls through the engine chain; if none answer, the safety net **escalates to a nurse** (fail closed) |
| Any LLM | Load-bearing for extraction | Referral becomes a **manual triage task** showing the raw document — never silent |
| Embedding / vector search | Load-bearing for card matching, decorative for notes search | Card matching → manual triage. Notes search degrades quietly |
| Reranker | Decorative | Fusion order used, trace marked |
| Gemini vision | Load-bearing for photographed referrals | Manual task showing the image |
| Sarvam translation | Decorative for triage comprehension | Original text + flag shown; **pre-translated Tamil safety templates are unaffected** |
| Uploadcare | Load-bearing for uploads | Clear error, front desk retries — nothing half-registered |
| Slack / email | Each decorative to the other | Remaining channel + in-app; a failure raises an admin banner |
| Langfuse / Grafana | Decorative | Nothing else changes — the in-app watchdog (§13.3) keeps running regardless |
| Postgres | Everything | API returns 503; patient-facing pages keep a **static, backend-free** "in an emergency call 108" notice |
| Voice channel (Google Gemini Live) | Decorative — the text channel is the load-bearing one it sits beside | The mic button is hidden; the patient continues the same conversation in text, at the same point they were at, since it's the same message thread either way (§9.2) |

### 16.3 Deployment topology

**Split, not single-machine:**
- API + Postgres on always-on hosting.
- **Model workers (Laya, Unstructured, the local embedding model) run on your own CPU machine**, pulling jobs from the Postgres queue — outbound connections only, no inbound ports required on that machine.
- Red-flag rules and booking keep working when the model machine is off; model-dependent steps simply queue, with a stale-queue alert if they sit too long.

SLOs (>99.9% uptime, <5-minute rollback) apply to the deterministic path (API + database) on paid hosting. Free-tier components are explicitly **best-effort**, with keep-alive pings — do not represent them as meeting the same SLO.

### 16.4 Rollout ladder

Mapped to autonomy modes, per clinic:

1. **Sandbox** — synthetic data only.
2. **Shadow** — real referrals, agent proposes, human decides everything (recommended: at least one week before the first real automated action, even though the clinic's chosen default is to start in `assisted`).
3. **Assisted** — the default live mode.
4. **Autonomous** — opt-in, gated by §14.7.

Each stage transition needs: exit criteria met from the relevant Layer 11 gate; a change-control record (sign-off, evidence, date); a rollback path that flips the mode/flag back. A named on-call covers the first 72 hours of each new stage.

### 16.5 Security, backup, breach basics

- Secrets never in prompts, logs, or version control; CI secret-scanning blocks a commit containing one.
- Least privilege: separate credentials per MCP tool server (read-only vs. write); the application's database role cannot bypass row-level security.
- Audit log: append-only, retained per clinic policy (default 8 years, §9.4).
- Data residency: database in an India region; models that touch patient data run locally; every external API call is listed with its data class (§17.6 data-flow register); `clinical` profile blocks any call not approved for patient data.
- Backups: encrypted, point-in-time restore; targets ≤15 minutes data loss, ≤4 hours recovery; **quarterly restore drill**, on the release checklist.
- Breach runbook: covers the 72-hour DPDP notification window; every incident feeds the MTTR metric via an incident register.

### 16.6 Vector storage location

**Both card vectors and patient-note vectors live in Qdrant** (patient-note vectors were deliberately moved off Postgres/pgvector per an explicit build decision — see Appendix A for the trade-off this accepts). This is **profile-gated**:
- `demo` profile: Qdrant Cloud free tier is acceptable.
- `clinical` profile: requires a Qdrant deployment under a data-processing agreement. **Without one, notes search switches off** (load-bearing-vs-decorative rule, §16.2) — card matching is unaffected since cards carry no patient data.

Safeguards regardless of tier: vector payloads hold ids only (text stays in Postgres, the source of truth); one query wrapper enforces clinic + patient filtering, so an unfiltered query cannot be issued; erasure deletes vectors and verifies deletion; a nightly job reconciles the index from Postgres.

---

## 17. Data model, identity, and infrastructure specifics

### 17.1 Schema families (Postgres, one database)

| Family | Contents |
|---|---|
| `iam` | Accounts, roles, staff, consent records, activation requests, grievances |
| `mpi` | Patients (encrypted demographics), identity tokens (keyed HMAC for blocking), match candidates, merges |
| `sched` | Providers, locations, visit types, templates, slots (gist exclusion constraint — **no double-booking is possible at the database level**), bookings, waitlist, idempotency keys |
| `clinical` | Bi-temporal FHIR-shaped resources (§9.5), resource history (append-only) |
| `docs` | Uploaded document records |
| `msg` | Message threads (topic: `care`/`appointments`/`safety` — see §17.5), messages, reads |
| `kb` | Protocol cards, card versions, red-flag rules, match log (write-once labels), calibration, ICD cache |
| `agents` | Runs (checkpoints), review tasks, waits |
| `ai` | Model registry |
| `ops` | Transactional outbox (event → job, exactly-once semantics with the queue) |
| `audit` | Append-only log, trigger-enforced immutability |

**Migrations are plain SQL files, checksummed, applied through an advisory lock, and are themselves append-only** — an applied migration is never edited; a mistake is fixed with a new migration.

**Postgres and Supabase Auth are the same Supabase project** (§18.1), hosted, India region (§16.5). The full RLS and identity mechanism — including the direct-asyncpg-connection wrinkle and why it isn't a simple drop-in — is specified once, in §18.1, and referenced from here rather than duplicated. **An earlier draft of this section specified a different, incompatible mechanism (a custom `app.clinic_id` session variable via `SET LOCAL`) instead of `auth.uid()`. That draft is superseded and was deleted from this file — it is recorded in Appendix A as a considered-and-rejected alternative, not silently erased, but it must not be treated as live spec.**

### 17.2 Master Patient Index (identity)

- Demographics encrypted at rest (AES-256-GCM or equivalent); lookups via keyed HMAC tokens (never a plaintext index on name/DOB/phone).
- **Confident match → link.** **Near-match without a strong identifier (e.g. matching phone) → create a new record AND flag a possible-duplicate for merge review** — never silently merge on a near-match alone.
- **No match → create.**
- Requires a date of birth. A referral letter that cannot supply one routes to the front desk, who supply the missing detail — an age alone ("58 yr old") is never treated as a date of birth.
- **Claiming a record at self-registration** (a referred patient signing up) requires **both** the verified phone/email **and** the date of birth to match the referral-created record; otherwise the front desk decides. This prevents claiming someone else's record via a shared or guessed detail.
- Merges are **logical links with survivorship rules, never destructive**. An unmerge procedure exists and re-runs affected decisions.

**Key versioning is a Phase 0 schema decision; the key store and rotation procedure are deferred to Phase 1.** Every encrypted column and every HMAC token carries a `key_version` alongside it, reserved now because retrofitting it after rows exist means a backfill, while reserving it costs nothing. What actually generates, stores, and rotates the keys is out of scope for the schema and belongs to Phase 1: the current plan is platform secrets (the hosting provider's own secret manager) rather than a dedicated KMS — disproportionate infrastructure for a single-operator project at this stage — with rotation as a **documented manual procedure** (re-encrypt under the new key, keep the old key available through the transition, verify, retire) rather than automated key-rotation machinery.

### 17.3 Documents pipeline (end to end)

Signed browser→storage upload (Uploadcare) → server-side re-verification of the file (type, size, that it actually exists at the claimed location — never trust the client) → registration in `docs` → extraction pipeline (§10.4) → facts extracted with citations → tiered grounding check (§11.2) → identity resolution (§17.2) → protocol match (§10) → triage decision → human approval where required (§12).

### 17.4 Prompts and configuration as versioned artifacts

Prompts live in the repository with a version and content hash; **every model call records which version answered.** A prompt change is a pull request that must pass the evaluation gates (§14.3) — nothing is editable at runtime. Calibration coefficients and thresholds live in the database with a version and an approver id (two-person rule where the change affects a clinical threshold).

### 17.5 Messaging topics and patient lifecycle

Message threads carry a `topic`: `care` (clinical conversation, opens only after account activation), `appointments` (slot offers — visible to `new_patient` accounts too, since a referred patient must be able to see and answer an offer before their first visit), `safety` (emergency advice — visible to every registered patient regardless of activation state).

Account states: `registered` → `new_patient` (booking + appointment/safety messages only) → `pending_activation` (after first visit) → `active` (full access). **Enforced server-side per feature, never only hidden in the UI.**

### 17.6 Data-flow register (build this as a literal table, not prose)

For every external call in the system: the service, what data it receives, its `data_policy` classification, and whether it is permitted in the `clinical` profile. This table is what Appendix B's "verify at build" items ultimately populate, and it is what an auditor will ask for first.

### 17.7 Local vs. hosted, restated precisely

**Runs locally, on CPU, no GPU assumed:** Laya (all three checkpoints), Unstructured.io (including its layout/OCR helpers — these run in worker processes only, never the request path, since they take seconds per page on CPU), the primary embedding model (`bioclinical-modernbert-base-embeddings`).

**Everything else is an external API behind a port interface:** Jev (OpenRouter), all general LLMs, Gemini vision, Sarvam translation, the hedge embedding model, the reranker, Qdrant Cloud, Langfuse, Grafana, email/SMS providers.

This is the line that makes the `clinical`-profile capability table in §2 non-negotiable: real patient data can reach the local models freely, but every external call on that list needs its own `phi_approved` sign-off before it can run on real patient text.

---

## 18. Authentication and authorization

### 18.1 Provider

**Supabase Auth, on the same Supabase project as the database — not a separate auth service in front of an independently hosted Postgres.** This means row-level security policies use Supabase's native `auth.uid()` inside the policy definition — the single RLS mechanism for this entire project. Role and clinic scope still live in application tables (`iam.accounts`, `iam.staff_roles`) joined against `auth.uid()` in each policy — Supabase's auth system knows *who*, the application schema knows *what they're allowed to touch*.

**The wrinkle, stated precisely so it can't be silently reintroduced as a different design:** `auth.uid()` reads from the Postgres session setting `request.jwt.claims`, which Supabase's own PostgREST layer populates automatically per request. This project's backend does **not** go through PostgREST — it talks to Postgres directly over SQLAlchemy Core + asyncpg (§3) — so nothing sets that claim automatically. The backend must do it itself: **verify the JWT independently on every request, then set the claim inside the same transaction wrapper already used for checkpointing (§7.2), before any query runs.**

**This must be done with `select set_config('request.jwt.claims', $1, true)`, passing the verified claims JSON as a bound parameter — never `SET LOCAL request.jwt.claims = '<value>'` built by string interpolation.** `SET`/`SET LOCAL` do not accept bind parameters for their value in the way a normal query does; constructing that statement by concatenating the claims JSON into SQL text makes an attacker-influenced string (a claim value can contain a quote or other SQL-breaking character even though the token itself is cryptographically verified) into a SQL injection surface. `set_config()` is an ordinary SQL function and takes a properly bound parameter like any other call; its third argument, `true`, gives it the identical transaction-local scoping `SET LOCAL` would have. `auth.uid()` then reads exactly what the backend just verified and set, regardless of which of these two mechanisms set it — this is a syntax correction, not a change to the design. This is the *only* correct way to make `auth.uid()` work outside PostgREST — setting a different, custom variable name (as an earlier, now-deleted draft of §17.1 did) does not integrate with `auth.uid()` at all and was a different, incompatible design, not a simplification of this one.

**No Docker anywhere in this project — development, CI, and production all use hosted infrastructure.** Local development and CI each use their own **hosted Supabase project** (Tokyo, `ap-northeast-1` — an accepted deviation, decided explicitly: both projects hold synthetic data only, and a Supabase project's region cannot be changed after creation; the production project must still be in an India region per §16.5), not the Supabase CLI's local Docker stack. A mocked or stand-in auth layer was considered and rejected for the same reason a Docker-based local stack was rejected in favor of hosted: the point of testing against the real thing is to catch the class of bug that only appears against production's actual identity mechanism, and a hosted dev project gives that without adding a container runtime as a project dependency. **Dev and CI use separate Supabase projects**, not one shared between them, so CI's test data and a developer's local exploration never collide.

**Connection modes: the application and the migrator use different Supabase pooler modes, on purpose.** The application's runtime connection (`DATABASE_URL_APP`, the restricted `careflow_app` role) uses the **transaction-mode pooler** (port 6543): its identity mechanism is request-scoped — one transaction per request, with `set_config('request.jwt.claims', $1, true)` transaction-local — so a connection returned to the pool after each transaction carries no identity into the next. The migration runner (`DATABASE_URL_MIGRATOR`) uses the **session-mode pooler** (port 5432), because its advisory lock is held across multiple statements and a transaction-mode pooler can hand the connection to a different caller mid-lock without erroring. Neither uses the direct connection, which is IPv6-only on new Supabase projects and commonly unreachable from GitHub-hosted runners. **Normative rule, not a tuning suggestion: behind the transaction-mode pooler the application must never rely on driver-side prepared statements.** The asyncpg statement cache is disabled (`statement_cache_size=0`), SQLAlchemy's asyncpg prepared-statement cache is disabled (`prepared_statement_cache_size=0`), and every prepared statement that is created carries a unique name (`prepared_statement_name_func`), so two clients can never collide on asyncpg's default sequential names. These settings live in one place (`careflow/db.py`) and are verified against a live connection, not only in code, by `scripts/verify_app_role.py`. Known consequence, accepted: a statement that errors can leave its uniquely-named prepared statement on the pooled backend until that backend is recycled; unique names make this harmless to correctness.

**`careflow_app` cannot call `auth.uid()` by name, and this is expected.** On hosted Supabase, schema `auth` is owned by `supabase_admin`; the `postgres` role holds `USAGE` on it without grant option, so nothing this project controls can grant `careflow_app` access to it (a `GRANT` attempted as `postgres` is a silent no-op). RLS policies still evaluate `auth.uid()` for the role — verified end to end — so every policy keeps using `auth.uid()` as specified above. Application code must not `select auth.uid()`: it already holds the verified claims it just set.

**Supabase Auth.** Patients: phone or email OTP; a device/session list; rate limits, lockouts, CAPTCHA on repeated failures; guardian proxy accounts for dependents. Staff: invitation-only (no public staff sign-up), **mandatory second factor** (authenticator app; passkey if supported — verify at build), enrolled at first login. Sensitive actions (merge, publish card/rule, autonomy-mode change, erasure, role change) require **step-up re-authentication** even within an active session. Staff account recovery goes through an admin plus a second admin — no self-service bypass of MFA.

### 18.2 SMS (patient notifications, India)

**DLT registration is mandatory before any real SMS delivery in India** — a message that doesn't match a pre-registered template is silently blocked at the carrier level, with no error returned. This is a **pre-launch blocker**, not an implementation detail: entity registration (~₹5,000–5,900, 2–7 business days to verify) + sender-header registration + per-template registration (7–21 business days total), done by the hospital as the registered principal entity. Templates are fixed, must include the brand name, and allow at most 5–6 variables; Tamil templates are registered separately from English ones.

Build an `SmsPort` interface with adapters for at least one India-first provider (e.g. MSG91 — verify its Supabase Auth integration path, likely via the Send-SMS-Hook, at build) and Twilio (global, separate DLT flow for India). **Demo mode runs against a sandbox sink, not a real provider.**

**Required mitigations:** rate limits per phone number and per IP; CAPTCHA on repeated OTP requests; a +91-only allowlist by default; a daily SMS-spend cap (protects against "SMS pumping" fraud); a delivery-rate alert (a silently-blocked template shows up as delivery rate, not as an error). SMS content **never** carries health details — booking/message nudges only. Staff never use SMS for MFA (authenticator app only, §18.1). Recycled-number risk (a phone number reassigned to a new person) is mitigated by requiring the DOB match at record-claim time (§17.2) and step-up checks before any sensitive view.

### 18.3 Email

`EmailPort` interface, two adapters: SendGrid (Twilio's email product — **no permanent free tier**; new accounts get a 60-day/100-per-day trial, then $19.95/month) and a free fallback (verify current free tiers at build — Brevo, Resend, and Mailtrap were candidates at spec time; confirm before committing). **The free fallback is the default active adapter**; SendGrid is built and selectable by configuration if the clinic chooses to pay for it. A delivery-failure alert fires if the active channel stops working, so a dead email channel is never silently dead.

### 18.4 Authorization

RBAC roles: `patient`, `guardian`, `front_desk`, `nurse`, `doctor`, `admin`, `business_owner` (aggregates only, no case-level access), `auditor` (read-only audit access). Enforced at **three layers**: the API policy layer, Postgres row-level security (clinic + patient-relationship scoping), and MCP tool-server scopes. **Deny by default.** A break-glass access path exists for emergencies, requires a stated reason, is audited immediately, and is reviewed after the fact. **A CI test exercises every route against every role** to catch authorization gaps before they ship. Service-to-service calls (agents → tool servers) use short-lived signed tokens per job, never a long-lived shared secret.

**The role-matrix test is a harness built once, in Phase 0, and populated phase by phase — never a complete matrix stubbed ahead of routes that don't exist yet.** Phase 0 has no feature routes to test; what it builds is the mechanism (the auth dependency, the parametrized fixture that asserts an allow/deny given a route and a role) plus one real, trivial route as proof it actually catches something. Every later phase that adds a route adds that route to this matrix **in the same phase**, as part of that phase's own definition of done in Appendix D — not stubbed early, and not deferred past the phase that introduces the route.

---

## 19. UI/UX specification

**"Best UI" is a build-time verification requirement, not a claim this document can make on its own — Claude Code must render every screen and check it with browser screenshots plus visual regression before considering a screen done.** This section defines the design system, information architecture, and required states; it does not substitute for looking at the rendered result.

### 19.1 Design system

Two densities sharing one token system, not two separate apps:
- **Patient surfaces:** calm, large, high-contrast. Older and low-vision users are a primary audience, not an edge case.
- **Staff/admin surfaces:** dense, keyboard-first, optimized for repeated use during a shift.

Requirements: WCAG 2.2 AA; full Tamil and English support (not just translated strings — check text expansion, line-wrapping, and date/number formatting); automated accessibility checks in CI (axe or equivalent); loading, empty, and error states defined for every screen (no screen ships with only its happy path designed); no stock photography of patients; placeholder branding (name, colors, logo, emergency number) until a real hospital supplies its own — do not invent a fictional hospital identity that could be mistaken for a real one.

### 19.2 Landing page (unauthenticated)

In order: hospital name + sign in/sign up + a static, backend-free "in an emergency call 108" banner → about the hospital (hospital-supplied content only — no superlative claims; **verify medical-advertising rules with counsel** before publishing anything promotional) → departments/doctors → features available (book, appointment offers by SMS/email, messages, records after first visit, family members, Tamil/English) → 3-step "how it works" → privacy promise → location/timings → FAQ → footer with grievance contact, privacy-notice link, and a small, deliberately unobtrusive "Staff sign in" link.

### 19.3 Post-authentication profile indicator

**Patient:** header shows initials avatar + name; menu contains profile, family-member switcher, language, notifications, security (devices/passkeys), privacy & consent (withdraw, download my data, request erasure), sign out.
**Staff:** adds a role badge and a role switcher (least-privilege — a user with multiple roles picks the active one rather than holding all permissions simultaneously) and the current clinic.
**Admin:** adds a persistent **LIVE / TEST** environment badge — the same visual and textual distinction used on the dashboard (§13.6), so it is never ambiguous which data an admin is looking at.

### 19.4 Sign-up fields

**Patient — required:** mobile number (OTP-verified), full name, date of birth, sex, preferred language (English/Tamil), consent to care delivery.
**Patient — optional:** email (recommended as a fallback notification channel), consent to AI-assisted processing, consent to service-improvement on de-identified data. A `voice_enabled` preference (§23) is available from the profile menu after registration, off by default — this is a setting the patient opts into later, not a signup-time question.
**Explicitly not collected at signup:** Aadhaar, address, ID proofs, insurance details, blood group — gathered later only if a specific visit requires them.
**Staff:** created by invitation only — name, work email, mobile, role, medical-council registration number (doctors/nurses); enrolls a second factor at first login (§18.1).

---

## 20. Consent, DPDP, and clinical content governance

### 20.1 Consent and patient rights

Separate, independently-withdrawable consent purposes: care delivery, AI-assisted processing, optional service-improvement on de-identified data. Withdrawal is one click. Access, correction, erasure, and nominee requests go through the grievance channel with tracking (DPDP requires a functioning grievance mechanism). **Under-18 accounts require guardian consent and a recorded proxy-booking trail — confirm specifics with counsel**, DPDP treats children's data specially. Calibration/fine-tuning training data draws only from de-identified, clinician-confirmed labels where the relevant consent was given; otherwise, synthetic data only.

### 20.2 Clinical content governance — the rule that protects against fabricated medicine

Every seeded protocol card, red-flag rule, and message template is created in a `draft_unreviewed` state and **is unusable in the `clinical` profile** until a named clinical lead approves it under the two-person rule (§12.5). **Claude Code must never invent a clinical threshold, dose, or red-flag phrase** — where the spec or a ticket calls for one and none has been supplied, insert `# CLINICAL-PLACEHOLDER: needs clinician sign-off` and stop, rather than writing a plausible-sounding number. The admin card editor validates against the card schema and runs the card's own embedded test cases before allowing a publish. Episode templates (e.g. an antenatal visit schedule) are clinician-authored content loaded the same way; the episode *engine* is generic and contains no embedded clinical assumptions of its own.

---

## 21. Testing strategy

**CI provider: GitHub Actions.** Not previously named — this is the decision, made explicitly rather than left for whoever writes the first workflow file to guess. Reasons: the repository lives on GitHub, no other CI system is already in use, and GitHub Actions' hosted runners need no Docker-based service container for Postgres — CI connects to its own hosted Supabase project (§18.1) exactly the way a developer's machine connects to the dev project, keeping "no Docker anywhere" true for CI as well as for local development. The integration and end-to-end test runs below execute as GitHub Actions jobs against that CI project, using the same restricted, `NOBYPASSRLS` application role production uses — never the Supabase secret key (`sb_secret_…`, the successor to the legacy service-role key), which is reserved for one-time setup operations (creating test accounts through the Auth admin API) and never used inside a test assertion itself.

- **Unit and graph tests:** pytest, run against fake adapters for every external port (`DecisionPort`, `EmbeddingPort`, `RerankPort`, `TranslationPort`, `EmailPort`, `SmsPort`, `QueuePort`) so the suite runs with no network access. A structural test walks every LangGraph graph definition and fails if any node both performs a side effect and pauses (§7.2).
- **Integration tests:** real PostgreSQL in CI, connected **as the application's actual restricted role**, not a superuser — row-level security must be genuinely exercised, not bypassed by the test harness. Cover: 200 concurrent booking attempts on one slot resolve to exactly one booking; idempotent retries (including concurrent duplicates) replay the original response; a clinic cannot read or write another clinic's rows, including child tables with no direct clinic column; the audit log cannot be edited, including by its owning role; a crashed job resumes from checkpoint without repeating a side effect; a job that exhausts its retries reaches the dead-letter table.
- **End-to-end tests:** the whole application booted (server, lifespan, MCP servers over HTTP, the job tick, real row-level security), external AI/storage services replaced at a single seam with deterministic stand-ins, driven over HTTP as the actual users (front desk, nurse, doctor, patient) through the full referral→triage→booking flow, the care-plan→conditional-referral flow, the emergency-escalation flow, and the cancellation-backfill flow.
- **Contract test:** the frontend's API client is driven for real (not mocked) against the backend's generated OpenAPI schema — asserts every method targets an endpoint that exists, with valid required parameters and body fields. A CI check separately fails if the checked-in schema snapshot has gone stale relative to the live backend.
- **Frontend:** Vitest + Testing Library component tests, plus the contract test above; a production build must succeed in CI.
- **Security/compliance gates:** dependency and secret scanning on every commit; the red-team and load-test suites from §14.6 run before any release promotion.

---

## 22. Visit Readiness agent

The fourth agent, covering the four capabilities the original five-agent design called Visit Prep
and Readiness & Attendance: a pre-visit clinical brief, an adaptive intake conversation, a
procedure-readiness confirmation, and no-show barrier resolution. One agent, four triggers — not
four separate flows glued together, and not a fifth agent, per the same reasoning that kept the
core build to three agents first: fewer agents proven thoroughly beats more agents proven thinly.

**Ship behind `FEATURE_VISIT_READINESS`, off by default, after Care Journey (Phase 7, Appendix D)** —
this is the most patient-conversation-heavy agent in the system, so it needs the guardrail and
evaluation layers already proven against three quieter agents first.

### 22.1 Pre-visit brief

**Trigger:** a booking is confirmed and reaches a configurable lead time before the visit (default
48 hours).

**What it does:** reads the patient's active medications, recent results, the referral reason, and
the matched card's declared facts, and produces a short structured brief for the clinician —
never for the patient. Built from cited, structured data through the same tiered grounding checks
as referral extraction (§11.2); free-text narrative sections are labeled model-generated and
carry no clinical weight of their own.

**Risk class: decorative.** If generation fails, the clinician sees the raw chart through the
existing tools — nothing is blocked, nothing is silent, and no automated decision depends on the
brief existing.

### 22.2 Adaptive intake conversation

**What decides which questions get asked — the gap, not a fixed list.** Every protocol card already declares a `facts` list (§10.2) — the facts its own priority rules and red-flag rules actually read (e.g. `exertional`, `abnormal_ecg`, `symptom_weeks`). This is extended with one field per fact: an `intake_question` — the clinician's own wording for asking it, authored and versioned exactly like the rest of the card (§20.2), never generated by a model. **Claude Code never invents an intake question, the same rule as thresholds.**

At the trigger point (same lead-time window as the brief, §22.1), the system checks each of the card's declared facts against what's already known for this patient — extracted from the referral letter and passed through the grounding tiers (§11.2), or already current in the chart. **Only the facts still missing or unverified are asked about.** A referral letter that already clearly stated the ECG finding is never asked about again; one that was silent on symptom duration is. The same card therefore produces a shorter conversation for a detailed referral than for a sparse one — the *content* of every possible question is fixed and clinician-owned in advance; the *selection* of which ones to actually ask is a deterministic gap computation, never a model's judgment call.

**How it runs, reusing existing infrastructure rather than building parallel machinery:**
- Each selected question is sent as a pre-approved template with the card's exact question text — this is why it doesn't violate the "no non-template outbound message" rule (§12.1): the template is the card's own governed content, not agent-generated prose. If the patient has `voice_enabled` (§19.4), the same question can be spoken instead of typed via the `voice_live` role (§5.1) — same text, same governance, different modality.
- **The deterministic red-flag rules run on every patient reply, before any model call, with no exception** — this is the same absolute rule from §5.2, unweakened by the conversation being scripted, and unweakened by the channel being voice (§11.6 covers the voice-specific mechanics of this). A patient can volunteer "I've also been having chest pain" in answer to an unrelated question, spoken or typed, and the rules must catch it exactly as they would in a referral letter.
- The reply is classified for intent first (a `decision`-engine call, closed-set: answers the question / asks a clinical question / expresses a barrier / unclear). A clinical question routes to a human per the existing scope-enforcement rule (§11.4) — this agent never answers one, no matter how simple it seems.
- An answer that resolves a declared fact is extracted and grounded (§11.2) before it's written as a fact the visit's card rules can use.
- Any fact still missing when the lead-time window closes becomes an "incomplete intake" flag for front desk to finish at check-in — never silently dropped, never guessed.

### 22.3 Procedure-readiness confirmation

**This list is deliberately separate from the intake fact-gap above, because the question it answers is different.** Intake asks "what does the card need to know to decide"; readiness asks "did the patient actually do what they were told to do" — a `readiness_checklist` (clinician-authored, e.g. "fasted 8 hours," "held anticoagulant per instructions," "arranged a driver") declared on the visit type itself, sent at a configured lead time before a procedure. It is never derived from a gap computation, because every item on it must always be confirmed regardless of what's already on file — "did you actually fast today" has no stand-in fact that could already be known.

**This is safety-relevant, not merely informational** — an incorrect "yes" or a missed "no" on an anticoagulant hold can mean a cancelled or unsafe procedure. Every checklist item's answer is classified into `confirmed` / `not-done` / `unclear`:
- `confirmed` on every item → the visit proceeds, logged.
- `not-done` or `unclear` on **any** item → **automatic nurse escalation, in every autonomy mode** (a locked row in §12.1) — this is never auto-resolved, because the cost of proceeding on a wrong assumption here is a harmed patient, not a wasted slot.

Spoken confirmation (§23) follows the identical classification and identical locked escalation — the channel a patient answers through never changes what a `not-done` answer triggers.

### 22.4 No-show barrier resolution

**Trigger:** no readiness or intake response by a configured point before the visit, or a reply
that isn't a simple confirmation.

**Barrier classification** is a closed-set `decision`-engine call (transport / cost / childcare /
forgot / scheduling conflict / other), never open-ended reasoning:
- **Scheduling conflict** → hands off to the existing Scheduling agent's reply-interpretation and
  rebooking flow (§ Scheduling agent) — this reuses that flow entirely rather than duplicating
  booking logic here. Automatic in every mode, same as any other reschedule, per the added row in
  §12.1.
- **Transport, cost, childcare, other social/resource barrier** → routed to front desk for human
  follow-up, in every mode. This agent identifies the barrier; it does not attempt to solve a
  transport or cost problem itself.
- **Feeds the `no_show_rate` and `followups_closed_vs_lost` metrics** already in `metrics-catalog.yaml`,
  and the barrier type is recorded so recurring barriers (e.g. most no-shows are transport-related
  on a particular weekday) become visible to the business owner over time — a pattern, not a
  one-off guess.

### 22.5 Tools and data

No new tool server. This agent uses the existing `clinical` read-only server (chart data for the brief and for the fact-gap check in §22.2), the existing message-thread and reply-interpretation pattern (§ Scheduling agent, §17.5 topics — intake and readiness messages use a new `readiness` topic, visible to the patient the same way `appointments` and `safety` already are), the `voice_live` role (§5.1) when the patient has opted in, and hands off to the existing Scheduling agent for any actual rebooking. No new external API beyond `voice_live` itself, and no new `data_policy` entries beyond the one already declared for it.

---

## 23. Voice channel

Voice is **in-app audio inside the patient's already-authenticated portal session** — a mic button, like a voice-mode toggle in a chat app — not a phone call. This is a deliberate correction from an earlier telephony-based design (Twilio, a phone number, carrier minutes): once the actual requirement turned out to be "a patient can opt into speaking instead of typing," the entire telephony layer, its regulatory registration burden, and its caller-identity problem stopped applying, because there is no phone number and no unauthenticated caller to identify — the patient is already signed in before the mic button ever appears.

**Ship behind `FEATURE_VOICE_CHANNEL` at the clinic level.** Even then, a patient must separately set `voice_enabled` in their own profile (§19.4) before the mic button appears at all — this is opted into per patient, the same way a chat app's voice mode is a setting, not a default.

### 23.1 The API: Google Gemini Live

One native speech-to-speech connection over a browser WebSocket handles listening, understanding, and speaking — no separate speech-to-text and text-to-speech services to assemble and no telephony bridge underneath it. Chosen specifically because it has a genuine, ongoing free tier through Google AI Studio (rate-limited, not a one-time credit that runs out) — the only realistic voice API found with that property; every telephony-oriented alternative checked (Twilio, Vapi, Retell, OpenAI's Realtime API) converges on either a small one-time signup credit or no free tier at all. `synthetic_only` at launch (§5.1) — patient speech leaves the browser for Google's API, so `clinical` profile requires the same kind of data-processing agreement already required for Gemini's document-vision role (§2) before this can touch real patient conversations.

### 23.2 Scope — deliberately narrow, and why that narrowness is what makes §11.6 sufficient

In scope for v1: spoken replies to a booking or backfill offer (§ Scheduling agent), and spoken answers to the fact-gap intake questions and the readiness checklist (§22.2, §22.3). Out of scope, and recorded as a deliberate deferral rather than an oversight (Appendix A): open-ended referral intake or free-form history-taking by voice. A native speech-to-speech model has the whole conversation itself rather than pausing before every sentence (§11.6) — keeping the channel's entire vocabulary to "which slot" and a short, fixed list of clinician-authored questions is what makes the scope-restriction half of §11.6's two-layer guardrail actually hold. An open-ended conversation would need the same async extraction-and-grounding pipeline a referral letter gets (§10, §11.2), which a live spoken exchange structurally cannot wait for.

### 23.3 Safety

Fully specified in §11.6, not repeated here — the guardrail lives in Layer 8, where every other guardrail in this spec lives, rather than being a voice-specific bolt-on. The one-line summary: system-instruction scoping is the first line of defense, the deterministic red-flag rules watching the live transcript in parallel are the second, and the honest gap between those two is named and scheduled for measurement at build (Appendix B) rather than assumed away.

### 23.4 Consent

Opening the mic prompts a spoken and on-screen consent and recording disclosure before any audio is processed — added to §20.1's consent purposes as a per-session event, distinct from the account-level consents collected at signup, since DPDP treats a recorded voice session as its own instance of processing.

### 23.5 Degradation and cost

Both fully covered where every other dependency's degradation and cost already live: the degradation row is in §16.2, the cost treatment is in §15.1. Voice is **decorative** relative to the text channel it sits beside — its total absence changes nothing about what a patient can accomplish, only which modality they use to do it.

## 24. Care gap review

"Gap review" turned out to name two related but distinct mechanisms already implicit elsewhere in
this spec. Both are made explicit here as one dashboard surface, so "which patients have fallen
through the cracks" has a real screen rather than living only in two different sections' prose.

### 24.1 What it shows

A single **Care Gaps** panel on the Live dashboard (§13.6), combining:

- **Overdue items** — from care episodes and recurrence rules (§9.3): a missed annual review, an
  overdue pregnancy check, a care-plan conditional step waiting past its expected result window.
  Computed live from the same due-date data those features already store; no new agent, no new
  model call.
- **The after-the-fact review queue** (§12.6) — the 5% sample of auto-approved decisions awaiting
  human re-check, and its current error rate, surfaced here rather than only in the raw review
  queue, so a rising error rate on the automatic path is visible without hunting for it.
- **Unresolved barriers** (§22.4) — barriers flagged as social/resource and still open, aged by how
  long they've sat unresolved.

### 24.2 What it is not

Not a new agent and not a new automated action. Every row on this panel is something a human
already needs to look at — the panel's job is to make sure that need is visible in one place on a
cadence, not to let anything on it auto-resolve. An item that sits on this panel past a configurable
age raises the same kind of review-queue-overdue alert already defined in `metrics-catalog.yaml`
(`review_task_overdue`), extended to cover these three sources.

### 24.3 New metrics

Added to `metrics-catalog.yaml`: `care_gap_open_count` (by source: overdue-recurrence /
after-the-fact-review / unresolved-barrier), `care_gap_median_age`, and
`voice_safety_escalation_rate` (mirrors `red_flag_task_overdue`'s urgency for the voice channel
specifically, since a missed voice escalation has no text trail to catch it later the way a missed
message might).

---

## Appendix A — Rejected options, with reasons (do not silently re-introduce)

| Option | Rejected because |
|---|---|
| A custom `app.clinic_id` session variable (set via `SET LOCAL`, read by a hand-written `app_clinic_id()` function) as the RLS identity mechanism | Superseded by `auth.uid()` (§18.1). It would have worked technically, but it duplicates what Supabase's own `auth.uid()` already does, doesn't integrate with any other Supabase-native RLS helper, and — critically — an earlier draft of this specification stated both this design and the `auth.uid()` design in two different sections without reconciling them, a genuine self-contradiction caught only when the actual implementation tried to follow both at once. Recorded here specifically so it is never reintroduced as if it were a simplification |
| Event-sourcing as the execution model | The lineage/audit benefits are already delivered by checkpointing + the append-only audit log + per-decision provenance; full event-sourcing would add an immutable-schema tax and a dedup burden on every replayed side effect for a benefit already achieved another way |
| A message broker (Redis/SQS) as the primary queue | This workload's jobs wait days for a human or an external result — a broker's dispatch-latency advantage (milliseconds) is worthless here, while it adds a second always-on service, a second keep-alive job, and a split-brain risk between the broker and the database |
| Mem0 (or any agent-owned patient memory store) | The facts worth remembering are already typed, validated, versioned clinical rows; a model-extracted second copy would be unversioned, unapproved by any clinician, outside retention rules, and a second erasure path to maintain |
| Prose/LLM-generated conversation summaries as working memory | Summarization of clinical conversation drifts; a lost or misremembered clinical fact from a bad summary is a patient-safety risk a typed state object doesn't have |
| A dedicated document-level vector alongside chunk vectors | The one-context-line-per-chunk approach recovers the same cross-section retrieval context at lower cost and one fewer moving part |
| `sentence-transformers/embeddinggemma-300m-medical` as the primary embedding | Fine-tuned for literature question-passage retrieval (scientific-paper Q&A), English-only — wrong task shape for referral-shorthand-to-protocol matching |
| MedCPT bi-encoder as the primary embedding | 64-token query cap; referral letters run 400–800 tokens |
| Verbatim source-quote matching as the sole grounding check | Rejects legitimate facts whenever a model correctly normalizes something (date format, abbreviation expansion), flooding the review queue with false alarms and training reviewers to rubber-stamp |
| Presidio with a name-detection model | Requires a local spaCy/transformer model, outside the local-footprint rule; pattern recognizers + masking of the patient's own known values is the accepted trade-off, with third-party names as a known, accepted gap |
| A single global "tool health" flag (healthy/unhealthy) | Collapses two different questions — "is the tool reachable" and "does this specific request need it" — into one; the load-bearing/decorative distinction is declared per tool *per workflow* instead |
| Patient-note vectors in Postgres/pgvector | Considered as the lower-risk default (one store, one erasure path, freshness from SQL); superseded by an explicit decision to keep all vectors in Qdrant — accept the consequence that `clinical`-profile notes search now depends on a Qdrant deployment under agreement (§16.6), not on Postgres alone |
| A fifth agent, splitting Visit Prep and Readiness & Attendance apart | Both fire in the same pre-visit window, read the same chart data, and would dilute the "fewer agents, proven thoroughly" build discipline from §1 for no functional gain — one Visit Readiness agent with four triggers (§22) does the same job |
| Open-ended referral intake or free-form history-taking by voice, in v1 | A native speech-to-speech model has the whole exchange itself rather than pausing before every sentence (§11.6); keeping the channel to a short, fixed vocabulary of slot offers and clinician-authored questions is what makes the scope-restriction guardrail hold. Open-ended extraction from live speech needs the same async grounding pipeline as a referral letter (§10, §11.2) — deferred to a later, dedicated design pass (§23.2) |
| Telephony (Twilio/Vapi/Retell, a real phone number) as the voice channel | Considered first; superseded once the actual requirement turned out to be in-app audio inside an already-authenticated session, not a phone call. Telephony would have added a real, unavoidable carrier cost, a separate India regulatory registration burden, and a caller-identity problem — none of which apply once there's no phone number and the patient is already signed in. Recorded here because the earlier design was real analysis, not a false start to hide |
| A managed voice-agent platform's own hosted response generation (rather than a bring-your-own-backend mode) for any voice architecture | Would let a model compose and speak a reply to a patient before the deterministic red-flag rules ever saw the transcript — a direct violation of the absolute ordering rule in §5.2. Not acceptable regardless of which vendor, which is why §11.6's scope-restriction-plus-parallel-monitoring pattern exists even for a native speech-to-speech API that has no "bring your own backend for every turn" mode to fall back on |

---

## Appendix B — Verify at build (do not assume; these were researched, not hands-on tested)

- Laya's actual CPU latency (p50/p95) on the target build machine — no GPU benchmark applies.
- Jev's request shape via OpenRouter (a "decisions" endpoint, distinct from `/chat/completions` — the adapter cannot reuse a standard OpenAI-compatible client without checking this first).
- `NeuML/biomedbert-base-reranker`'s actual availability and behavior via HF hosted inference (low usage at spec time — less battle-tested than its lineage suggests).
- DeepEval's support for a fully custom judge model/client.
- Guardrails AI's Provenance-NLI validator internals, to confirm it needs redirecting to a hosted/decision-engine NLI check rather than its bundled local model.
- MSG91 (or chosen India SMS provider) integration path with Supabase Auth — likely the Send-SMS-Hook, not a natively-listed provider.
- Current free-email-provider tiers (Brevo/Resend/Mailtrap or equivalents) — these change without notice.
- Sarvam's Mayura API current pricing, rate limits, and exact input-length cap in production (spec-time figures: ~₹0.005/char, 1,000-char input cap — confirm before relying on either).
- Uploadcare's malware-scanning capability and how to enable it — required before real (non-demo) use.
- Whether hosted embedding-inference APIs (for the hedge embedding model) return token-level vectors or pooled-only — this determines whether late chunking (§10.3.6) is possible at all without self-hosting.
- Passkey support in Supabase Auth for staff MFA (spec assumed authenticator-app TOTP as the safe baseline; passkeys would be a strict upgrade if supported).
- **RESOLVED (dev and CI, each confirmed by a real call to that project's own JWKS endpoint):** *which signing method the hosted project's Auth uses, and whether verification uses a shared secret or JWKS.* Finding: both projects use **asymmetric JWT Signing Keys** (ES256, EC keys) published at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`; `SUPABASE_JWT_SECRET` stays blank, since the two are mutually exclusive. Each JWKS currently lists one key, but a rotation window can list a current and a previous key at once, so verification must select the key by the token header's `kid` and never take the first entry in the list. The signing method is per project: a project created later must be checked, not assumed to match.
- **RESOLVED (dev and CI, each confirmed by a real call):** *which Supabase connection string supports session-level advisory locks.* Finding: the **session-mode pooler** (pooler host, port 5432, user `postgres.<project-ref>`) holds a session-level advisory lock across multiple statements — the same server backend served every statement while the lock was held, a second session could not take the held lock, and could once it was released. The direct connection was deliberately not tested (IPv6-only on new projects, commonly unreachable from GitHub-hosted runners). The transaction-mode pooler (port 6543) is not used for the migrator; it is the application's runtime connection (§18.1, connection modes). **Also confirmed from a GitHub-hosted runner:** the first CI workflow run on `main` (GitHub Actions run 36845835671, commit `6da1ca5`, 2026-10-01) applied the migrations to the CI project through the session-mode pooler from an `ubuntu-latest` runner and went green, so the advisory-lock path works from a hosted runner as well as from a developer machine.
- Qdrant's per-tenant/per-clinic access-control mechanism, to confirm it can enforce the clinic-isolation guarantee at the database layer and not only in application code.
- **The actual latency between a patient finishing a concerning sentence in a `voice_live` session and the deterministic-rule interrupt landing (§11.6)** — this is the honest gap named in the voice guardrail design, and it must be measured, not assumed, before voice ships past Phase 8 (Appendix D).
- Google AI Studio's exact Gemini Live free-tier rate limits (requests/concurrent-sessions/minutes) in production, and whether they're sufficient for expected clinic volume before this needs a paid Vertex AI tier.
- Whether Gemini Live supports updating a session's system instructions mid-conversation, which the scope-restriction design in §23.2 assumes is possible per workflow (a booking-reply session and an intake-question session should each be scoped only to their own task).

---

## Appendix C — Traceability to the reference architecture's 17 layers

| Reference layer | This spec's section | Departure, if any |
|---|---|---|
| 1. Use-case & architecture decision | §1, §4 | — |
| 2. Model selection | §5 | Added a `decision` engine tier (Laya/Jev) not in the generic layer model |
| 3. AI gateway | §6.1 | In-process module + Cloudflare pass-through, not a standalone gateway service |
| 4. Orchestration framework | §7.1 | — |
| 5. Orchestration reliability | §7.2–7.4 | — |
| 6. Tools/function calling | §8 | Split into two MCP servers by read/write, not the generic single-server model |
| 7. Retrieval (RAG) | §10 | No RAG framework; hand-rolled per §10.1 |
| 8. Memory | §9 | No agent-owned memory; clinical record does this work instead |
| 9. Guardrails & safety | §11 | Tiered grounding replaces simple verbatim/entailment checking |
| 10. Evaluation | §14 | Origin-tagged (live/test/eval/canary) dashboarding is an addition |
| 11. Observability | §13 | — |
| 12. Infrastructure & deployment | §16.3–16.5 | Split topology (hosted API/DB + owner-operated model workers), not a single deployment target |
| 13. Security & compliance | §16.5, §18, §20 | DPDP-specific, not the generic compliance layer |
| 14. Cost management | §15 | — |
| 15. Org & process | §12.5–12.6, §16.4, §20.2 | Two-person rules and clinical-content governance are domain-specific additions |
| 16. Testing & QA for agent code | §21 | — |
| 17. Caching | §6.2 | Four distinct mechanisms, explicitly enumerated rather than treated as one layer |
| — (domain addition, no reference-layer equivalent) | §22 Visit Readiness, §23 Voice, §24 Care gap review | Added after an explicit gap audit against the original five-agent design; not present in the reference architecture's 17 layers because they're product scope, not infrastructure layers |

**Production-readiness scorecard self-assessment at spec-completion (by design only — re-score once built):** ≈85/100. The gaps closed by this spec versus an earlier draft were: authentication/authorization (§18), the rollback mechanism (§16.3–16.4), and runbooks (Appendix D lists them as a build deliverable). Re-run the actual scorecard checklist (§14.6) once the system is built — a self-assessment is not evidence.

---

## Appendix D — Phased build plan

**Phase 0 — Foundations.** Migrations ledger + advisory lock; **all 11 schema namespaces from §17.1 created now, but only `iam`, `audit`, and the migration ledger itself get full table definitions in this phase** — every other schema (`mpi`, `sched`, `clinical`, `docs`, `msg`, `kb`, `agents`, `ai`, `ops`) is created empty, with its tables added in the phase that actually uses them. This is deliberate, not an oversight: designing a table's columns, constraints, and RLS policy before the feature that needs it exists means guessing at a shape nothing has confirmed yet — the same reasoning CLAUDE.md applies to inventing a clinical threshold applies here to inventing a schema. Origin tags and bi-temporal columns (§9.5) are part of each table's design from the migration that creates it, not retrofitted later. Auth (§18.1, §18.4) — Supabase Auth on the same project as the database, `auth.uid()`-based RLS with the backend setting `request.jwt.claims` itself per transaction via `set_config()`, separate hosted Supabase projects for dev and CI (no Docker), CI running on GitHub Actions (§21) — with the role-matrix **test harness** and one real route proving it catches a violation, not a complete matrix of stubbed routes; the data-flow register (§17.6) as a living document, empty but structured, from day one.

**Phase 1 — Deterministic core, no agents yet.** Scheduling (slots, holds, exclusion constraints, idempotent booking, waitlist) fully working via the staff console and a minimal patient portal; MPI (§17.2) with its matching, encryption, and merge/unmerge, **including the actual key store and rotation runbook the §17.2 schema decision deferred from Phase 0**; the red-flag rule engine (deterministic layer only, no model second-opinion yet) with its own gold-set regression tests; the transactional outbox and the Postgres-backed queue (§7.4) with the ceilings from §7.3 enforced and tested.

**Phase 2 — Front Door agent.** Document upload and the extraction pipeline (§10.4); the `decision` engine tier (§5.2) with the three-adapter contract test; the tiered grounding checks (§11.2); protocol-card retrieval (§10) with its own gold set and CI gate; the Front Door graph itself, with the structural no-side-effect-across-a-pause test (§7.2) as a hard CI gate before this phase is considered done.

**Phase 3 — Scheduling agent.** Slot offer/reply/book over the MCP scheduling server; reply interpretation via the `decision` tier; cancellation backfill ranked by clinical priority, with its own end-to-end test proving a higher-priority waitlisted patient is offered a freed slot ahead of a longer-waiting lower-priority one.

**Phase 4 — Human approval, observability, cost.** The full review console (§12) including canaries and two-person rules; Langfuse + Grafana + the in-app watchdog (§13) wired to real traces; the cost canvas (§15) computed from the first real week of Phase 2–3 traffic, not estimated in advance.

**Phase 5 — Evaluation and gates.** The full gold set (§14.1) authored and clinician-reviewed; every hard and threshold gate (§14.3) wired into CI; the pre-deployment checklist (§14.6) run for the first time — this phase does not end until the scorecard is actually ≥70/100 with evidence, not by assertion.

**Phase 6 — Care Journey agent (flagged).** Plan-to-tasks parsing, doctor confirmation, result-waiting via the prefix wait-claim mechanism, the conditional-referral close-out, and its own end-to-end test proving a report lacking the needed value routes to a doctor rather than the loop silently closing as "not met."

**Phase 7 — Visit Readiness agent (flagged).** Card schema extended with `intake_questions` and `readiness_checklist` (§22.1–22.3); pre-visit brief generation with its own grounding checks; the adaptive intake conversation reusing the red-flag and scope-enforcement rules exactly, with an end-to-end test proving a volunteered danger phrase mid-conversation still escalates; the readiness-checklist escalation path, with a test proving any `not-done`/`unclear` answer reaches a nurse in every autonomy mode; barrier classification handing off cleanly to the existing Scheduling agent for the rebooking case.

**Phase 8 — In-app voice channel (flagged).** The `voice_live` role wired to Gemini Live over a browser WebSocket (§23.1); the scope-restricted system instructions per workflow (§23.2); the real-time transcript-monitoring guardrail (§11.6), with its own test proving a red flag spoken mid-session triggers the same safety response and nurse escalation as every other channel, and a measured (not assumed) figure for the interrupt-latency gap named in that section; the patient-level `voice_enabled` opt-in (§19.4) confirmed to gate the mic button independently of the clinic-level feature flag.

**Phase 9 — Care gap review.** The combined dashboard panel (§24.1) over already-existing data (recurrence rules, after-the-fact review, barrier tracking) — no new agent, so this is primarily an observability and query-correctness phase, gated by confirming every row on the panel is reachable from a real overdue/open item in the seed data.

**Phase 10 — Pre-launch.** The runbooks (overdue red flag, dead-letter growth, model outage, wrong merge, breach); the rollback drill, executed and timed; the restore drill; the DLT/SMS registration process (§18.2) started (it takes weeks — start this well before Phase 7 actually, in parallel with Phase 4–5); the `clinical`-profile capability gaps (§2) either closed (an approved LLM/vision route secured) or explicitly signed off as accepted for a `demo`-only launch.

Each phase ends with its own acceptance criteria drawn from the gates already defined above — this plan does not introduce new criteria, it only sequences the ones already specified.
