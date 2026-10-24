# Phase 2 plan: the Front Door agent (BUILD-SPEC.md Appendix D)

Recorded 2026-10-24 on branch `next-backend`. Order set by the owner: Phase 1 close-out, then **P2-0** (this page: decisions and approvals) and
**P2-1** (runner and gateway, no new services), then **P2-2** with fake adapters first. Frontend F0 to F2 may start after P2-1 only if the owner
says so. **No hosted service, account, key or hosted call is added at any point before the owner approves that service for the package that
needs it (CLAUDE.md rule 13).** Everything below is built against fake adapters.

## Approvals on record (owner, 2026-10-24)

| Item | Status | Condition |
|---|---|---|
| `pypdf`, Unstructured (local), `qdrant-client`, `langgraph`, `guardrails-ai` (custom validators only), `fhir.resources`, local embedding and Laya model runtimes | **Approved**, libraries only | A Context7 check and a data-flow-register entry for each before it is added. For `langgraph`, check the Postgres checkpointer's driver against asyncpg first and report |
| LLM provider, Gemini vision, OpenRouter/Jev, Qdrant Cloud, Hugging Face inference, Uploadcare, Langfuse, Grafana, Sarvam, Cloudflare | **NOT approved** | Ask again when the package that needs the service starts. No account, no key, no call |
| Langfuse, Grafana, Sarvam, Cloudflare AI Gateway, the hosted reranker | **Deferred to later phases** | The reranker is off; fusion order is used |

## Work packages

| Package | Content | Status | Appendix D acceptance it serves | Appendix B items |
|---|---|---|---|---|
| **P2-0** | Decisions, approvals, this plan | **Done** (this page) | n/a | none |
| **P2-1** | The 7.3 ceilings and the safety class (`careflow/budget.py`); the worker tick, the poke and an off-by-default in-process worker (`careflow/tick.py`); the `ai.model_registry` migration 0015; the six ports with fakes (`careflow/ports.py`, `careflow/adapters/fake.py`); the gateway with routing, fallback, retries, breakers and spend ceilings (`careflow/gateway.py`); the readiness rule and `GET /readyz` (`careflow/readiness.py`); the prompt-template scan (`careflow/prompt_scan.py`, `prompts/`) | **Built** (offline tests pass; the 0015 live tests have not run in CI yet) | The 7.3 ceilings enforced and tested; the 7.4 tick and poke; section 2 "refuses to report ready"; section 6.2 prompt scan | none |
| **P2-2** | `DecisionPort` and the Laya, Jev and LLM adapters; routing by data class; script detection before Laya; contract test | **Next**, fakes first | The three-engine contract test (hard gate) | Laya CPU latency; Jev request shape via OpenRouter |
| **P2-3** | `docs` schema, signed upload, server-side re-verification, registration | Not started | Section 17.3 steps | Uploadcare malware scanning (pre-launch blocker) |
| **P2-4** | Extraction worker: pypdf, Unstructured (local), vision | Not started | Section 10.4 tiers with page provenance | none new |
| **P2-5** | Tiered grounding as Guardrails custom validators | Not started | Hard gate: no unverified fact reaches a rule | Guardrails' bundled NLI validator (do not use as is) |
| **P2-6** | Card schema and loading, Qdrant card index, BM25 and RRF, embeddings, abstention (no hosted reranker) | Not started; **content blocked on clinicians** | Retrieval gate: top-1, recall@3 >= 0.80, correct-abstention >= 0.90, false-routing | Qdrant access control (cards only); embedding availability |
| **P2-7** | Referral to identity (reuse `mpi`), red-flag screen before any model call | Not started | Rule 2: rules run first on every path | none |
| **P2-8** | Front Door LangGraph graph with a Postgres checkpointer | Not started | The structural no-side-effect-across-a-pause test (hard CI gate); topology matches the inventory; crash-resume repeats no side effect | checkpointer driver vs asyncpg |
| **P2-9** | Triage proposal and review tasks | Not started; **priorities blocked on clinicians** | Nothing clinical approved by timeout | none |
| **P2-10** | End-to-end referral to triage with stand-ins at one seam | Not started | The Phase 2 gates pass together | none |

## What P2-1 decided without asking (non-safety details, also in the commit messages)

- Counting is increment-then-check: the 12th model call is allowed, the 13th raises. The warning fires once at the 8th.
- A call is `ORDINARY` or `SAFETY`; a SAFETY call is counted and never refused, does not consume the ordinary allowance, and skips the spend
  ceilings, but it **never bypasses the data policy** (a PHI request still only reaches a `phi_approved` row).
- A handler still running at the tick's 40-second deadline is cancelled and its job is left for the queue's visibility timeout; a ceiling breach
  or a missing handler dead-letters at once with a message naming only the ceiling or task type.
- Gateway retries: 3 attempts, doubling backoff with jitter, each attempt counts as a model call; the breaker opens at 10 consecutive failures
  and allows one probe after 60 seconds. A free-tier row is never skipped for spend.
- The in-process worker is OFF unless `CAREFLOW_RUN_WORKER` is set; it needs `CAREFLOW_WORKER_ACCOUNT_ID`.
- The model registry has no seed rows, new rows are disabled by default, identity and data policy are immutable by trigger, and the application
  role can only read it (as admin or queue_worker). Changes go through the migrator.
- Model ids must be pinned: no trailing latest/default/stable/current, no surrounding space or control character, in the database and in Python.

## Questions for the owner (batched; nothing below was assumed)

1. **Which roles block a `clinical` launch?** `CAREFLOW_REQUIRED_ROLES` has no default, so a clinical deployment reports not ready until it is
   set. BUILD-SPEC section 2 lists capabilities that degrade visibly without an approved route; which of them count as launch-blocking is your call.
2. **Per-clinic daily spend ceiling** (`SpendLimits.clinic_daily_usd`): there is deliberately no default. What amount, and should the day's spend
   be persisted in the database so a restart cannot reset it? The ledger is in memory today, which is harmless while no paid route exists.
3. **Who may approve a `phi_approved` registry row?** The migration only requires a named approver and a date; it does not check that the approver
   is an active staff member (the migrator is the only writer). Do you want that enforced by a trigger or an approval tool before the first
   `phi_approved` row is written?
4. **LLM provider choice** (still open from Phase 1) for the `llm_*` roles, and whether Supabase Storage may replace Uploadcare for uploads
   (the spec names Uploadcare).
5. **Queue worker HTTP auth** (`/internal`, a short-lived signed token per job, section 18.4): the in-process worker avoids it; confirm that is
   acceptable until a separate worker process exists.
