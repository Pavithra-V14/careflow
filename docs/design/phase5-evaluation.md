# Phase 5: evaluation and gates (mechanism level), 2026-11-06

BUILD-SPEC.md section 14. The phase's own end condition ("the scorecard is actually 70 or more with evidence") **cannot be met by engineering**: it needs clinician-reviewed gold cases and real drills. What is built is every mechanism, each failing closed, so that real evidence can be dropped in and read honestly.

## What exists

| Piece | File | What it does |
|---|---|---|
| Gold-set format and loader | `careflow/goldset.py`, `eval/gold/*.yaml` | Case schema (kind, split, language, text, expected labels, author, review). Malformed cases and duplicate ids are reported problems. |
| Review rule | `GoldCase.reviewed` / `.gates` | A case gates only if a **named clinician** reviewed it, with a date, and the reviewer is not the author; and only in the `held_out` split. Unreviewed cases are loaded and counted, never scored toward a gate (14.1). |
| Frozen held-out split | `eval/gold/held-out.lock`, `scripts/gold_freeze.py` | Content hashes of every held-out case. An edited, removed or unlocked held-out case fails CI. `--freeze-new` adds new cases only and never rewrites an entry. |
| Tamil subset and size | `goldset.report` | Tamil counted apart; the 60/40/30/20 starting size is reported as "n of target", never gated. |
| Metrics | `careflow/evalmetrics.py` | Red-flag recall, urgent recall, card recall@3, correct abstention, priority accuracy, undetected injection, wrong merge. A metric over no relevant case has **no value** (not 0, not 1). A pipeline that cannot answer counts as a miss. |
| Variance band | `evalmetrics.variance_band`, `gates.evaluate_gate` | Mean minus 2 sample standard deviations over exactly 3 baseline runs; fewer or more gives no band. A threshold gate with a band fails only below it, and fails if the baseline itself is under the bar. |
| Judge rule | `evalmetrics.judge_may_gate` | A judged metric gates only if judge differs from generator, agreement was measured on at least 30 cases, and the agreement bar (a required setting) is met. Otherwise reported. |
| Evidence builder | `evalmetrics.evidence_for_gates` | Turns run outcomes into the `careflow/gates.py` evidence format from gating cases only. No gating case means no entry, so the gate stays NOT MEASURED. |
| Pre-deployment go/no-go | `careflow/predeploy.py`, `scripts/predeploy_check.py`, `ops/predeploy-evidence.yaml` | The 12 checks of 14.6. Each needs a pointer to its evidence and a verification date or it is NOT MEASURED. The bars are only the ones the spec states. GO only if all 12 are met. |
| Readiness scorecard | `config/readiness-rubric.yaml` | 5 categories x 4 items x 5 points. An item scores only with traced evidence. Pass: total 70 or more and no category under 12. |

CI: the offline job runs `gold_freeze.py` (fails on a malformed or retuned case) and `predeploy_check.py --report-only` (prints NO-GO today). The existing gate scorecard step and the JUnit-to-evidence job are unchanged.

## What is deliberately not here

- **No gold case.** Expected cards, priorities, red flags and urgency are clinical content. The test fixtures use made-up `DEMO-*` labels and are not the gold set.
- **No threshold number.** The three threshold bars arrive as clinician-set values in the evidence; the code has none.
- **No DeepEval, Langfuse experiments or RAGAS.** They are external services and libraries not yet approved (rule 13). The metrics are pure functions, so a judged metric plugs in later through the same `Outcome`.
- **No evidence for the gold-set hard gates** (injection, unverified fact, wrong merge, three-engine contract, voice) until cases exist. They stay NOT MEASURED.

## Needs a person (listed in the phase report)

1. A clinician authors/reviews the gold cases and sets the three threshold bars (clinician sheet, evaluation section).
2. The owner approves or edits the starting readiness rubric.
3. The owner decides whether DeepEval and Langfuse experiments are wanted (external dependencies).
4. Every drill, load test, scan and sign-off in `ops/predeploy-evidence.yaml` is real work by people; nothing is filled in.
