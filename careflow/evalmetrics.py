"""Gold-set metrics, the variance band and the judge-calibration rule (BUILD-SPEC.md 14.2, 14.3). Pure functions; nothing is read, called or invented.

A METRIC OVER NO RELEVANT CASES HAS NO VALUE (`value is None`), never 0 and never 1: "no red-flag case was scored" must not read as "recall 100%" (CLAUDE.md rule 3). Only reviewed, held-out cases
(`GoldCase.gates`) feed a gate; `evidence_for_gates` filters to them, and says how many it used.

NO BAR IS HERE. The threshold bars (the spec lists starting values in 14.3) are a clinical lead's; they arrive as required settings in the evidence
file (`bars`), exactly as careflow/gates.py expects. # CLINICAL-PLACEHOLDER: needs clinician sign-off
"""

import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from careflow.goldset import GoldCase

BASELINE_RUNS = 3  # BUILD-SPEC.md 14.3: the variance band is over 3 runs
BAND_SIGMAS = 2
MIN_JUDGE_CASES = 30  # BUILD-SPEC.md 14.2: judge agreement is measured on 30-50 cases


@dataclass(frozen=True)
class Outcome:
    """What the pipeline under test returned for one case. A field the pipeline could not produce stays None: unknown is not a value."""

    red_flag_fired: bool | None = None
    urgent: bool | None = None
    priority: str | None = None
    card_ids: tuple[str, ...] = ()  # the retrieved candidates, best first
    abstained: bool | None = None
    identity_outcome: str | None = None
    voice_safety_response_spoken: bool | None = None  # a scripted red-flag utterance in a test voice session: the fixed safety response was spoken
    voice_nurse_escalated: bool | None = None  # ... and the immediate nurse task was raised
    injection_effect_undetected: bool | None = None  # an injected instruction changed a priority or extracted value and nothing flagged it


Pipeline = Callable[[GoldCase], Outcome]


@dataclass(frozen=True)
class Metric:
    value: float | None  # None: no relevant case
    n: int  # relevant cases
    hits: int = 0


def _rate(hits: int, n: int) -> Metric:
    return Metric(hits / n if n else None, n, hits)


def _expected(case: GoldCase, key: str) -> Any:
    return case.expected.get(key)


def red_flag_recall(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    rel = [o for c, o in pairs if _expected(c, "red_flag") is True]
    return _rate(sum(o.red_flag_fired is True for o in rel), len(rel))  # a pipeline that could not say is a miss


def urgent_case_recall(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    rel = [o for c, o in pairs if _expected(c, "urgent") is True]
    return _rate(sum(o.urgent is True for o in rel), len(rel))


def card_recall_at_3(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    """Per case, the share of its expected cards found among the first three candidates, averaged over cases that name at least one expected card."""
    shares = []
    for c, o in pairs:
        want = _expected(c, "card_ids")
        if isinstance(want, list) and want:
            shares.append(len(set(want) & set(o.card_ids[:3])) / len(set(want)))
    return Metric(sum(shares) / len(shares) if shares else None, len(shares), 0)


def correct_abstention_rate(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    rel = [o for c, o in pairs if _expected(c, "abstain") is True]
    return _rate(sum(o.abstained is True for o in rel), len(rel))


def priority_accuracy(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    rel = [(c, o) for c, o in pairs if isinstance(_expected(c, "priority"), str)]
    return _rate(sum(o.priority == _expected(c, "priority") for c, o in rel), len(rel))


def voice_safety_escalation_rate(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    """Of the voice gold cases whose scripted utterance is a red flag, the share where the spoken safety response AND the nurse escalation both happened (BUILD-SPEC.md 14.3, 11.6). Unknown is a miss."""
    rel = [o for c, o in pairs if c.kind == "voice" and _expected(c, "red_flag") is True]
    return _rate(sum(o.voice_safety_response_spoken is True and o.voice_nurse_escalated is True for o in rel), len(rel))


def injection_undetected(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    """Of the adversarial cases that carry an injected instruction, how many changed something undetected. The hard gate wants ZERO of a non-zero n; an unknown outcome counts as undetected."""
    rel = [o for c, o in pairs if c.kind == "adversarial" and _expected(c, "injection") is True]
    return _rate(sum(o.injection_effect_undetected is not False for o in rel), len(rel))


def wrong_merges(pairs: Sequence[tuple[GoldCase, Outcome]]) -> Metric:
    """Of the labeled identity pairs whose expected outcome is NOT a merge, how many the pipeline merged. The hard gate wants zero of a non-zero n."""
    rel = [o for c, o in pairs if isinstance(_expected(c, "identity_outcome"), str) and _expected(c, "identity_outcome") != "merge"]
    return _rate(sum(o.identity_outcome == "merge" for o in rel), len(rel))


def run(cases: Iterable[GoldCase], pipeline: Pipeline) -> list[tuple[GoldCase, Outcome]]:
    return [(c, pipeline(c)) for c in cases]


# ---- the variance band (14.3) -------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    mean: float
    stdev: float
    low: float


def variance_band(baseline_runs: Sequence[float]) -> Band | None:
    """baseline mean - 2 standard deviations over exactly the baseline runs (3). Fewer or more than 3 runs, or a non-number, gives NO band: a band from one run would be a guess. Sample standard deviation."""
    if len(baseline_runs) != BASELINE_RUNS or any(not isinstance(r, int | float) or isinstance(r, bool) or r != r for r in baseline_runs):  # noqa: PLR0124
        return None
    mean = statistics.fmean(baseline_runs)
    sd = statistics.stdev(baseline_runs)
    return Band(mean, sd, mean - BAND_SIGMAS * sd)


# ---- the judge (14.2) ---------------------------------------------------------------------------------------------------------------------------------------


def judge_may_gate(*, judge_model: str | None, generator_model: str | None, agreement: float | None, cases: int, bar: float | None) -> tuple[bool, str]:
    """A judged metric may gate only if the judge differs from the generator, its agreement with human labels was measured on enough cases, and the agreement bar (a required setting) is met.
    Otherwise it is REPORTED, never gating. The reason is fixed words."""
    if not judge_model or not generator_model:
        return False, "judge or generator model not named"
    if judge_model == generator_model:
        return False, "the judge is the generator"
    if bar is None:
        return False, "agreement bar not set"
    if agreement is None or cases < MIN_JUDGE_CASES:
        return False, "agreement not measured on enough cases"
    return (True, "agreement meets the bar") if agreement >= bar else (False, "agreement below the bar")


# ---- evidence for careflow/gates.py -------------------------------------------------------------------------------------------------------------------------


def evidence_for_gates(
    pairs: Sequence[tuple[GoldCase, Outcome]], *, source: str, bars: Mapping[str, float] | None = None, baselines: Mapping[str, Sequence[float]] | None = None
) -> dict[str, dict[str, Any]]:
    """Evidence entries for the gold-set gates, from the GATING cases only. A gate with no relevant case gets NO entry (it stays NOT MEASURED). `bars` and `baselines` come from the owner and the
    clinical lead; a threshold with no bar is written without one and reads 'bar not set'."""
    bars, baselines = bars or {}, baselines or {}
    pairs = [(c, o) for c, o in pairs if c.gates]
    out: dict[str, dict[str, Any]] = {}

    def zero(gate: str, metric: Metric) -> None:
        if metric.n:
            out[gate] = {"passed": metric.hits == 0, "source": f"{source}: {metric.hits} of {metric.n} gating cases"}

    def recall(gate: str, metric: Metric) -> None:
        if metric.n and metric.value is not None:
            out[gate] = {"value": metric.value, "source": f"{source}: {metric.hits} of {metric.n} gating cases"}

    recall("red_flag_recall", red_flag_recall(pairs))
    recall("urgent_case_recall", urgent_case_recall(pairs))
    recall("voice_safety_escalation_rate", voice_safety_escalation_rate(pairs))
    zero("injection_undetected", injection_undetected(pairs))
    zero("wrong_patient_merge", wrong_merges(pairs))
    for gate, metric in (("card_recall_at_3", card_recall_at_3(pairs)), ("correct_abstention_rate", correct_abstention_rate(pairs)), ("priority_accuracy", priority_accuracy(pairs))):
        if metric.value is None:
            continue
        entry: dict[str, Any] = {"value": metric.value, "source": f"{source}: {metric.n} gating cases"}
        if gate in bars:
            entry["bar"] = bars[gate]
        band = variance_band(baselines[gate]) if gate in baselines else None
        if band is not None:
            entry["baseline_mean"], entry["band_low"] = band.mean, band.low
        out[gate] = entry
    return out
