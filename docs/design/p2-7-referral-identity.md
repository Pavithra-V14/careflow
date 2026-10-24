# P2-7 - referral letter to patient identity

Code: `careflow/referral.py`. Tests: `tests/test_referral.py` (offline; one shared event log across fake rules, extractor and ingestor).

Order (CLAUDE.md rule 2): document lookup (db) -> red-flag rules -> [only if `clear`] extractor (model) -> tier 1 grounding of name, DOB, phone ->
`PatientIngestionPipeline.ingest` (existing mpi module, unchanged). `red_flag` escalates and stops; `unassessable` stops; a database error stops
with the error. The extractor takes a `Clearance`, which only `screen_referral` can mint.

## Decisions (safety-neutral, noted)
- Name and DOB are required and must be verified against the letter; an unverified phone is dropped (weaker matching, never a wrong token).
- Name grounding is exact words in order (case/punctuation ignored): no initials, nicknames, transliteration or edit distance.
- Idempotency key of the identity write: `referral:<document id>`.
- Failure to extract degrades visibly (`extraction_unavailable`); the front desk keys the letter by hand.

## QUESTIONS FOR THE OWNER (not decided here)
1. **Which role runs this?** `escalate_red_flag` is allowed for nurse/doctor/queue_worker only; `mpi.patients` insert is front desk only (0002). One
   actor cannot do both under the present policies. Options: (a) split the stages (the worker screens/extracts, the front desk resolves identity
   from verified facts), (b) a SECURITY DEFINER path. Needs a decision and a migration; not built.
2. **Unassessable screening of a document has no nurse task.** 0013 raises `screening_unassessable` only for stored messages. For a document the result
   `held_unassessable` is returned but nothing is written, so a hold is visible only to the caller. Needs a review-task path and a priority
   (priority is a clinical decision).
3. The letter text comes from P2-4 (Unstructured), not built.
