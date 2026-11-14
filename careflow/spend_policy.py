"""Small pure policy pieces from BUILD-SPEC.md sections 5.3, 6.2, 14.4, 15.2 and 15.4. No database, no network, no model, no clinical content.

  * `soft_threshold_plan` (15.2): at 80% of a cap the DECORATIVE features go off, in the spec's order; at the cap, new non-urgent jobs queue; the red-flag path and the safety net are never budget-limited.
    An unknown spend or an unknown cap changes nothing and says so (unknown is not a value).
  * `failure_multiplier` (15.4): effective cost per success divided by nominal cost per call; no value when there is no success (never 0 or infinity).
  * `tail_review` (15.4): the ids and costs of the costliest 5% of jobs, for the weekly admin summary. Ids and numbers only.
  * `grounding_rate` (14.4): the share of decision-relevant facts that passed the tiered checks; no value when none were checked.
  * `check_pinned_models` (5.3): which pinned model ids a provider's listing no longer shows. A provider whose listing could not be read is UNVERIFIABLE, never "vanished".
  * `latency_parts` (14.4): machine time, human wait and external wait summed separately, never blended.
  * `RunMemo` (6.2): request-scoped memoisation of tool reads within ONE run; discarded with the run.
"""

import hashlib
import json
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Final

SOFT_FRACTION: Final = Decimal("0.8")
DECORATIVE_ORDER: Final = ("card_shortlist_ensemble", "reranker", "judge_sampling")  # 15.2: switched off in this order
TAIL_SHARE: Final = Decimal("0.05")


@dataclass(frozen=True)
class SpendPlan:
    known: bool  # False when the spend or the cap is not known: nothing is changed
    disable: tuple[str, ...]  # decorative features to switch off
    queue_non_urgent: bool  # at the cap
    safety_limited: bool = False  # the red-flag path and the safety net are never budget-limited, whatever the numbers say


def soft_threshold_plan(spent: Decimal | None, cap: Decimal | None) -> SpendPlan:
    if spent is None or cap is None or cap <= 0 or spent < 0:
        return SpendPlan(False, (), False)
    if spent >= cap:
        return SpendPlan(True, DECORATIVE_ORDER, True)
    if spent >= cap * SOFT_FRACTION:
        return SpendPlan(True, DECORATIVE_ORDER, False)
    return SpendPlan(True, (), False)


def failure_multiplier(total_cost: Decimal, successes: int, nominal_cost_per_call: Decimal, calls: int) -> Decimal | None:
    """(total cost / successes) / nominal cost per call. None unless there is at least one success, a positive nominal cost and at least one call."""
    if successes <= 0 or calls <= 0 or nominal_cost_per_call <= 0 or total_cost < 0:
        return None
    return (total_cost / successes) / nominal_cost_per_call


def tail_review(jobs: Sequence[tuple[str, Decimal]], share: Decimal = TAIL_SHARE) -> list[tuple[str, Decimal]]:
    """The costliest `share` of jobs (at least one when there are any), most expensive first: (job id, cost). Nothing else leaves it."""
    if not jobs:
        return []
    keep = max(1, math.ceil(len(jobs) * share))
    return sorted(jobs, key=lambda j: (-j[1], j[0]))[:keep]


def grounding_rate(passed: int, checked: int) -> Decimal | None:
    if checked <= 0 or passed < 0 or passed > checked:
        return None
    return Decimal(passed) / Decimal(checked)


@dataclass(frozen=True)
class DeprecationReport:
    vanished: dict[str, frozenset[str]] = field(default_factory=dict)  # provider -> pinned ids its listing no longer shows
    unverifiable: frozenset[str] = frozenset()  # providers whose listing could not be read: nothing is concluded about them


def check_pinned_models(pinned: Mapping[str, frozenset[str]], listed: Mapping[str, frozenset[str] | None]) -> DeprecationReport:
    vanished: dict[str, frozenset[str]] = {}
    unverifiable: set[str] = set()
    for provider, ids in pinned.items():
        shown = listed.get(provider)
        if shown is None:
            unverifiable.add(provider)
            continue
        gone = frozenset(i for i in ids if i not in shown)
        if gone:
            vanished[provider] = gone
    return DeprecationReport(vanished, frozenset(unverifiable))


LATENCY_KINDS: Final = ("machine", "human_wait", "external_wait")


def latency_parts(intervals: Sequence[tuple[str, datetime, datetime]]) -> dict[str, timedelta]:
    """Latency per completed task in THREE parts, never blended (14.4): machine time, human wait, external wait (a patient reply, a pending result). A kind outside the three or an interval that ends before it starts is an error."""
    totals = {k: timedelta(0) for k in LATENCY_KINDS}
    for kind, start, end in intervals:
        if kind not in totals or end < start:
            raise ValueError("an interval has an unknown kind or ends before it starts")
        totals[kind] += end - start
    return totals


class RunMemo:
    """Create one per run. A tool read with the same name and arguments inside the run is computed once."""

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self.hits = 0

    @staticmethod
    def _key(tool: str, arguments: Mapping[str, Any]) -> str:
        return tool + ":" + hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()

    async def get(self, tool: str, arguments: Mapping[str, Any], compute: Callable[[], Awaitable[Any]]) -> Any:
        key = self._key(tool, arguments)
        if key in self._values:
            self.hits += 1
            return self._values[key]
        value = await compute()
        self._values[key] = value
        return value
