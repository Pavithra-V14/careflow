# Phase 6: Care Journey agent (flagged), mechanism level, 2026-11-06

BUILD-SPEC.md sections 1, 4, 7.1, 7.2, 12.1 and Appendix D Phase 6. Code: `careflow/care_journey.py`. Behind `FEATURE_CARE_JOURNEY` (off by default): `build_care_journey_graph` refuses to build with the flag off, and `agents_to_build` lists `care_journey` only when it is on.

## The graph

`screen_plan` (rules first) -> `parse_plan` (the one model call) -> `request_confirmation` -> **`await_confirmation` (pause)** -> `activate_plan` -> `next_wait` -> **`await_result` (pause)** -> `claim_result` -> `evaluate_result` -> `propose_referral` | `close_step` | `hold_for_human` -> `next_wait` ... -> `complete_plan`. A rejected plan goes to `reject_plan`.

- **Rules before any model** (rule 2): the plan text is screened by the deterministic red-flag rules before the plan parser sees it. A flag or an unassessable text ends the run (nurse task) with no model call.
- **A doctor confirms before anything executes** (7.1): the confirmation pause is unconditional. It does not consult the autonomy mode (care-plan tasks are `human` in the matrix).
- **Closed plan schema**: the model proposes `{"steps": [...]}`. Code accepts it whole or not at all (`parse_proposal`): step kinds `task` and `wait_result`, a result-code prefix, an optional condition `field op threshold`, an optional `on_met: propose_referral`. Unknown keys, wrong types, NaN or text thresholds reject the whole proposal, which goes to a doctor. Nothing is repaired.
- **Prefix wait-claim**: each result step registers a wait with a result-code prefix. `match_wait` picks, among that patient's open waits, the one with the longest matching prefix; a tie is ambiguous. `claim_result` lets one result take a wait (first wins, the same result again is a no-op, a second result is ignored and the run keeps waiting).
- **Unknown is not a value** (rule 3): `evaluate` returns `unknown` for an unverified result or an absent, null, text, NaN or infinite value. `unknown` goes to a doctor. Only a verified value that fails the condition closes the step as not met, and the close cites the result id as evidence. This is the spec's named test: *a report lacking the needed value routes to a doctor rather than the loop silently closing as not met* (`tests/test_care_journey.py`).
- **A met condition proposes, it does not act**: `propose_referral` raises a doctor task. Nothing leaves the clinic (records release is `human` in every mode).
- **No run ends silently**: every non-clean route reaches `hold_for_human`.
- **State**: ids, counters and closed words only. The plan text, step labels and values live in the store.

Structural gate: `tests/test_care_journey_structure.py` (node declarations match the source, two pausing nodes touch no port, effect immediately before each pause, no model chooses a path, topology pinned).

## Not built (needs a decision or is a later package)

1. **Storage and routes.** The ports have fakes only. Real tables (`agents.care_plans`, plan steps, waits, with forced RLS, a definer claim function with a unique constraint for first-claim-wins, retention under the agent-working-state rule) need migrations, a reviewer pass and role-matrix cases for every route. Left for when the owner wants the agent switched on; designing them now would guess at a shape nothing exercises.
2. **The real plan parser** (`llm_large`) and the result source. No real model route exists in `clinical`; in `demo` a fake is the only adapter.
3. **The result router** (a tick that matches incoming results to waits with `match_wait` and resumes the run) and the "expected result window" that produces `overdue`. The window length is a clinician's decision.
4. **Clinician decisions**: which plan step kinds a clinic allows, who gets the confirmation and referral tasks (`task_role`, `task_priority` are required arguments, never defaulted), and the result-window lengths.
