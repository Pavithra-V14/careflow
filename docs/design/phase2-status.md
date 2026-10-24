# Phase 2 status (BUILD-SPEC.md Appendix D). Phase 2 ACCEPTANCE IS NOT MET.

Updated 2026-10-24. Branch stack: next-backend (PR #2, CI green) <- p2-3-docs <- p2-8-graph. Migrations 0017 to 0021 are on the stack, NOT merged, NOT applied to dev
(`python -m careflow.migrate --check` against dev on 2026-10-24: 0017 and 0018 pending, ledger intact; 0019 to 0021 did not exist yet). Apply to dev only after merge and review.

## Done (code, offline tests, reviewed)
- P2-1 runner, budgets, tick; P2-2 decision tier with fakes and the three-adapter contract harness (against fakes only).
- P2-3 documents: signed upload service, server-side re-verification, registration, `read_verified` re-hash, uploads disabled outside demo, clinical readiness needs a scanner (0017).
- P2-5 tiered grounding (plain code; Guardrails AI not needed).
- P2-6 pure-code retrieval: structured-field text, BM25, RRF, version-checked join, abstention with no default threshold, fake vector store.
- P2-7 referral to identity: rules before any model on every path; verified facts only into mpi.
- P2-8 the Front Door graph: structural no-side-effect-across-a-pause hard CI gate (fails closed), topology and agent-inventory pins, crash-resume tests, tracing refused.
  Postgres checkpointer: our own asyncpg saver on 0021 (forced RLS, append-only history), conformance with LangGraph's InMemorySaver, restart-durability test.
- P2-9 triage: 0018 (proposal and human-only decision tables), routing policy with no clinical values, SQL writers.
- No run ends silently: `hold_for_human` raises a `referral_hold` review task (0020: front desk can see and resolve its own).
- The queue worker can register patients from a verified referral (0019: INSERT plus the reads matching needs; no UPDATE/DELETE; merges stay human and two-person).
- P2-10 offline end-to-end with stand-ins at the edges.

## Buildable now
- A LIVE end-to-end run against the real database (now possible: the worker can run the whole clean path under RLS). Needs the real vault, a rules fixture and the review console is NOT needed.
- The worker/process that starts and resumes runs, and the authenticated resume route with its role-matrix cases (rule 8).
- Retention/erasure procedure for checkpoints (history is append-only; deletion is refused today).
- A SECURITY DEFINER ingestion function so the worker cannot attach a token to an arbitrary patient (0019 residual risk 4), if wanted.

## Blocked on clinicians (see docs/design/clinician-decisions.md)
- Card content, card attributes, scope keys, the retrieval gold set and the thresholds (so the retrieval gate and correct-abstention cannot be measured).
- Red-flag rules; the review-task priorities; who reviews triage; reason codes; auto-approval policy; deadlines.

## Blocked on the owner
- LLM provider and accounts for the real-engine contract tests (Laya/Jev/LLM): the three-engine hard gate stays OPEN until real engines answer. No hosted account may be added without approval.
- Step-up claim shape (real tokens), Priya's STRONG_IDENTIFIERS sign-off.
- The real embedding, Qdrant and storage adapters (each needs its data-flow entry and an Appendix B spike: signed-upload overwrite, Qdrant access control).

## Parked for RAM
- P2-4 extraction pipeline (pypdf, Unstructured local). Its disk and RAM needs will be restated when it is reached; nothing is installed.

## Not done and gating real use
Real-engine contract tests; retrieval gold-set gate; P2-4; the route/worker to run the graph; the review console; the malware scanner; audit.log rows for the human decisions.
