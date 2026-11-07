# Gap audit: BUILD-SPEC.md against the code (re-read in full 2026-11-08, after migration 0057)

Every spec item that is **not built**, **built only to a fake or a port**, or **needs a decision**, with one line of reason. Status words: **FAKE** = a port with a fake/stand-in and no real adapter; **NOT BUILT**; **DECISION** = built or buildable but waits for an owner, clinician, provider or counsel answer; **UNMEASURED** = a mechanism exists, the real number does not. Companion documents: `docs/design/pre-launch-checklist.md` (rows 1 to 57, owners and closing evidence), `docs/design/remaining-work.md` (blocked list), `docs/design/phase*-*.md`.

Everything not listed here is built and tested (against the real database, as pure code, or in process with fakes where a row says so). Only what is still fake, unbuilt, undecided or unmeasured is listed.

## 1 to 4. Scope, profiles, architecture, agents
| Item | Status | Reason |
|---|---|---|
| Care Journey graph (§1, Phase 6) | FAKE | The plan MODEL is a port with no approved route (a plan is held for a doctor); the task priority and role are required settings nobody has set (the routes answer 503). Erasure of a patient now erases and closes their plans (0056, 0057) |
| Visit Readiness graph (§22) | FAKE | The five MODEL roles have no approved route (every reply is unclear, re-asked once, then front desk). Questions and checklists are the required setting `CAREFLOW_VISIT_READINESS_CONTENT` (only a labelled demo placeholder exists). Not built: a patient-facing channel for the replies (owner decision: none; a nurse or doctor relays them), the brief narrative, a real rebooking hand-off (it raises a front-desk task), the lead-time trigger that starts a run |
| Voice channel (§23) | FAKE | Mic gating, scoped prompts, live-transcript monitor, tables and session route exist; no engine, so the route answers 503 and the interrupt gap is unmeasured |
| `clinical` profile capability gaps (§2 table) | DECISION | Each row is closed by an approved route or accepted off for a demo-only launch; the sign-off sheet is blank (`phase10-pre-launch.md`) |
| FHIR R4-shaped JSONB validated by a FHIR library (§3, §17.1) | NOT BUILT | `clinical.results` holds plain numbers, not FHIR resources, and no FHIR library is used. Vitals, medications, notes and resource history are not stored |
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
| Outbox and event-to-job supervisor producers (§7.4) | PARTIAL | Table, relay and tick exist. Producers: the document run job (direct enqueue) and a recorded result (an outbox event, written in the result's own transaction; the worker identity wakes the plan). Other state changes emit no event |
| Expired-hold sweep scheduled in production (§7.4) | DECISION | Function and sweep exist; scheduling it needs the worker identity decision |
| Redis/SQS queue adapter stub (§7.4) | NOT BUILT | Deferred by the spec until volume needs it |
| Bi-temporal clinical state: vitals, labs, medications, notes (§9.5, §9.6) | PARTIAL | `clinical.results` (valid time, recorded time, supersede chain, append-only) exists (0050). Medications and notes are NOT built: they need coded vocabularies, which are clinician content |
| Correction fan-out and decision provenance (§9.5) | NOT BUILT | A correction of a result does not yet block or flag the decisions that read the old value, and decisions do not record the versioned resource ids they read (a screening records rule provenance only). Needs the decision records to carry provenance first |
| Notes: append-only, structure-aware, content-addressed embeddings (§9.6) | NOT BUILT | No notes table, no embedding cache, no Qdrant; erasure of note vectors (§16.6) therefore has nothing to do yet |
| Care episodes and recurrence rules (§9.3) | PARTIAL | Producers exist (0048: nurse/doctor create episodes and record done; the worker raises the overdue alert). The rule DEFINITIONS are a required setting no clinician has written; the demo pack has one labelled placeholder |
| Retention enforcement job and purge role (§9.4) | DECISION | Erasure of a patient's identity, messages, voice transcript chunks, plan text and result measures is built (0032, 0056, 0057, audited, two people). The scheduled retention job is not: periods are required settings and counsel's, a purge needs its own grants and review (checklist 4). Result code, verified flag and times are kept after erasure by owner decision. Expired voice chunks have an audited purge (0047); prompt/response bodies (30 days) and checkpoints (90 days) have none |
| Context assembler with a token budget (§9.2) | BUILT, UNUSED | `careflow/context_assembler.py` is pure and tested; no model call uses it yet because no model route is approved |

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
| Upload malware scanning (§11.5) | FAKE | The ClamAV client and a rescan worker (`careflow/scanner_worker.py`) exist and are tested against a fake clamd; where the daemon runs is undecided (checklist 2), and no table records a verdict or signature version per document yet |
| Rate limits per account on messages (§11.5) | PARTIAL | Upload size and type allowlists exist; only the patient-view open and label-row limiters exist; a per-account message rate limit is not enforced |

## 12 and 13. Approval, observability
| Item | Status | Reason |
|---|---|---|
| Autonomy mode switch (§12.1, §12.5) | BUILT, DISABLED | Admin-only audited history; the switch to `autonomous` needs an admin AND a doctor, two different accounts (0054). OFF until `CAREFLOW_AUTONOMOUS_MODE_ENABLED`; in `clinical` a stored `autonomous` is ignored until `CAREFLOW_CLINICAL_AUTONOMY_SIGNOFF`. RESIDUAL: one human holding both an admin and a doctor account can satisfy it alone; the clinic must assign the roles to different people (pre-launch item 6, `phase10-pre-launch.md`). Only the Scheduling and Visit Readiness runs read the stored mode; with no row the default is `shadow` (a recorded departure from the spec's `assisted`) |
| Reviewer free-text reason note (§12.2) | DECISION | Owner decided not to store it |
| Learning loop: label export (§12.6) | PARTIAL | Pseudonymised label rows are built; no export or training job |
| After-the-fact 5% sampler (§12.6, §24) | FAKE | The sampler sweep, the worker insert and the nurse/doctor re-check exist (0048); no auto-approval path exists, so the candidate source returns none and nothing real is sampled |
| Langfuse traces, Grafana metrics (§13.1, §13.2) | FAKE | Owner decided they stay fakes; dashboard and alert rules are generated files only, no panel has a query |
| E-mail and chat alert senders (§13.4) | FAKE | Ports with fakes; external services not approved |
| Test vs live dashboard page (§13.6) | PARTIAL | `GET /v1/staff/console/dashboard` returns four measured values and lists every other catalog metric as not measured; there is no page |

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
| Account states `registered` to `active`, enforced per feature (§17.5) | NOT BUILT | The states and the `readiness` topic exist in the schema; no route or policy gates a feature by account state yet |
| Patient portal backend (§17.2, §18.4, §20.1) | PARTIAL | Built: register, versioned consent, activation, the claim, front-desk resolution of claims (0049, 0051). The consent texts are counsel's (a labelled demo placeholder only). NOT built: guardian and proxy accounts, e-mail as the verified contact, the account-state gating above |
| SMS via DLT (§18.2) | DECISION | Guard built; nothing registered, no provider; the owner starts registration |
| Email provider (§18.3) | FAKE | Port only; provider tiers are an Appendix B check |
| Staff MFA and passkeys (§18.1) | PARTIAL | TOTP enforced in the API; `iam.aal2_session()` (0047) is enforced by the database on every table and function added since 0047, reads included for results, plans and visit readiness. The older tables are NOT retrofitted: an ACCEPTED RESIDUAL (owner decision 2026-11-07); passkeys are excluded by the owner |
| Break-glass access path (§18.4) | NOT BUILT | No route lets a staff member open a record outside their normal scope with a stated reason, audited and reviewed afterwards |
| Staff account recovery through an admin and a second admin (§18.1) | NOT BUILT | No recovery flow exists; a lost second factor is handled outside the system |
| Short-lived signed tokens per job from agents to tool servers (§18.4) | PARTIAL | The tool servers run in process behind the worker identity; a per-job signed token between separate processes is not built |
| Patient OTP rate limits, CAPTCHA, device list (§18.1) | DECISION | These live in the hosted Auth project's settings, which no migration controls; the owner must set and evidence them |
| Staff invite flow with the Auth admin API | DECISION | The admin call stays unwired by the owner |
| Slot, provider and location creation (§17.1) | PARTIAL | Set-up routes exist for front desk and admin; who owns them is an owner decision (checklist 45) |

## 19 to 21. UI, consent, governance, testing
| Item | Status | Reason |
|---|---|---|
| UI (§19) | OUT OF SCOPE HERE | The frontend is a separate workstream, not touched by this build |
| Tamil strings reviewed (§19, checklist 43) | DECISION | 419 of 419 unreviewed, including the emergency banner |
| Patient rights and consent withdrawal (§19.3, §20.1, §23.4) | NOT BUILT | Notices and consent events exist (0049) and a voice session records its own consent time. Missing: one-click withdrawal of a purpose, download my data, correction and nominee requests, the grievance channel with tracking (no grievance table or route exists; 0001 only lists it), the guardian-consent trail for under-18s |
| Clinical content governance (§20.2) | BUILT as rules | Enforced by process and the reviewer agent; all clinical content itself is missing by design |
| Dependency vulnerability scan, visual regression (§21) | PARTIAL | pip-audit job report-only; no visual regression |
| Chaos tests, load test at N times peak (§21, §14.6) | PARTIAL | In-process chaos (a fault in each side-effect port; retries reuse the same idempotency keys) and load (N times a LABELLED PLACEHOLDER peak of concurrent runs, many rules and assemblies) run in CI with fakes; they show no shared state, not capacity. A real load test needs hosting and an owner-set target |

## 22 to 24. Visit Readiness, voice, care gaps
| Item | Status | Reason |
|---|---|---|
| Visit Readiness content and lead times (§22.2, §22.3) | DECISION | The questions, checklists and lead times are clinician content; none exists |
| Voice real engine, measured interrupt gap, shadow price (§11.6, §23.1, Appendix B) | DECISION | No real engine by owner decision; the gap and price are unmeasured |
| Care gap data producers: episodes, rules, samples; worker-raised overdue alert | PARTIAL | Built in 0048 (see above). Residual: any nurse or doctor can insert a rule row (the application takes definitions from the setting; the database cannot check them); `last_done_by` records who moved a date and every move is an audit row |
| Care-gap metrics on the dashboard (§24.3) | PARTIAL | Values computed in code; no dashboard query |

## Appendix B items still unverified
Laya latency; Jev request shape; the reranker model's hosted availability; DeepEval custom judge; Guardrails NLI; the SMS provider hook; free e-mail tiers; Sarvam limits; Uploadcare scanning; hosted embedding output shape; passkeys; Qdrant tenancy; the voice interrupt gap and Gemini Live limits; mid-session system-instruction updates; the signed-upload overwrite and validity behaviour.
