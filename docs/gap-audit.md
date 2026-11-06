# Gap audit: BUILD-SPEC.md against the code (2026-11-06)

Every spec item that is **not built**, **built only to a fake or a port**, or **needs a decision**, with one line of reason. Items that are built and tested against the real database or as pure code are listed under "Built" only in summary. Status words: **FAKE** = a port with a fake/stand-in and no real adapter; **NOT BUILT**; **DECISION** = built or buildable but waits for an owner, clinician, provider or counsel answer; **UNMEASURED** = a mechanism exists, the real number does not. Companion documents: `docs/design/pre-launch-checklist.md` (rows 1 to 57, owners and closing evidence), `docs/design/remaining-work.md` (blocked list), `docs/design/phase*-*.md`.

## Built (summary, not itemised)
Migrations ledger and advisory lock; row-level security with a role-matrix test per route; MPI with encryption, tokens, merge and unmerge state machine, erasure and key rotation; scheduling with exclusion constraints, holds, waitlist, backfill; the deterministic red-flag engine (mechanism only) with rules-first ordering on every path including a live voice transcript; Front Door, Scheduling, Care Journey and Visit Readiness graphs with structural gates; review console, deadlines, rubber-stamp statistics, canaries, learning labels, in-app watchdog and alerts; gateway with ceilings, breakers and a spend ledger; evaluation scorecard mechanism; cost canvas; text guard heuristics.

## 1 to 4. Scope, profiles, architecture, agents
| Item | Status | Reason |
|---|---|---|
| Care Journey, Visit Readiness graphs (§1, §22) | FAKE | Graphs, schemas and tests exist behind flags; no plan, visit-fact or readiness-answer tables, no routes, no real model roles |
| Voice channel (§23) | FAKE | Mic gating, scoped prompts, live-transcript monitor, tables and session route exist; no engine, so the route answers 503 and the interrupt gap is unmeasured |
| `clinical` profile capability gaps (§2 table) | DECISION | Each row is closed by an approved route or accepted off for a demo-only launch; the sign-off sheet is blank (`phase10-pre-launch.md`) |
| FHIR R4-shaped JSONB validated by a FHIR library (§3) | NOT BUILT | No `clinical` schema tables exist yet |
| Qdrant vectors, per-clinic access control (§3, Appendix B) | FAKE | `VectorStorePort` only; the tenancy mechanism is an Appendix B spike |
| Two MCP servers (§3, §8) | FAKE | `scheduling` write tools and a read-only `clinical` server exist in process; the clinical server has no chart data to read |

## 5 and 6. Models, decision tier, gateway, caching
| Item | Status | Reason |
|---|---|---|
| Real model adapters (vision, decision hosted, translation, embeddings, rerank, `llm_*`) (§5.1) | FAKE | Only fakes are merged; the hosted adapters live on the unmerged branch `c1-providers` (synthetic demo only) and need provider approvals |
| Laya local engine (§5.2) | NOT BUILT | Adapter over an injectable client exists; no local model is installed; CPU latency is an Appendix B spike |
| Three-engine contract test (§14.3 hard gate) | UNMEASURED | Needs real engines and a clinician-reviewed set |
| Safety-net second opinion, fail-closed (§5.2, §16.2) | NOT BUILT | Escalate-only design needs a clinician-designed path |
| Model deprecation handling (§5.3) | NOT BUILT | No deprecation watcher or re-pin procedure beyond pinned registry rows |
| Four caching mechanisms (§6.2) | PARTIAL | Prompt files are byte-stable and CI-scanned (prefix caching); no semantic, result or embedding cache is built |

## 7 and 9. Orchestration, queue, memory
| Item | Status | Reason |
|---|---|---|
| Outbox and event-to-job supervisor producers (§7.4) | NOT BUILT | Tables exist; nothing produces events (checklist 42) |
| Expired-hold sweep scheduled in production (§7.4) | DECISION | Function and sweep exist; scheduling it needs the worker identity decision |
| Redis/SQS queue adapter stub (§7.4) | NOT BUILT | Deferred by the spec until volume needs it |
| Bi-temporal clinical state: vitals, labs, medications, notes (§9.5, §9.6) | NOT BUILT | No `clinical` tables; the Care Journey result source and the brief need them |
| Care episodes and recurrence rules (§9.3) | PARTIAL | Tables and panel exist (0044) but nothing creates episodes or rules; clinician-authored content is missing |
| Retention enforcement job and purge role (§9.4) | DECISION | Design approved; periods are required settings; a purge needs its own grants and review (checklist 4) |
| Context assembler with a token budget (§9.2) | NOT BUILT | No model call needs it yet |

## 8, 10 and 11. Tools, retrieval, guardrails
| Item | Status | Reason |
|---|---|---|
| Retrieval pipeline end to end (§10) | FAKE | BM25 and fusion are pure code; no embeddings, Qdrant, reranker or chunking against real documents; the real-PDF spike is open |
| Protocol cards (§10.2, §20.2) | DECISION | `kb.cards` does not exist; every card, threshold and `intake_question` is clinician content |
| Red-flag rules content and the 100% recall gate (§5.2, §14.3) | DECISION | Mechanism only; rules need two named clinician approvers; no approval route exists (checklist 19) |
| Retrieval quality gate and abstention (§10.8, §10.9) | UNMEASURED | Needs the gold set and thresholds |
| Instruction-injection classifier (§11.1) | PARTIAL | Heuristics only; the hard gate needs a clinician-authored adversarial set (checklist 41) |
| PII masking before retrieval (§11.3) | PARTIAL | Heuristic masker exists; not wired to a real retriever |
| Scope enforcement classifier (§11.4) | PARTIAL | Rule routes exist for replies and voice; the model-based classifier is a port |
| Upload malware scanning (§11.5) | NOT BUILT | ClamAV adapter code exists; where the worker machine lives is undecided (checklist 2) |

## 12 and 13. Approval, observability
| Item | Status | Reason |
|---|---|---|
| Autonomy mode switch route and two-person act (§12.1, §12.5) | NOT BUILT | Pure check exists; mode stays `shadow`; clinic settings routes wait on the owner |
| Reviewer free-text reason note (§12.2) | DECISION | Owner decided not to store it |
| Learning loop: label export (§12.6) | PARTIAL | Pseudonymised label rows are built; no export or training job |
| After-the-fact 5% sampler (§12.6, §24) | NOT BUILT | Table exists (0044); the sampling step has no producer |
| Langfuse traces, Grafana metrics (§13.1, §13.2) | FAKE | Owner decided they stay fakes; dashboard and alert rules are generated files only, no panel has a query |
| E-mail and chat alert senders (§13.4) | FAKE | Ports with fakes; external services not approved |
| Test vs live dashboard page (§13.6) | NOT BUILT | Generated definition only; no page |

## 14 and 15. Evaluation, cost
| Item | Status | Reason |
|---|---|---|
| Gold set, held-out split, thresholds, judge calibration (§14.1 to 14.3) | DECISION | Mechanism built; no reviewed case exists; the three bars are clinician-set |
| DeepEval, RAGAS, Langfuse experiments (§14.2) | NOT BUILT | New external dependencies, not approved |
| Pre-deployment go/no-go and readiness scorecard (§14.6) | UNMEASURED | Checker built; every evidence cell is empty, answer is NO-GO |
| Business KPIs (§14.5) | NOT BUILT | Needs a baseline and owner-set targets |
| Autonomy promotion evidence (§14.7) | UNMEASURED | Demotion rule is pure code; promotion needs months of data |
| Cost canvas F and total (§15.1) | DECISION | Owner left F and the total null |
| Budgets, cascade, attribution (§15.2 to 15.4) | PARTIAL | Ceilings and a durable ledger for one path; the cheap-then-expensive cascade runs only in the gateway for `decision` |

## 16 to 18. Reliability, data model, auth
| Item | Status | Reason |
|---|---|---|
| Production hosting, India region, secret manager, rollout ladder (§16.3, §16.4) | DECISION | Dev and CI are Tokyo and synthetic; production is the owner's |
| Backup, rollback and restore drills (§16.5, §14.6) | UNMEASURED | Checkers and runbooks built; no drill run |
| Vector storage location (§16.6) | DECISION | Depends on the Qdrant tenancy spike |
| Documents pipeline with Uploadcare/Supabase signed uploads (§17.3) | FAKE | Storage adapter and the 0027 policy exist; the signed-upload spike and the bucket policy approval are open |
| Messaging topics incl. the `readiness` topic (§17.5, §22.5) | NOT BUILT | Appointments and safety topics exist; `readiness` needs a migration |
| Patient portal backend: consent, activation, guardian, claim by contact + DOB (§17.2, §18.4) | NOT BUILT | Only the voice preference and session routes exist for patients (checklist, remaining-work B16) |
| SMS via DLT (§18.2) | DECISION | Guard built; nothing registered, no provider; the owner starts registration |
| Email provider (§18.3) | FAKE | Port only; provider tiers are an Appendix B check |
| Staff MFA and passkeys (§18.1) | PARTIAL | TOTP enforced in the API; a database-level `aal2` policy and passkeys are undecided (checklist 44) |
| Staff invite flow with the Auth admin API | DECISION | The admin call stays unwired by the owner |
| Slot, provider and location creation (§17.1) | PARTIAL | Set-up routes exist for front desk and admin; who owns them is an owner decision (checklist 45) |

## 19 to 21. UI, consent, governance, testing
| Item | Status | Reason |
|---|---|---|
| UI (§19) | OUT OF SCOPE HERE | The frontend is a separate workstream, not touched by this build |
| Tamil strings reviewed (§19, checklist 43) | DECISION | 419 of 419 unreviewed, including the emergency banner |
| Consent records and notices, per-session voice consent as an event (§20.1, §23.4) | PARTIAL | A session row records consent time; the account-level consent store, notice versions and rights requests do not exist |
| Clinical content governance (§20.2) | BUILT as rules | Enforced by process and the reviewer agent; all clinical content itself is missing by design |
| Dependency vulnerability scan, visual regression (§21) | PARTIAL | pip-audit job report-only; no visual regression |
| Chaos tests, load test at N times peak (§21, §14.6) | NOT BUILT | Need hosting and a target load |

## 22 to 24. Visit Readiness, voice, care gaps
| Item | Status | Reason |
|---|---|---|
| Visit Readiness storage, intake and readiness content, lead times | DECISION | Clinician content and a migration package |
| Voice real engine, measured interrupt gap, shadow price (§11.6, §23.1, Appendix B) | DECISION | No real engine by owner decision; the gap and price are unmeasured |
| Care gap data producers: episodes, rules, samples; worker-raised overdue alert | NOT BUILT | Tables and panel exist; producers and a worker read policy do not |
| Care-gap metrics on the dashboard (§24.3) | PARTIAL | Values computed in code; no dashboard query |

## Appendix B items still unverified
Laya latency; Jev request shape; the reranker model's hosted availability; DeepEval custom judge; Guardrails NLI; the SMS provider hook; free e-mail tiers; Sarvam limits; Uploadcare scanning; hosted embedding output shape; passkeys; Qdrant tenancy; the voice interrupt gap and Gemini Live limits; mid-session system-instruction updates; the signed-upload overwrite and validity behaviour.
