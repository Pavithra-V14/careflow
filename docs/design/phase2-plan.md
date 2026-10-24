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
| **P2-2** | `DecisionPort` and the Laya, Jev and LLM adapters over injectable clients; routing by data class; script detection before Laya; contract test (design: `docs/design/p2-2-decision-tier.md`) | **In progress**, fakes only | The three-engine contract test (hard gate) | Laya CPU latency; Jev request shape via OpenRouter |
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

Answered by the owner on 2026-10-24:

1. **Required roles for a `clinical` launch**: recommended `decision,embed,llm_large`, documented commented-out in `.env.example`; **no code
   default**; the owner sets the real `CAREFLOW_REQUIRED_ROLES` value.
2. **Daily spend ceiling**: no code default; the number arrives by configuration. The day's spend is **persisted** (`ops.spend_ledger`,
   migration 0016, append-only, forced RLS).
3. **`phi_approved` approver**: enforced in the database (migration 0016): an active staff account holding an admin or doctor grant, different
   from the account that added the row.
4. **LLM provider**: NOT decided; no hosted provider is added. **Uploads**: Supabase Storage behind an `UploadPort`, an accepted deviation from
   Uploadcare (`docs/design/uploads-supabase-storage.md`), with the missing malware scanner recorded as a pre-launch blocker.
5. **In-process worker**: accepted until a separate worker process and `/internal` auth exist.

Standing instruction (owner, 2026-10-24): the owner's machine has little memory. **Do not install, download or load any model weights or heavy
runtime (Laya, Unstructured, the local embedding model, torch and similar) without asking first, and say each one's approximate disk and RAM.**
Everything is built against fakes. The frontend is postponed; backend only.
