"""The gate scorecard (BUILD-SPEC.md 14.3; docs/design/remaining-work.md A11). A pure evaluator: it takes EVIDENCE (a mapping from gate id to what was measured) and says, per gate,
MEASURED (and passed), FAILED, or NOT MEASURED with the reason. It FAILS CLOSED and INVENTS NO NUMBER:

  * A gate with no evidence is NOT MEASURED, never passed. The default evidence is empty, so today every gate reads NOT MEASURED (the gold sets do not exist yet).
  * A HARD gate is binary and its bar is written in the spec (100%, zero): evidence is {"passed": bool, "source": "..."}; a recall gate may give {"value": 1.0, "source": ...} and passes only at exactly 1.0.
  * A THRESHOLD gate needs a number from a clinician (the spec's figures are starting values "a clinical lead adjusts"). The bars are NOT in this file: they arrive in the evidence as
    {"value": x, "bar": b, "source": ...}. A threshold gate with no bar is NOT MEASURED ("bar not set"). # CLINICAL-PLACEHOLDER: needs clinician sign-off
  * A REPORTED gate never gates: it prints MEASURED or NOT MEASURED and cannot fail.
  * Unknown is not a value: a malformed entry, a non-number, or an evidence key that names no gate is reported, never ignored and never counted as a pass.

Nothing here reads a database, a file or the network; scripts/gate_scorecard.py loads the evidence file.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

Kind = Literal["hard", "threshold", "reported"]
Status = Literal["MEASURED", "NOT MEASURED", "FAILED"]


@dataclass(frozen=True)
class Gate:
    id: str
    kind: Kind
    description: str


# The gates of BUILD-SPEC.md 14.3, by name only. No bar is stored here except where the spec states a binary one (hard gates are all-or-nothing).
GATES: Final[tuple[Gate, ...]] = (
    Gate("red_flag_recall", "hard", "Red-flag recall = 100% on the gold set"),
    Gate("urgent_case_recall", "hard", "Urgent-case recall = 100% on the gold set"),
    Gate("injection_undetected", "hard", "No injected or adversarial text changes a priority or extracted value undetected"),
    Gate("unverified_fact_reaches_rule", "hard", "No unverified fact reaches a rule"),
    Gate("wrong_patient_merge", "hard", "No wrong patient merge on the labeled identity-matching pairs"),
    Gate("clinic_isolation_tests", "hard", "Clinic-isolation tests pass"),
    Gate("double_booking_tests", "hard", "Double-booking tests pass"),
    Gate("idempotency_tests", "hard", "Idempotency tests pass"),
    Gate("three_engine_contract", "hard", "The three-engine (Laya/Jev/LLM) contract test passes"),
    Gate("voice_safety_escalation_rate", "hard", "Voice safety escalation rate = 100% on the voice gold set"),
    Gate("card_recall_at_3", "threshold", "Protocol-card recall@3 at or above the variance band"),
    Gate("correct_abstention_rate", "threshold", "Correct-abstention rate at or above the variance band"),
    Gate("priority_accuracy", "threshold", "Priority accuracy at or above the variance band"),
    Gate("cost_per_outcome", "reported", "Cost per outcome (reported, not gating)"),
    Gate("latency", "reported", "Latency (reported, not gating)"),
    Gate("judged_quality", "reported", "Judged quality (reported until the judge is calibrated)"),
)
GATE_IDS: Final = frozenset(g.id for g in GATES)
_RECALL_GATES: Final = frozenset({"red_flag_recall", "urgent_case_recall", "voice_safety_escalation_rate"})


@dataclass(frozen=True)
class Result:
    gate: Gate
    status: Status
    reason: str  # fixed words; never data
    source: str | None = None


def _number(x: object) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) and x == x else None  # noqa: PLR0124  (x == x: NaN is not a number here)


def _source(entry: Mapping[str, Any]) -> str | None:
    s = entry.get("source")
    return s if isinstance(s, str) and 0 < len(s) <= 200 else None


def evaluate_gate(gate: Gate, entry: object) -> Result:
    if entry is None:
        return Result(gate, "NOT MEASURED", "no evidence")
    if not isinstance(entry, Mapping):
        return Result(gate, "NOT MEASURED", "evidence is malformed")
    src = _source(entry)
    if src is None:
        return Result(gate, "NOT MEASURED", "evidence names no source")  # a number nobody can trace is not evidence
    value = _number(entry.get("value")) if "value" in entry else None
    if gate.kind == "hard":
        if gate.id in _RECALL_GATES and "value" in entry:
            if value is None:
                return Result(gate, "NOT MEASURED", "value is not a number", src)
            return Result(gate, "MEASURED", "at 100%", src) if value == 1.0 else Result(gate, "FAILED", "below 100%", src)
        passed = entry.get("passed")
        if not isinstance(passed, bool):
            return Result(gate, "NOT MEASURED", "evidence has no pass/fail", src)
        return Result(gate, "MEASURED", "passed", src) if passed else Result(gate, "FAILED", "did not pass", src)
    if gate.kind == "threshold":
        bar = _number(entry.get("bar"))
        if bar is None:
            return Result(gate, "NOT MEASURED", "bar not set (a clinician sets it)", src)
        if value is None:
            return Result(gate, "NOT MEASURED", "value is not a number", src)
        return Result(gate, "MEASURED", "at or above the bar", src) if value >= bar else Result(gate, "FAILED", "below the bar", src)
    return Result(gate, "MEASURED", "reported", src) if value is not None else Result(gate, "NOT MEASURED", "value is not a number", src)


@dataclass(frozen=True)
class Scorecard:
    results: tuple[Result, ...]
    unknown_evidence: tuple[str, ...]  # evidence keys that name no gate

    @property
    def failed(self) -> bool:
        return any(r.status == "FAILED" for r in self.results)

    @property
    def releasable(self) -> bool:
        """True only when EVERY hard and threshold gate is MEASURED and passed and no evidence key was unknown. Anything unmeasured blocks."""
        gating = [r for r in self.results if r.gate.kind != "reported"]
        return not self.unknown_evidence and all(r.status == "MEASURED" for r in gating)


def evaluate(evidence: Mapping[str, object] | None = None) -> Scorecard:
    evidence = evidence or {}
    return Scorecard(tuple(evaluate_gate(g, evidence.get(g.id)) for g in GATES), tuple(sorted(k for k in evidence if k not in GATE_IDS)))


def render(card: Scorecard) -> str:
    lines = [f"{'GATE':32} {'KIND':10} {'STATUS':13} REASON"]
    lines += [f"{r.gate.id:32} {r.gate.kind:10} {r.status:13} {r.reason}" + (f" [{r.source}]" if r.source else "") for r in card.results]
    measured = sum(r.status == "MEASURED" for r in card.results)
    lines.append(f"\n{measured} of {len(card.results)} gates measured; releasable: {'YES' if card.releasable else 'NO'}")
    if card.unknown_evidence:
        lines.append("evidence names no gate (ignored, and it blocks release): " + ", ".join(card.unknown_evidence))
    return "\n".join(lines)
