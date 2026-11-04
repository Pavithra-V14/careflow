# Pre-launch checklist (collected 2026-11-04)

ONE list of everything that must be true before real patient data. It collects, and does not replace, the sources named in the last column (those keep the reasoning). **Nothing here is closed unless it says CLOSED with its evidence.** Owner key: **O** the project owner, **C** a clinician (item number on `clinician-sheet.md`), **P** the clinic's privacy / data-protection owner, **V** a provider approval, **H** hosting, **E** engineering. Status: OPEN, IN PROGRESS, CLOSED.

Items 1 to 18 keep the numbers of `phase2-status.md` ("PRE-LAUNCH BLOCKERS"); 19 and 20 are recorded on branches not yet merged (noted) and keep their numbers; 21 onward are new here.

## A. Recorded blockers (numbers from phase2-status.md)

| # | Item | Status | Owner | What closes it | Source |
|---|---|---|---|---|---|
| 1 | Worker token-attach and forged possible-duplicates | CLOSED in code (0022; live tests green in CI, PR #6) | E | nothing further, unless the worker's forging of claims inside the process is re-opened (see threat-model.md) | phase2-status 1; blocker1-worker-token-forgery.md |
| 2 | Malware scanner for uploads (none exists; uploads are demo-only) | OPEN | O (where the worker machine lives) + E | an owner choice from `malware-scanner-options.md`, built and tested; clinical readiness then requires it | phase2-status 2, 14; uploads-supabase-storage.md |
| 3 | `STRONG_IDENTIFIERS` sign-off; `identity_refused` routing confirmed by the clinic | OPEN | C (Priya; clinician-decisions 13, 14) | signed sheet entries | phase2-status 3 |
| 4 | Retention and erasure for checkpoints, review tasks and agent working state | OPEN (design approved, no code) | O + P + C | retention periods (required settings, no defaults); the purge role/function with exact grants, reviewer pass and the owner's sign-off | retention-and-erasure.md; retention-and-erasure-purge.md |
| 5 | Audit rows for human clinical decisions | CLOSED (PR #8, #9) | E | n/a | phase2-status 5 |
| 6 | Dead-lettered job raises a nurse task | CLOSED (2026-10-27); its priority is clinician item 15 | C (15) | clinician sets the priority | phase2-status 6 |
| 7 | Dedicated-harness exception for the referral routes | CLOSED (confirmed 2026-10-27) | O | n/a | phase2-status 7 |
| 8 | Graph through the real pooler engine with real commits | CLOSED (PR #8) | E | n/a | phase2-status 8 |
| 9 | Real-world PDF spike (no real referral before it clears) | OPEN | O (dummy-document samples) | run `real-pdf-spike.md` on the samples; record the failure rate | phase2-status 9, 15; real-pdf-spike.md |
| 10 | Planted token in worker-created records | CLOSED in code (0023, 0024, 0025 merged) | E | n/a; sits next to item 3 for real identity matching | planted-token-design.md |
| 11 | Confirm no pre-0023 patient row is worker-created | OPEN | O | run the read-only query in phase2-status 11 on the target project before launch | phase2-status 11 |
| 12 | Rotate every key that was in `.env.cli` | OPEN | O | new database passwords (app, migrator), new Supabase secret key, new GitHub secrets; delete the old CI secret key | phase2-status 12 |
| 13 | Durable spend ledger for a real reply-interpretation call | OPEN | O (a new grant needs sign-off) + E | build the definer-function variant in `spend-ledger-options.md` when a real call is wanted | phase2-status 13 |
| 14 | (same as 2) | see 2 | | | |
| 15 | (same as 9) | see 9 | | | |
| 16 | Key-squatting hole in the 0013 `unmerge_review` insert clause | OPEN | E (a new migration; reviewer pass) | fix-forward migration binding the clause to a real event | phase2-status 16 |
| 17 | Privacy owner's written confirmation of the patient-view field list; a shared (not per-process) open rate limiter | OPEN | P + E | the written confirmation; a shared limiter | patient-view-fields.md |
| 18 | Document storage is synthetic-only | OPEN | O + P + H | region decision (India region in production), the privacy owner's written confirmation, scanner (item 2), the recorded spike, and the 0027 storage policies applied in production | phase2-status 18; uploads-supabase-storage.md |

## B. Recorded on branches not yet merged
| # | Item | Status | Owner | What closes it | Source |
|---|---|---|---|---|---|
| 19 | No API route exists to approve a red-flag rule with its two named approvers; the demo runs on a seeded DRAFT rule (profile `demo` only) | OPEN | C + O + E | design the approval route with the clinicians (two named approvers) before any clinical use | branch `seed-demo-and-fake-front-door`, phase2-status 19 |
| 20 | Hosted provider adapters (Gemini, Jev via OpenRouter, Sarvam) are approved for SYNTHETIC DEMO ONLY; Jev output is never evidence; the Jev downstream model is unpinned | OPEN | V + O | an agreement and `phi_approved` registry rows for a vision and a decision route before clinical use | branch `c1-providers` (numbered 19 there; renumbered here) |

## C. Clinician decisions that gate real behaviour (clinician-sheet.md)
| # | Item | Status | Owner | What closes it |
|---|---|---|---|---|
| 21 | Red-flag rules and the 100% recall gate; protocol cards, retrieval gold set and thresholds; the priority levels (sheet items A, B, C) | OPEN | C | signed answers, two named approvers for rules |
| 22 | Offer wording, accept-confidence threshold, expiry and backfill counts, scheduling-task urgency (items 16 to 22) | OPEN | C | signed answers |
| 23 | Revoke reasons list and who re-checks and how urgently (items 23, 24); no front-desk supervisor role and no admin alert exist today | OPEN | C; O for any new role or feature | signed answers, then wiring built once with the final list (and a migration if `other` is chosen) |
| 24 | Closing a red-flag review task (item 25); what a reviewer may edit in a triage proposal (item 26) | OPEN (safe defaults: no route closes one; edit refused) | C | signed answers |

## D. Phase 4 to 10 launch work (BUILD-SPEC Appendix D)
| # | Item | Status | Owner | What closes it |
|---|---|---|---|---|
| 25 | Gold sets and every 14.3 gate wired with real evidence; pre-deployment checklist run; scorecard at least 70/100 with evidence | OPEN (the scorecard runner exists and reads NOT MEASURED) | C + E | clinician-authored gold sets; evidence file produced in CI; drop `--report-only` |
| 26 | Hosted observability (Langfuse, Grafana, the watchdog on real traces), cost canvas | OPEN | O (accounts) + E | approvals; a first week of real traffic |
| 27 | Production hosting in an India region, production secret manager | OPEN | O + H | region and host decision |
| 28 | Rollback drill and restore drill, executed and timed | OPEN | E + H | after hosting exists |
| 29 | DLT / SMS registration (a message not matching a pre-registered template is silently blocked); the offer delivery channel and expiry job | OPEN | O + V | provider and registration; a design note (no worker identity in Phase 3) |
| 30 | Runbooks: dead-letter growth, wrong merge, model outage drafted (A10); overdue red flag and breach not written (breach needs contacts and counsel) | IN PROGRESS | O (review), E | owner review of the three drafts; breach runbook contacts |
| 31 | Staff invitation and role management, clinic settings and the autonomy mode (need insert grants on `iam` and the Auth admin API) | OPEN | O | a design note, then sign-off of grants and the external call |
| 32 | A revoked-role session ends within a known time (token lifetime vs the live grant check) | OPEN, to verify | E | measure and document; the live grant check already applies on the next request |
| 33 | Appendix B items depended on in a non-test path were each verified by a real call (step-up claim shape: recorded 2026-10-30 on a dev token; storage signed-upload spike done; hosted provider calls verified for the synthetic demo only) | IN PROGRESS | E | re-verify any remaining Appendix B item before it is depended on |
| 34 | Voice, Care Journey, Visit Readiness, care-gap panel (flagged phases) | OPEN, not started | C + V | clinician content; a voice engine approval; phase order |

## How to use this file
When an item changes, change it HERE and in its source in the same commit. A new pre-launch blocker is added here first. Do not close an item on an assertion: put the evidence (a merged PR, a recorded spike, a signed sheet entry) in the "What closes it" cell.
