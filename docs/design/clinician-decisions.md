# Decisions that belong to clinicians (or the clinic), kept as required placeholders

CLAUDE.md rule 1: no threshold, priority, role rule, reason code, phrase or unit equivalence is written from our own judgment. Each item below is a REQUIRED argument or an absent
column, never a default, and is marked `# CLINICAL-PLACEHOLDER: needs clinician sign-off` where it appears in code. Nothing below has a value in the repository.

| # | Decision | Where it is a placeholder | Who decides |
|---|---|---|---|
| 1 | Who reviews a triage proposal (nurse, doctor, or either) | `SqlTriageProposer(assigned_role=...)` is required, restricted to nurse/doctor, no default | clinical lead |
| 2 | The `priority` ('immediate' or 'standard') of a triage-review task | `SqlTriageProposer(task_priority=...)` required, no default | clinical lead |
| 3 | The `priority` of a referral-hold task (unassessable, extraction unavailable, missing date of birth, key conflict, empty letter) | `SqlHoldTaskWriter(priority=...)` required, no default | clinical lead |
| 4 | The reason-code picklist for a review decision (BUILD-SPEC.md 12.2) | no column exists; `edit` carries no content yet | clinical lead |
| 5 | Auto-approval of one confident low-risk card (assisted mode): the low-risk label, the allowed priorities, the minimum calibrated confidence | `AutoApprovalPolicy(...)` required fields; `route_proposal` sends everything to a human without it; 0018 can hold only human decisions | clinical lead; then a later migration |
| 6 | Review deadlines per task class and the escalation timing | `next_escalation(deadline_seconds=...)` required | clinic / clinical lead |
| 7 | Retrieval abstention thresholds (dense similarity, BM25 ratio) | `AbstentionPolicy(...)` required, none by default; no policy means abstain | clinical lead, from the gold set |
| 8 | Protocol-card content, the card attributes (risk class, priority), the scope keys, the gold set | no cards exist; cards are clinician-authored | clinicians |
| 9 | Red-flag rules (phrases, patterns, vitals) | `kb.red_flag_rules` is data; approved only under the two-person rule | clinicians |
| 10 | Unit synonyms and conversions for grounding (mg vs milligram, mcg to mg) | none: units must match as spelt; a mismatch is "unverified" | clinical lead |
| 11 | Which structured facts a card reads (so that only those raise a review when unverified) | `needs_review(fields_read_by_matched_rules)` takes it as input | card authors |
| 12 | The patient-facing emergency template and any reply text | `patient_reply = pending_clinician_template`; nothing is sent | clinical lead |
| 13 | `STRONG_IDENTIFIERS` (what counts as a confident identity match) | `careflow/mpi.py`, pending Priya's sign-off; no real patient data until recorded | Priya (health-information lead) |
| 14 | Whether `identity_refused` (data-entry refusals from the identity module) belongs to front desk or a nurse | currently front desk; an idempotency-key conflict goes to a nurse | clinical lead |
