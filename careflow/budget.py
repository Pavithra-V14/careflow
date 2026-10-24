"""The per-run ceilings (BUILD-SPEC.md §6.1, §7.3): 40 steps, 25 tool calls, 12 model calls (warn at 8), 40 s wall-clock per tick, and an
absolute hard stop of $1 equivalent per job. The fifth §7.3 ceiling, 5 attempts then dead-letter, lives in the queue (careflow/queue.py).

THE RULE THIS MODULE EXISTS TO KEEP (§6.1, §5.2, CLAUDE.md rule 2): *the red-flag path and the safety net are never budget-limited.* A
call is either ORDINARY or SAFETY. An ordinary call is counted and refused once a ceiling is passed (`BudgetExceeded`). A SAFETY call is
only COUNTED (so observability can see it) and is NEVER refused, whatever has already been spent: not by the step, tool, model, time or
cost ceiling. It also does not consume the ordinary allowance, so a flood of safety calls cannot starve ordinary work either. Nothing
else in the codebase decides who is exempt: callers pass `CallClass.SAFETY` explicitly, which keeps the exemption visible in code review
and greppable.

Counting is "increment, then check": the 12th model call is allowed and the 13th raises. The warning fires once, when the 8th model call
is counted. A limit that is crossed never silently succeeds: the exception names the ceiling (`kind`) and its limit, never any content.

No logging, no clock reads except through the injected `clock`, no I/O: the budget is a pure counter so it can be tested exhaustively.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

MAX_STEPS: Final = 40
MAX_TOOL_CALLS: Final = 25
MAX_MODEL_CALLS: Final = 12
WARN_MODEL_CALLS: Final = 8
MAX_TICK_SECONDS: Final = 40.0
MAX_COST_USD: Final = 1.0  # §6.1: "absolute hard-stop cost/job = $1 equivalent"


class CallClass(StrEnum):
    ORDINARY = "ordinary"
    SAFETY = "safety"  # the red-flag path and the safety net: never limited


class BudgetExceeded(Exception):
    """An ordinary call would pass a ceiling. Carries the ceiling's name and limit only."""

    def __init__(self, kind: str, limit: float) -> None:
        super().__init__(f"the {kind} ceiling ({limit:g}) was reached")
        self.kind, self.limit = kind, limit


@dataclass(frozen=True)
class Limits:
    steps: int = MAX_STEPS
    tool_calls: int = MAX_TOOL_CALLS
    model_calls: int = MAX_MODEL_CALLS
    warn_model_calls: int = WARN_MODEL_CALLS
    cost_usd: float = MAX_COST_USD


class TickClock:
    """The 40-second wall-clock of one tick, shared by every job the tick runs. Monotonic; the clock is injectable for tests."""

    def __init__(self, seconds: float = MAX_TICK_SECONDS, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock, self._seconds = clock, float(seconds)
        self._started = clock()

    @property
    def deadline(self) -> float:
        return self._started + self._seconds

    def remaining(self) -> float:
        return max(0.0, self.deadline - self._clock())

    def expired(self) -> bool:
        return self._clock() >= self.deadline

    def now(self) -> float:
        return self._clock()


@dataclass
class RunBudget:
    """Counters for ONE job run. Create one per run; share the tick's `TickClock` across runs."""

    tick: TickClock
    limits: Limits = field(default_factory=Limits)
    on_warn: Callable[[str, int, int], None] | None = None  # (ceiling name, used, limit): ids and numbers only
    steps: int = 0
    tool_calls: int = 0
    model_calls: int = 0
    cost_usd: float = 0.0
    safety_steps: int = 0
    safety_tool_calls: int = 0
    safety_model_calls: int = 0
    safety_cost_usd: float = 0.0
    warned: bool = False

    def _check_time(self, call_class: CallClass) -> None:
        if call_class is CallClass.ORDINARY and self.tick.expired():
            raise BudgetExceeded("wall_clock_per_tick", MAX_TICK_SECONDS)

    def step(self, call_class: CallClass = CallClass.ORDINARY) -> None:
        """Count one graph step."""
        if call_class is CallClass.SAFETY:
            self.safety_steps += 1
            return
        self._check_time(call_class)
        if self.steps + 1 > self.limits.steps:
            raise BudgetExceeded("steps", self.limits.steps)
        self.steps += 1

    def tool_call(self, call_class: CallClass = CallClass.ORDINARY) -> None:
        """Count one tool call."""
        if call_class is CallClass.SAFETY:
            self.safety_tool_calls += 1
            return
        self._check_time(call_class)
        if self.tool_calls + 1 > self.limits.tool_calls:
            raise BudgetExceeded("tool_calls", self.limits.tool_calls)
        self.tool_calls += 1

    def model_call(self, call_class: CallClass = CallClass.ORDINARY) -> None:
        """Count one model call; warn once at the warning level."""
        if call_class is CallClass.SAFETY:
            self.safety_model_calls += 1
            return
        self._check_time(call_class)
        if self.model_calls + 1 > self.limits.model_calls:
            raise BudgetExceeded("model_calls", self.limits.model_calls)
        self.model_calls += 1
        if not self.warned and self.model_calls >= self.limits.warn_model_calls:
            self.warned = True
            if self.on_warn is not None:
                self.on_warn("model_calls", self.model_calls, self.limits.model_calls)

    def add_cost(self, usd: float, call_class: CallClass = CallClass.ORDINARY) -> None:
        """Add the actual (not shadow) cost of a call. An ordinary call that would pass the hard stop is refused BEFORE it is recorded."""
        if isinstance(usd, bool) or not isinstance(usd, int | float) or usd < 0:
            raise ValueError("a cost must be a non-negative number")
        if call_class is CallClass.SAFETY:
            self.safety_cost_usd += float(usd)
            return
        if self.cost_usd + usd > self.limits.cost_usd:
            raise BudgetExceeded("cost_usd", self.limits.cost_usd)
        self.cost_usd += float(usd)

    def remaining_seconds(self) -> float:
        return self.tick.remaining()
