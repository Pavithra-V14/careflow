# P2-10 - end to end: referral letter to human decision

Test: `tests/test_phase2_e2e.py` (offline, 6 tests). The REAL Front Door graph, referral/grounding code, card retriever (BM25, RRF, version-checked join, abstention)
and writer shapes run together; the stand-ins sit at the edges: the extractor and embedder (scripted), the vector store (in memory), the database connection
(recording fake) and the checkpointer (InMemorySaver). Cards are synthetic; every threshold is a test input.

Asserted together on one run: rules before any model call; identity only from verified facts; paused for a human with nothing recorded; no patient text in the checkpoint;
the structural checker passes on that very graph; then a human decision records exactly once. Plus: no policy => abstain and the query is never embedded; a stale index
entry is never proposed; a red flag ends everything before any model; a crash in the proposal step repeats nothing; an unverified name never reaches identity.

## Why there is no live (real database) variant yet
Under the present policies NO single role can run the clean path: `kb.red_flag_rules` and escalation need nurse/doctor/queue_worker, the `mpi.patients` insert is
front desk only (0002), and the proposal insert is queue worker/nurse/doctor (0018). A live end-to-end therefore either switches identity per node (which needs the
owner's decision on the role split, owner question 2 of P2-7) or runs as the migrator, which bypasses row-level security and would prove little. It also needs the
unassessable-document task path and an authenticated resume route (docs/design/p2-8-front-door.md, hard prerequisites). Build it when those are decided.

## Phase 2 status against Appendix D (honest)
Done in code: documents (P2-3), decision tier with fakes (P2-2), grounding (P2-5), referral to identity (P2-7), retrieval mechanics (P2-6 pure code), the Front Door graph
with the structural gate (P2-8), triage tables/policy/writers (P2-9), this offline end-to-end (P2-10).
NOT done, and gating real use: P2-4 extraction (Unstructured; parked), the Postgres checkpointer (driver decision), card schema and content (clinicians), calibrated
thresholds, the real extractor/embedder/Qdrant adapters and their data-flow entries, a route/worker to start and resume a run (with role-matrix cases), the role split,
the unassessable-document task path, real-engine contract tests (the three-engine hard gate stays open), and the retrieval gold-set gate.

## Compliance review (spec-compliance-checker)
- Fixed: the aggregate `test` job now needs `secret-scan` and `offline` too, so the structural gate and lint block a merge. It is a change to the workflow that PR #2 (next-backend) also carries: pick it into next-backend if you want PR #2 gated the same way.
- Renamed the end-to-end test: it shows safety properties on stand-ins, NOT Phase 2 acceptance (contract test, extraction, retrieval gold set, durable checkpointer and a real database run are missing).
- Before merge: confirm the dev and CI ledgers hold no 0018 row with the pre-daf31c8 checksum (nothing here applied it).
- When a durable saver lands, run with durability "sync" and test it.
