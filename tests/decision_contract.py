"""The three-engine contract test harness (BUILD-SPEC.md §5.2, §14.3 hard gate). It runs the SAME golden inputs through any set of decision
adapters and reports violations and agreement. It is reusable: today the adapters sit over fake clients; when the real Laya, Jev and LLM
clients are approved the same `run_contract` runs against them, and THAT run is what meets the hard gate (see docs/design/p2-2-decision-tier.md).

STATED TOLERANCE (an engineering starting value; the owner may change it): across the engines, the choice must agree on at least
AGREEMENT_MIN of the golden items, and where all engines agree their confidences may differ by at most CONFIDENCE_SPREAD_MAX.
"""

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

from careflow.ports import AdapterResult, DataClass, DecisionRequest, DecisionResult

AGREEMENT_MIN = 0.90
CONFIDENCE_SPREAD_MAX = 0.35
PROBABILITY_TOLERANCE = 1e-6

Adapter = Callable[[DecisionRequest], Awaitable[AdapterResult[DecisionResult]]]


@dataclass(frozen=True)
class Golden:
    question_type: str
    text: str
    options: tuple[str, ...]
    expected: str
    language: str = "en"
    data_class: DataClass = DataClass.SYNTHETIC

    def request(self) -> DecisionRequest:
        return DecisionRequest(self.question_type, self.text, self.options, self.data_class, self.language)


@dataclass
class ContractReport:
    violations: list[str] = field(default_factory=list)  # item index + rule name only; never the text
    agreement: float = 0.0
    max_spread: float = 0.0
    declined: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.violations and self.agreement >= AGREEMENT_MIN and self.max_spread <= CONFIDENCE_SPREAD_MAX


def _shape_violations(index: int, engine_name: str, golden: Golden, got: DecisionResult) -> list[str]:
    out = []
    if got.choice not in golden.options:
        out.append(f"{index}:{engine_name}:choice-not-an-option")
    probs = got.probabilities
    if set(probs) != set(golden.options):
        out.append(f"{index}:{engine_name}:probabilities-do-not-cover-the-options")
        return out
    if abs(sum(probs.values()) - 1.0) > PROBABILITY_TOLERANCE or any(not math.isfinite(p) or p < 0 or p > 1 for p in probs.values()):
        out.append(f"{index}:{engine_name}:probabilities-not-a-distribution")
    if got.choice in probs and probs[got.choice] < max(probs.values()) - 1e-12:
        out.append(f"{index}:{engine_name}:choice-is-not-the-argmax")
    if got.choice in probs and abs(got.confidence - probs[got.choice]) > PROBABILITY_TOLERANCE:
        out.append(f"{index}:{engine_name}:confidence-is-not-the-chosen-probability")
    if not got.engine:
        out.append(f"{index}:{engine_name}:no-engine-recorded")
    if not got.route:
        out.append(f"{index}:{engine_name}:no-route-recorded")
    return out


async def run_contract(adapters: Mapping[str, Adapter], golden: list[Golden]) -> ContractReport:
    from careflow.ports import RouteNotApplicable

    report = ContractReport()
    agreed = comparable = 0
    for index, item in enumerate(golden):
        results: dict[str, DecisionResult] = {}
        for name, adapter in adapters.items():
            try:
                results[name] = (await adapter(item.request())).value
            except RouteNotApplicable:
                report.declined[name] = report.declined.get(name, 0) + 1  # an engine may decline an input it cannot take; that is not a violation
                continue
            report.violations.extend(_shape_violations(index, name, item, results[name]))
        if len(results) == len(adapters):
            comparable += 1
            choices = {r.choice for r in results.values()}
            if len(choices) == 1:
                agreed += 1
                confidences = [r.confidence for r in results.values()]
                report.max_spread = max(report.max_spread, max(confidences) - min(confidences))
    report.agreement = agreed / comparable if comparable else 0.0
    return report
