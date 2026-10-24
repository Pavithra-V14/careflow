# P2-5 - tiered grounding (BUILD-SPEC.md 11.2)

Code: `careflow/grounding.py`. Tests: `tests/test_grounding.py` (offline, no model).

## What it does
| Tier | Fact | Check | Success needs |
|---|---|---|---|
| 1 | date, phone, blood pressure, quantity | both sides to a canonical form, exact compare | the quote holds exactly one readable candidate and it equals the value |
| 2 | interpretive finding | `DecisionService` (so gateway, data policy, budget, record all apply) | the engine answered `supported` AND the answer is calibrated |
| 3 | free-text summary | none | never verified; labelled `model_generated`; never usable by a rule |

A failed check is `unverified` with a fixed-word reason. There is no false/rejected status (a test pins the enum). An unverified fact has
`usable_by_rules == False`, and `rule_value()` returns `UNKNOWN`, whose truthiness raises: it cannot be read as true or false (rule 3).
`needs_review(facts, fields_read_by_matched_rules)` returns only the unverified (or missing) facts a matched card actually reads.

## Decisions made here (safety-neutral or in the safe direction; say if you disagree)
- **Guardrails AI is not used.** Its model is Pass/Fail plus `on_fail` actions (`fix`, `reask`, `filter`, `exception`): a vocabulary with a
  "fail" and an auto-"fix" that can rewrite a clinical value, against our "unverified, never false". Its validators are synchronous; ours call an
  async gateway. The checks are ~300 lines of plain code with no dependency. `Decider`/`verify_*` have the same shape a Guardrails validator
  would wrap, so a thin wrapper can be added later without touching the checks. Spec 11.2 says "compose as Guardrails custom validators";
  this is a deviation from the letter, in the direction of fewer dependencies (nothing to approve, no data-flow entry).
- **Ambiguity is unverified, not guessed.** Numeric dates with both parts <= 12 and different (05/03/1980), ranges (5-10 mg, 5 to 10), fractions
  (1/2 tab), decimal commas (5,5), two different candidates in the quote, or a unit spelt differently. Units are compared as spelt
  (case-insensitive): no synonyms, no conversion. Unit synonyms and conversions are clinical content and need clinician sign-off before anyone adds them.
- **Tier 2 trusts only a calibrated engine.** No confidence threshold exists in the code. Until real calibration (the Laya calibration store)
  exists, tier 2 verifies nothing: the safe default. The hard gate "no unverified fact reaches a rule" holds trivially.
- **Gateway failures are unverified; `BudgetExceeded` is not swallowed** (it stops the run).
- Tier 2 residual risk: the passage is quoted data to the adapter's fixed prompt (11.1), but a hostile passage could still sway an engine
  into "supported". The injection classifier (11.1) and human review where a rule reads the fact are the other layers.
- DOB range checks (not in the future, not before 1900) stay in `mpi.parse_date_of_birth`; tier 1 only proves the value is in the quote.

## Not here
Which fields a card reads (clinician-authored, P2-6), the extractor (P2-4), wiring into the graph (P2-8), image-region display (11.2 last paragraph).
