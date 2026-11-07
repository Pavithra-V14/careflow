"""The five checks before a write tool executes, in order (careflow/write_pipeline.py; BUILD-SPEC.md 8). Offline, with fake executors."""

import pytest
from pydantic import Field

from careflow.autonomy import Policy
from careflow.budget import CallClass, Limits, RunBudget, TickClock
from careflow.write_pipeline import StrictArgs, ToolResult, guarded_write

pytestmark = pytest.mark.asyncio


class Args(StrictArgs):
    count: int = Field(ge=1, le=5)
    kind: str


def world(*, allowed=True, mode="assisted", budget=None):
    log: list[str] = []

    async def authorize(args):
        log.append("authorize")
        return allowed

    async def execute(args, key):
        log.append(f"execute:{key}")
        return ToolResult(status="ok", reason="executed", data={"count": args.count})

    return log, authorize, execute, Policy(mode), budget


async def run(w, raw=None, key="k-1", action="booking", **kw):
    _log, authorize, execute, policy, budget = w
    return await guarded_write(
        schema=Args, raw_args=raw if raw is not None else {"count": 2, "kind": "x"}, authorize=authorize, action_class=action, policy=policy, budget=budget, idempotency_key=key, execute=execute, **kw
    )


async def test_all_five_pass_in_order_and_only_then_the_executor_runs_with_the_validated_arguments():
    w = world()
    r = await run(w)
    assert (r.status, r.reason, r.data) == ("ok", "executed", {"count": 2}) and w[0] == ["authorize", "execute:k-1"]


@pytest.mark.parametrize("raw", [{"count": 0, "kind": "x"}, {"count": 9, "kind": "x"}, {"count": 2}, {"count": "two", "kind": "x"}, {"count": 2, "kind": "x", "extra": 1}, {}])
async def test_step_one_bad_arguments_are_invalid_and_nothing_else_runs(raw):
    w = world()
    r = await run(w, raw)
    assert (r.status, r.reason) == ("invalid", "bad_arguments") and w[0] == []


async def test_step_two_a_row_the_actor_has_no_rights_on_is_refused_before_policy_budget_or_execution():
    budget = RunBudget(TickClock())
    w = world(allowed=False, budget=budget)
    r = await run(w)
    assert (r.status, r.reason) == ("refused", "not_authorized") and w[0] == ["authorize"] and budget.tool_calls == 0


async def test_step_three_shadow_mode_returns_the_proposal_for_a_person_and_executes_nothing():
    budget = RunBudget(TickClock())
    w = world(mode="shadow", budget=budget)
    r = await run(w)
    assert (r.status, r.reason, r.data) == ("needs_human", "human_decides", {"count": 2, "kind": "x"}) and w[0] == ["authorize"] and budget.tool_calls == 0


async def test_a_locked_class_is_a_person_even_in_autonomous_mode():
    w = world(mode="autonomous")
    r = await run(w, action="patient_merge")
    assert r.status == "needs_human" and "execute:k-1" not in w[0]


async def test_step_four_the_tool_call_ceiling_stops_an_ordinary_call_but_never_a_safety_call():
    budget = RunBudget(TickClock(), Limits(tool_calls=1))
    first = await run(world(budget=budget))
    second = await run(world(budget=budget))
    assert (first.status, second.status, second.reason) == ("ok", "budget_exhausted", "tool_call_ceiling")
    safety = await run(world(budget=budget), call_class=CallClass.SAFETY)
    assert safety.status == "ok"


@pytest.mark.parametrize("key", [None, "", "   ", 5, "k" * 201], ids=["none", "empty", "blank", "int", "long"])
async def test_step_five_no_usable_idempotency_key_is_a_refusal_and_nothing_executes(key):
    w = world()
    r = await run(w, key=key)
    assert (r.status, r.reason) == ("refused", "missing_idempotency_key") and not any(e.startswith("execute") for e in w[0])


async def test_an_executor_may_not_answer_with_a_refusal_status():
    _log, authorize, _execute, policy, budget = world()

    async def bad(args, key):
        return ToolResult(status="refused", reason="none")

    with pytest.raises(TypeError):
        await guarded_write(schema=Args, raw_args={"count": 1, "kind": "x"}, authorize=authorize, action_class="booking", policy=policy, budget=budget, idempotency_key="k", execute=bad)


async def test_an_empty_result_is_an_explicit_status_never_a_bare_empty_value():
    _log, authorize, _execute, policy, budget = world()

    async def nothing(args, key):
        return ToolResult(status="empty", reason="nothing_to_do", data=[])

    r = await guarded_write(schema=Args, raw_args={"count": 1, "kind": "x"}, authorize=authorize, action_class="booking", policy=policy, budget=budget, idempotency_key="k", execute=nothing)
    assert (r.status, r.reason) == ("empty", "nothing_to_do")
