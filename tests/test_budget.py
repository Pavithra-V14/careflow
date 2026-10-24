"""careflow.budget: the BUILD-SPEC.md §7.3 / §6.1 ceilings, and the rule that the red-flag path and the safety net are never limited.
Pure: no database, no network, an injected clock."""

import pytest

from careflow.budget import (
    MAX_COST_USD,
    MAX_MODEL_CALLS,
    MAX_STEPS,
    MAX_TICK_SECONDS,
    MAX_TOOL_CALLS,
    WARN_MODEL_CALLS,
    BudgetExceeded,
    CallClass,
    RunBudget,
    TickClock,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def budget(clock=None, warnings=None):
    clock = clock or FakeClock()
    return RunBudget(TickClock(clock=clock), on_warn=(lambda *a: warnings.append(a)) if warnings is not None else None), clock


def test_the_ceilings_are_the_ones_in_the_spec():
    assert (MAX_STEPS, MAX_TOOL_CALLS, MAX_MODEL_CALLS, WARN_MODEL_CALLS, MAX_TICK_SECONDS, MAX_COST_USD) == (40, 25, 12, 8, 40.0, 1.0)


@pytest.mark.parametrize(("method", "kind", "limit"), [("step", "steps", 40), ("tool_call", "tool_calls", 25), ("model_call", "model_calls", 12)])
def test_the_last_allowed_call_succeeds_and_the_next_one_is_refused(method, kind, limit):
    b, _ = budget()
    for _ in range(limit):
        getattr(b, method)()
    with pytest.raises(BudgetExceeded) as exc:
        getattr(b, method)()
    assert (exc.value.kind, exc.value.limit) == (kind, limit)
    assert getattr(b, kind if kind != "steps" else "steps") == limit  # the refused call was not counted


def test_the_model_call_warning_fires_once_at_eight():
    warnings = []
    b, _ = budget(warnings=warnings)
    for _ in range(WARN_MODEL_CALLS - 1):
        b.model_call()
    assert warnings == []
    b.model_call()
    for _ in range(MAX_MODEL_CALLS - WARN_MODEL_CALLS):
        b.model_call()
    assert warnings == [("model_calls", 8, 12)]


def test_the_wall_clock_ceiling_is_per_tick_and_shared_by_every_run_in_it():
    clock = FakeClock()
    tick = TickClock(clock=clock)
    first, second = RunBudget(tick), RunBudget(tick)
    first.step()
    clock.t += MAX_TICK_SECONDS - 0.1
    second.model_call()  # still inside the tick
    clock.t += 0.2
    for call in (first.step, first.tool_call, second.model_call):
        with pytest.raises(BudgetExceeded) as exc:
            call()
        assert exc.value.kind == "wall_clock_per_tick"


def test_the_cost_hard_stop_refuses_before_recording():
    b, _ = budget()
    b.add_cost(0.6)
    with pytest.raises(BudgetExceeded) as exc:
        b.add_cost(0.5)
    assert exc.value.kind == "cost_usd" and b.cost_usd == pytest.approx(0.6)
    b.add_cost(0.4)  # exactly the limit is allowed


@pytest.mark.parametrize("bad", [-1, True, "1", None])
def test_a_cost_must_be_a_non_negative_number(bad):
    b, _ = budget()
    with pytest.raises(ValueError):
        b.add_cost(bad)  # type: ignore[arg-type]


# ---- the rule: the red-flag path and the safety net are never budget-limited ------------------------------------------------------------


def exhaust_everything(b, clock):
    for _ in range(MAX_STEPS):
        b.step()
    for _ in range(MAX_TOOL_CALLS):
        b.tool_call()
    for _ in range(MAX_MODEL_CALLS):
        b.model_call()
    b.add_cost(MAX_COST_USD)
    clock.t += MAX_TICK_SECONDS + 1


def test_every_ordinary_ceiling_is_exhausted_so_the_test_below_means_something():
    b, clock = budget()
    exhaust_everything(b, clock)
    for call in (b.step, b.tool_call, b.model_call, lambda: b.add_cost(0.01)):
        with pytest.raises(BudgetExceeded):
            call()


def test_safety_calls_are_never_refused_after_every_ordinary_ceiling_is_exhausted():
    b, clock = budget()
    exhaust_everything(b, clock)
    for _ in range(500):  # far past every limit, and past the wall clock
        b.step(CallClass.SAFETY)
        b.tool_call(CallClass.SAFETY)
        b.model_call(CallClass.SAFETY)
        b.add_cost(5.0, CallClass.SAFETY)
    assert (b.safety_steps, b.safety_tool_calls, b.safety_model_calls) == (500, 500, 500)
    assert b.safety_cost_usd == pytest.approx(2500.0)


def test_safety_calls_never_consume_the_ordinary_allowance():
    b, _ = budget()
    for _ in range(100):
        b.step(CallClass.SAFETY)
        b.tool_call(CallClass.SAFETY)
        b.model_call(CallClass.SAFETY)
        b.add_cost(2.0, CallClass.SAFETY)
    assert (b.steps, b.tool_calls, b.model_calls, b.cost_usd) == (0, 0, 0, 0.0)
    for _ in range(MAX_MODEL_CALLS):  # the full ordinary allowance is still there
        b.model_call()
    with pytest.raises(BudgetExceeded):
        b.model_call()


def test_a_safety_model_call_does_not_trigger_the_ordinary_warning():
    warnings = []
    b, _ = budget(warnings=warnings)
    for _ in range(20):
        b.model_call(CallClass.SAFETY)
    assert warnings == []


def test_the_default_call_class_is_ordinary_so_exemption_must_be_asked_for_by_name():
    b, _ = budget()
    for _ in range(MAX_MODEL_CALLS):
        b.model_call()
    with pytest.raises(BudgetExceeded):
        b.model_call()  # no argument: ordinary, refused
