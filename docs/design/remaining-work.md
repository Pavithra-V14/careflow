# Remaining work (2026-10-30)

Written from BUILD-SPEC.md Appendix D (phases 0 to 10), the Phase 2 and Phase 3 acceptance criteria, `phase2-status.md`'s checklist, `frontend-contract.md`, and the decisions recorded in the design notes. "Blocked" means exactly what it says: the item cannot be done without the named person or thing, and nothing in it may be worked around.

Sizes: **S** under a day of work, **M** one to three days, **L** more. Every route item also carries, by rule, a role-matrix case in the same change, an audit row if it records a decision, and a regenerated OpenAPI snapshot; those are not repeated per item. "Live" tests run only in CI (they need the shared database); offline tests run in the 1,200+ test suite on every change.

Legend for blockers: **D** a decision by the owner, **C** a clinician (item number on `clinician-sheet.md`), **P** a provider approval, **S** a spike or real samples, **H** hosting.

## A. Engineering items that need NO decision, clinician or provider (ordered by value to the clinic)

| # | Item | Size | Tests | Depends on |
|---|---|---|---|---|
| A1 | **DONE (batch 4)** Scheduling reference lists for the booking screens: providers, locations, visit types, priority levels (ids, names/labels, rank). Reads only, under the existing `select` policies and grants. Unblocks every scheduling form (frontend-contract item 4). | S | Role-matrix cases per route (readers: front desk, nurse, doctor; others 403; other clinic sees none), paging, offline shape tests | none |
| A2 | **DONE (batch 4)** Merge and possible-duplicate lists: list and open pending `mpi.match_candidates` and `mpi.merges` (ids, kinds that agreed, status, who proposed), so the merge screens have something to show (frontend-contract item 3). Reads only. | S to M | Matrix cases (candidates: front desk; merges: front desk, nurse, doctor per the existing select policies); live test that a subordinate and a master are both listed correctly | none |
| A3 | **DONE (batch 4)** Release a hold on purpose, and list holds: `release_hold` is in the Phase 3 design but there is no route; a hold otherwise lives until its time runs out. Uses the existing front-desk `update` on `sched.holds` and the slot update the book/cancel paths already use. | S to M | Matrix cases, audit row, live test that the slot reopens and a replay is a no-op, race with a concurrent book | none |
| A4 | **DONE (branch a4-priority-e2e)** Phase 3 end-to-end priority test (acceptance criterion): cancel a booking, take the ranked list, draft, send; prove a higher-priority waitlisted patient is offered the freed slot ahead of a longer-waiting lower-priority one, then book on acceptance. Never-committed harness (messages). | M | The test itself (live, in `tests/test_api_offers_live.py` style) | A3 helpful, not required |
| A5 | **Phase 2 live end-to-end through the routes**: upload, register, run, referral queue, review decision, with fake engines at the edges and the real database. Closes the "LIVE end-to-end run" item in `phase2-status.md`. | M to L | Live, never-committed or fully cleaned harness; the existing graph structure gate stays a hard gate | uploads routes merged (batch 4) |
| A6 | **Audit-log reader for the auditor**: `GET /v1/staff/audit` with filters (action, resource, actor, time), ids and closed words only. The `select` policy already allows the auditor. | S to M | Matrix cases (auditor only; the others 403/404), paging, a test that no free text can appear | none |
| A7 | **Declare the fixed error responses in the OpenAPI document** (401, 403, 404, 409, 422, 503 with the exact bodies) so the frontend can generate types. Mechanical. | S | Snapshot test; a test that every route declares the statuses its docstring lists | none |
| A8 | **Reply interpretation, fakes only** (Phase 3): the staff action "interpret this reply" through the `decision` port with a FAKE adapter and the in-memory ledger; the deterministic red-flag rules run on the reply FIRST; a spent ceiling shows "no suggestion: needs a person". Nothing real is called. | L | Order test (rules before any model), consumer-side ceiling test, a red flag on the same reply still raised, structural gate | none for the fake; the real call stays blocked (B5, B6) |
| A9 | **Message thread and message-header lists** (no bodies) for the appointments topic and the safety topic that front desk may see, so the frontend can show a conversation list. | M | Matrix cases per the existing message policies, a test that no body or decrypted text can appear | none |
| A10 | **Runbooks** that need no external contact: dead-letter growth, wrong merge (unmerge and review procedure), model outage (what degrades and how it shows), key rotation drill checklist update. The breach runbook needs contacts and counsel (B-list). | S each | Review by the owner; a drill checklist where one can be run in CI | none |
| A11 | **Gate scorecard mechanism** (Phase 5 skeleton): a runner that evaluates every 14.3 gate and prints MEASURED / NOT MEASURED (with the reason) / FAILED, failing closed, with no invented numbers. Real values arrive with the gold sets. | M | Offline tests of the runner; CI step that prints the scorecard | none |
| A12 | **Reduce per-request round trips** (POOL-DIAG: about 0.5 s per statement to the Tokyo pooler; a request holds its connection for about two). Combine the claims `set_config` with the first query where safe. Matters more once the region moves. | M | The two pool tests (isolation and no leak must still pass), a round-trip count test | none |
| A13 | **CI speed and shard balance**: shard 1 (the role matrix) grows with every route. Split the matrix by route family across shards, keep each under 50 minutes. | S to M | `tests/test_ci_shards.py`, measured shard times | none |
| A14 | **Metrics catalog to dashboard generator** (`metrics-catalog.yaml` to a Live/Test dashboard definition and alert rules as files). No service is called. | M | Offline tests that every metric is defined exactly once and every alert refers to one | none (the consumer, Grafana, is blocked below) |
| A15 | **Confirm and test that a patient's reply is screened by `msg.record_screening` and raises the nurse task** (Phase 3 design item 4 asks for this confirmation). | S | A live test, no new code expected | none |

## B. Blocked (and exactly on what)

| # | Item | Blocked on |
|---|---|---|
| B1 | **Real storage adapter** (Supabase Storage `UploadPort`) and its bucket policy; the upload routes answer 503 until then | **D** the bucket and `storage.objects` policy (a new policy: stop and ask); **S** the Appendix B signed-upload spike |
| B2 | Signed READ url for the reviewer's document | B1 |
| B3 | Real malware scanner adapter (ClamAV) | **D** where the worker machine lives (direction approved: self-operated, no hosted API, no Docker) |
| B4 | Retention and erasure job; the purge role and function | **D** its own design with exact grants and a reviewer pass, and the owner's sign-off of the grants; **C/D** the retention periods (required settings, no defaults) |
| B5 | Durable spend ledger for a real reply-interpretation call | **D** build the definer-function variant when a real call is wanted (a new grant, needs sign-off then) |
| B6 | Real vision, decision and translation engines; the three-engine contract gate (14.3) | **P** provider approvals (the owner is answering separately for synthetic demo only) |
| B7 | Real-PDF spike | **S** the owner makes dummy-document samples (tooling ready: `real-pdf-spike.md`) |
| B8 | Red-flag rules and the 100% recall gate; protocol cards, the retrieval gold set and thresholds; the priority levels | **C** clinician sheet (red-flag rules, cards, gold set, thresholds, priority levels) |
| B9 | Revoke review task (role, urgency), the revoke reason list | **C** items 23 and 24 (revoke is recorded, no task raised, until answered) |
| B10 | Closing a red-flag review task by a route | **C** item 25 |
| B11 | Offer wording, accept-confidence threshold, offer expiry and backfill counts (real values) | **C** items 17 to 20 (demo placeholders exist for `demo` only) |
| B12 | Offer expiry job and delivery of an offer to the patient (a channel) | **D** design (no worker identity in Phase 3) and **P** a messaging channel (SMS provider, DLT registration) |
| B13 | Showing a patient's name, date of birth or phone to staff; a patient list; fuzzy search; logging search reads | **D** what a staff session may see decrypted, with read logging |
| B14 | Column-level re-grant on `mpi.identity_confirmations` (client-settable `created_at`/`id`, reviewer residual F3) | **D** it is a grant change |
| B15 | Staff invitation and role management routes; clinic settings and the autonomy mode | **D** (they need insert grants on `iam` and a call to the Auth admin API, an external service) |
| B16 | Minimal patient portal backend (consent, activation, guardian, patient-facing routes) | **D** scheduling the package, **P** SMS provider, **H** region |
| B17 | Hosted observability (Langfuse, Grafana, the in-app watchdog wired to real traces) and the cost canvas | **P/D** accounts and approvals; a first real week of traffic |
| B18 | Phase 6 Care Journey, Phase 7 Visit Readiness, Phase 8 voice, Phase 9 care-gap panel | **C** (plans, readiness checklists, safety scripts), **P** (voice engine), and the phase order |
| B19 | Production hosting in an India region, the production secret manager, the restore and rollback drills | **H/D** |
| B20 | `STRONG_IDENTIFIERS` sign-off; step-up claim shape spike | **C** Priya (clinician-decisions 13); **D** the owner's spike on a real token |
| B21 | Rotate every key that was in `.env.cli` | **D** the owner (pre-launch blocker 12) |
| B22 | DLT/SMS registration; breach runbook contacts and counsel | **D/P** |

## Order of work from here

Section A in the order listed. A1 to A3 are small and unblock the frontend's scheduling and identity screens; A4 and A5 are the Phase 2 and 3 acceptance evidence; A6 and A7 are frontend contract items; A8 is the largest and only starts when the fake-only boundary is clear. Anything in section B stays untouched until the named thing arrives; nothing in section A needs a new grant, role or external service (if one turns out to, it moves to section B and the work stops).
