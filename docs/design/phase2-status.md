# Phase 2 status (BUILD-SPEC.md Appendix D). Phase 2 ACCEPTANCE IS NOT MET.

Updated 2026-10-24. Branch stack: next-backend (PR #2, CI green) <- p2-3-docs <- p2-8-graph. Migrations 0017 to 0021 are on the stack, NOT merged, NOT applied to dev
(`python -m careflow.migrate --check` against dev on 2026-10-24: 0017 and 0018 pending, ledger intact; 0019 to 0021 did not exist yet). Apply to dev only after merge and review.

## Done (code, offline tests, reviewed)
- The live run, on stand-ins for the external engines: the worker job handler for the Front Door graph (`careflow/front_door_runner.py`), `POST /v1/staff/referrals/{id}/run` (enqueue) and `/review` (resume as the signed-in reviewer), each with its role-matrix case in the dedicated live harness `tests/test_api_referrals_live.py` (NEVER RUN yet: it first runs in CI on PR #3).
- P2-1 runner, budgets, tick; P2-2 decision tier with fakes and the three-adapter contract harness (against fakes only).
- P2-3 documents: signed upload service, server-side re-verification, registration, `read_verified` re-hash, uploads disabled outside demo, clinical readiness needs a scanner (0017).
- P2-5 tiered grounding (plain code; Guardrails AI not needed).
- P2-6 pure-code retrieval: structured-field text, BM25, RRF, version-checked join, abstention with no default threshold, fake vector store.
- P2-7 referral to identity: rules before any model on every path; verified facts only into mpi.
- P2-8 the Front Door graph: structural no-side-effect-across-a-pause hard CI gate (fails closed), topology and agent-inventory pins, crash-resume tests, tracing refused.
  Postgres checkpointer: our own asyncpg saver on 0021 (forced RLS, append-only history), conformance with LangGraph's InMemorySaver, restart-durability test over the in-memory store (and a single rolled-back transaction against the real tables): NOT yet proven across a real process restart and commit. The production session factories exist (`front_door_runner.worker_session`, `request_session`) and are unit-tested with fakes, but nothing has run them against the real pooler.
- P2-9 triage: 0018 (proposal and human-only decision tables), routing policy with no clinical values, SQL writers.
- No run ends silently: `hold_for_human` raises a `referral_hold` review task (0020: front desk can see and resolve its own).
- The queue worker can register patients from a verified referral (0019: INSERT plus the reads matching needs; no UPDATE/DELETE; merges stay human and two-person).
- P2-10 offline end-to-end with stand-ins at the edges.

## Buildable now
- A LIVE end-to-end run against the real database. The policies now allow the worker to run the whole clean path (0017 to 0021), but no such run has happened yet and the new live tests have never been executed.
- Retention/erasure procedure for checkpoints (history is append-only; deletion is refused today).

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

## PRE-LAUNCH BLOCKERS (recorded decisions; nothing here blocks the merge, all of it blocks real patient data)
1. **Worker token-attach and pending-duplicate forgery (0019 residual risk 4).** INSERT on `mpi.identity_tokens` and `mpi.match_candidates` lets a queue worker attach an identity token to ANY existing patient of its clinic, or forge a pending possible-duplicate between two patients; a wrongly attached token would make a later referral link to the wrong person. The owner decided (2026-10-24) NOT to build the SECURITY DEFINER ingestion function now, and to accept the risk while the worker is a trusted, synthetic-data-only identity. It must be closed (a function that touches only the record it just created) or explicitly re-accepted before any real patient data.
2. The malware scanner (uploads are disabled outside demo; clinical readiness needs one).
3. Priya's `STRONG_IDENTIFIERS` sign-off; `identity_refused` routing confirmed by the clinic.
4. A retention/erasure procedure for checkpoints and review tasks (history is append-only today).
5. audit.log rows for human clinical decisions.
6. (DONE 2026-10-27) A dead-lettered `front_door_run` job now raises a nurse's `referral_dead_letter` review task (careflow/dead_letters.py, run by the tick's sweep); its priority is a required placeholder (clinician-decisions item 15).
7. The dedicated-harness role-matrix exception for the two referral routes applies the standing rule (append-only tables); the owner has not named it separately. Please confirm.
8. No process has yet run the graph through the real pooler engine and a real commit (the production session factories are only unit-tested).
