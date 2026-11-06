# Phase 7: Visit Readiness agent (flagged), mechanism level, 2026-11-06

BUILD-SPEC.md section 22 (with 11.4, 12.1, 5.2). Code: `careflow/visit_readiness.py`. Behind `FEATURE_VISIT_READINESS` (off by default): `build_visit_readiness_graph` refuses to build with the flag off, and `agents_to_build` lists `visit_readiness` only when it is on. One agent, four triggers (brief, intake, readiness inside the intake conversation, barrier).

## What is built

- **No question is written by code.** A question is a card's `intake_question` for a declared fact (`IntakeCard`), or a visit type's `readiness_checklist` item (`ReadinessChecklist`), sent as the exact clinician text. `card_problems` and `checklist_problems` check structure only, never wording.
- **The gap, by code** (`fact_gap`): a declared fact is asked only if it is missing or not usable by a rule (unverified is unknown; two conflicting entries are usable only if both are; a free-text fact is never usable). Declaration order, so the conversation is reproducible. A fact with no clinician question is **unaskable**: never asked, never guessed, listed in the incomplete-intake flag for front desk.
- **Rules before any model, on every reply** (rule 2): `screen_reply` sits between the pause and every model node (`classify_intent`, `classify_readiness`, `resolve_fact`); the structural test pins that no other edge reaches them. A danger phrase volunteered in an answer to an unrelated question ends the run in the screener's fixed safety response and nurse task, and the flagged reply is never written as a fact (`tests/test_visit_readiness.py`, the end-to-end test the phase names).
- **A clinical question is never answered**: it goes to front desk. An unclear reply is asked once more, then goes to front desk. An intent outside the closed set is unclear.
- **An answer becomes a fact only through grounding** (11.2): `resolve_fact` records it only if the grounded fact is usable by a rule; otherwise it joins the incomplete flag.
- **Readiness is locked toward a nurse in every mode**: any item `not_done`, `unclear`, unanswered or outside the closed set raises a nurse task. `escalate_nurse` reads no policy (a test pins it) and the test runs all three modes. An empty checklist never counts as ready.
- **Barriers**: a closed set; only `scheduling_conflict` hands off to the Scheduling flow (automatic in assisted and autonomous, confirmed by a person in shadow, from the matrix row `barrier_resolvable_by_rebooking`); every other barrier, or one outside the set, goes to front desk. The barrier type is recorded.
- **Brief**: structured lines for the clinician only, each labelled verified, unverified or model-generated; nothing upgraded. A failure blocks nothing (decorative, 22.1).
- **No run ends silently**; the state holds ids, counters and closed words only (a test greps it).

## Not built, needs a person or a decision

1. **Clinician content**: every intake question, every readiness checklist, the barrier wording to patients, and the lead times (brief 48 h is the spec default; intake and readiness lead times, the "no response by" point for barriers) are clinician or owner settings. None is in code.
2. **Storage and routes**: the `readiness` message topic (22.5) and the tables for visit facts, readiness answers, barrier records and briefs need migrations with forced RLS, a reviewer pass and role-matrix cases for every route. The ports have fakes only.
3. **The real model roles** (intent, readiness, barrier classifiers, fact extraction, brief narrative) are unapproved externals in `clinical`; nothing real is called.
4. **The Scheduling hand-off** is a port: wiring it to start a Scheduling run for a visit's slot needs the slot-from-visit rule from the owner.
5. **The incomplete-intake task** and the nurse escalation use `role` words only; their priority is a required setting still to be chosen with the clinicians.
