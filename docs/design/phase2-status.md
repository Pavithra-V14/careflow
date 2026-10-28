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
- P2-4 extraction: BUILT on branch p2-4-extraction (pypdf native tier, layout slot, vision through a port with fakes only, manual fallback, worker-only); Unstructured light mode is impossible without torch and was NOT installed. See p2-4-extraction.md.

## Not done and gating real use
Real-engine contract tests; retrieval gold-set gate; the route/worker to run the graph; the review console; the malware scanner; audit.log rows for the human decisions.

## PRE-LAUNCH BLOCKERS (recorded decisions; nothing here blocks the merge, all of it blocks real patient data)
1. **Worker token-attach and pending-duplicate forgery (0019 residual risk 4).** INSERT on `mpi.identity_tokens` and `mpi.match_candidates` lets a queue worker attach an identity token to ANY existing patient of its clinic, or forge a pending possible-duplicate between two patients; a wrongly attached token would make a later referral link to the wrong person. The owner decided (2026-10-24) NOT to build the SECURITY DEFINER ingestion function now, and to accept the risk while the worker is a trusted, synthetic-data-only identity. It must be closed (a function that touches only the record it just created) or explicitly re-accepted before any real patient data.
   **STATUS (2026-10-28): fix written, not merged.** Migration 0022 adds `mpi.register_patient_from_referral` (SECURITY DEFINER) as the worker's only write path and drops its three INSERT policies; branch `blocker1-worker-registration-fn`; design `docs/design/blocker1-worker-token-forgery.md`. The blocker closes when 0022 is merged and the live tests pass in CI.
2. The malware scanner (uploads are disabled outside demo; clinical readiness needs one).
3. Priya's `STRONG_IDENTIFIERS` sign-off; `identity_refused` routing confirmed by the clinic.
4. A retention/erasure procedure for checkpoints and review tasks (history is append-only today).
5. audit.log rows for human clinical decisions.
6. (DONE 2026-10-27) A dead-lettered `front_door_run` job now raises a nurse's `referral_dead_letter` review task (careflow/dead_letters.py, run by the tick's sweep); its priority is a required placeholder (clinician-decisions item 15).
7. (CONFIRMED 2026-10-27, on the owner's two conditions, both met and enforced by tests) The dedicated-harness role-matrix exception for the two referral routes: every one of the 14 identities has its own case on each route in the dedicated harness (`tests/test_api_referrals_live.py`; the split guard requires every required role on both routes and a driving test parametrized over exactly them), and `test_every_route_is_in_the_matrix` fails when any route in the served OpenAPI document is covered by neither the main matrix nor a dedicated harness (and when a case is stale).
8. No process has yet run the graph through the real pooler engine and a real commit (the production session factories are only unit-tested).
9. **Real-world PDF spike (P2-4).** pypdf and the image reader were tried only on synthetic PDFs. Run real scanner output (JBIG2, CMYK, odd fonts, inline images, encrypted files) through the pipeline. Failures already route to a nurse as `image_unreadable` / `document_unreadable`, which is the safe behaviour; the spike measures how often that happens. Images in annotation appearance streams, tiling patterns and Type3 glyph procedures are accepted DOCUMENTED LIMITS (owner decision 2026-10-27). **No real referral is processed until the real-PDF spike has cleared this.** Until then the `data_policy` must stay `synthetic_only` for the referral path (no registry row on that path may be `phi_approved`).
10. **A worker-created record can carry a token for a person who is not registered yet (planted token).** The worker computes tokens itself; migration 0022 stops attach-to-an-existing-patient but not this. When that person's real referral arrives the matcher would find exactly one record agreeing on both strong identifiers and link it confidently and silently. It BLOCKS REAL IDENTITY MATCHING and sits next to the `STRONG_IDENTIFIERS` sign-off (clinician-decisions item 13). Option A SIGNED OFF 2026-10-28 and written on branch `optionA-unverified-worker-records` (not merged): worker-created records are unverified and never auto-linked; a front-desk confirm-identity action verifies them (migration 0023); a queue_worker account holds no other role (0024). The blocker closes when 0022-0024 are merged and their live tests pass in CI; option C (one record per registered document) is deferred.
