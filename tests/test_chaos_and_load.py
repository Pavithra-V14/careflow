"""Chaos and load tests that need no external service (BUILD-SPEC.md 14.6, 21; owner decision 2026-11-06 item G). Everything runs in this process with the same fakes the other tests use.

CHAOS. A fault is injected into one side-effect port at a time (the port raises once, then works). The graph is resumed from its checkpoint until it finishes. The properties checked are the ones the design promises:
the run ends in the SAME outcome as a run with no fault; every retry of a write carries the SAME idempotency key as the first try (so the database would see one write, rule 5); a model fault never turns a
red flag into "clear"; and a failure never leaves a run silently "finished" with a missing side effect.

LOAD. N times an assumed peak of concurrent runs through the real graphs, with a time bound and a cross-talk check (each run's effects stay in its own world). The peak figure is a LABELLED PLACEHOLDER
(the owner sets the real expected peak, pre-launch checklist 49); the multiplier is the spec's "N times". These prove the code has no shared mutable state between runs and no quadratic blow-up; they do NOT
prove production capacity (that needs hosting and a target load, which are the owner's).
"""

import asyncio
import time
import uuid

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from careflow.care_journey import build_care_journey_graph, initial_state, run_config
from careflow.context_assembler import assemble
from careflow.rules import Rule, RuleSet, evaluate_with
from tests import test_care_journey as cj

pytestmark = pytest.mark.asyncio

ASSUMED_PEAK_CONCURRENT_RUNS = 20  # LABELLED PLACEHOLDER, not a measured figure: the owner sets the real expected peak
LOAD_MULTIPLIER = 10  # the spec's "N times expected peak"
RUN_TIME_BOUND_SECONDS = 60.0


class Flaky:
    """Wraps one port: records (method, idempotency key) for every call and raises on the first call of the chosen method."""

    def __init__(self, inner, calls, fail_method=None):
        self._inner, self._calls, self._fail = inner, calls, fail_method
        self._failed = False

    def __getattr__(self, name):
        target = getattr(self._inner, name)
        if not callable(target):
            return target

        async def wrapped(*args, **kwargs):
            self._calls.append((type(self._inner).__name__, name, kwargs.get("idempotency_key")))
            if self._fail == name and not self._failed:
                self._failed = True
                raise RuntimeError("injected fault")
            return await target(*args, **kwargs)

        return wrapped


SIDE_EFFECT_PORTS = ("screener", "proposals", "activator", "waits", "closer", "tasks")
FAULTS = [("screener", "screen"), ("proposals", "save"), ("tasks", "raise_task"), ("activator", "activate"), ("waits", "claim"), ("closer", "complete")]  # (close_step is not on this path: a met condition proposes a referral task instead)


async def drive(graph, cfg, first):
    """Invoke, and on a fault resume from the checkpoint (what the queue's retry does) until the run reaches its pause or its end."""
    faults = 0
    out = None
    step = first
    for _ in range(6):
        try:
            out = await graph.ainvoke(step, cfg)
            return out, faults
        except RuntimeError:
            faults += 1
            step = None  # resume from the last checkpoint
    raise AssertionError("the run did not recover within 6 resumes")


async def scenario(world, plan_id, fault=None, rid="11111111-1111-1111-1111-111111111111"):
    """Plan -> confirm -> a good result: the full path through every side-effect port. Returns (final outputs, number of faults hit, calls)."""
    calls: list[tuple] = []
    deps = world.deps()
    for port in SIDE_EFFECT_PORTS:
        setattr(deps, port, Flaky(getattr(deps, port), calls, fault[1] if fault is not None and fault[0] == port else None))
    graph = build_care_journey_graph(deps, InMemorySaver(), environ=cj.ON)
    cfg = run_config(plan_id)
    total = 0
    _, f = await drive(graph, cfg, initial_state(cj.CLINIC, plan_id, cj.PATIENT, "test"))
    total += f
    _, f = await drive(graph, cfg, Command(resume={"action": "approve"}))
    total += f
    good = cj.add_result(world, {"demo_value": 5}, rid=rid)  # the same result id in both runs: the key names the result it was claimed for
    out, f = await drive(graph, cfg, Command(resume={"kind": "result", "result_id": good.result_id}))
    total += f
    return out, total, calls


@pytest.mark.parametrize(("port", "method"), FAULTS)
async def test_one_injected_fault_changes_neither_the_outcome_nor_the_idempotency_keys(port, method):
    plan = uuid.uuid4()
    clean_world = cj.World()
    clean, _, clean_calls = await scenario(clean_world, plan)
    world = cj.World()
    out, faults, calls = await scenario(world, plan, fault=(port, method))
    assert faults == 1, "the fault must actually have fired"
    assert out.get("status") == clean.get("status") and [t["reason"] for t in world.tasks] == [t["reason"] for t in clean_world.tasks]
    # A retry must be recognisable as the same write: the SET of (port, method, idempotency key) the faulted run used is exactly the set the clean run used (the retry adds no new key, drops none).
    assert {(k, n, key) for k, n, key in calls} == {(k, n, key) for k, n, key in clean_calls}


async def test_a_model_fault_never_turns_a_red_flag_into_clear():
    class BrokenParser:
        async def parse(self, conn, *, plan_id, clinic_id):
            raise RuntimeError("model down")

    world = cj.World(screen="red_flag_escalated")
    deps = world.deps()
    deps.parser = BrokenParser()  # type: ignore[assignment]
    graph = build_care_journey_graph(deps, InMemorySaver(), environ=cj.ON)
    out = await graph.ainvoke(initial_state(cj.CLINIC, uuid.uuid4(), cj.PATIENT, "test"), run_config(uuid.uuid4()))
    assert out["status"] != "screened" and "MODEL:parse" not in world.log  # the rules ran first, the flag stopped the run, and the model was never reached


async def test_a_model_fault_after_a_clear_screen_holds_for_a_person_not_a_silent_finish():
    class BrokenParser:
        async def parse(self, conn, *, plan_id, clinic_id):
            return None  # the model returned nothing usable

    world = cj.World()
    deps = world.deps()
    deps.parser = BrokenParser()  # type: ignore[assignment]
    graph = build_care_journey_graph(deps, InMemorySaver(), environ=cj.ON)
    out = await graph.ainvoke(initial_state(cj.CLINIC, uuid.uuid4(), cj.PATIENT, "test"), run_config(uuid.uuid4()))
    assert out["status"] != "completed" and world.tasks and world.tasks[0]["reason"] == "needs_doctor"


# ---- load ------------------------------------------------------------------------------------------------------------------------------------------------------------


async def test_n_times_the_assumed_peak_of_concurrent_care_journey_runs_finish_in_time_without_cross_talk():
    n = ASSUMED_PEAK_CONCURRENT_RUNS * LOAD_MULTIPLIER
    worlds = [cj.World() for _ in range(n)]
    started = time.monotonic()
    results = await asyncio.gather(*(scenario(w, uuid.uuid4()) for w in worlds))
    elapsed = time.monotonic() - started
    assert elapsed < RUN_TIME_BOUND_SECONDS, f"{n} runs took {elapsed:.1f}s"
    for world, (out, faults, _) in zip(worlds, results, strict=True):
        assert faults == 0 and out["status"] == results[0][0]["status"]
        assert [t["reason"] for t in world.tasks] == [t["reason"] for t in worlds[0].tasks]  # every run raised exactly its own tasks
        assert world.log.count("effect:activate") == 1  # no run's effect landed twice, none leaked into another world


def _ruleset(count: int) -> RuleSet:
    rules = tuple(Rule(uuid.uuid4(), f"synthetic_{i}", 1, "approved", (f"zzphrase{i} alpha beta",), ()) for i in range(count))
    return RuleSet(rules, "demo")  # type: ignore[arg-type]


@pytest.mark.filterwarnings("ignore::pytest.PytestWarning")
def test_the_deterministic_rules_scale_to_many_messages_and_many_rules_and_stay_correct():
    ruleset = _ruleset(300)
    texts = [f"ordinary words {i} and then some more filler text to scan " * 3 for i in range(3000)]
    planted = {17, 1234, 2999}
    for i in planted:
        texts[i] += " zzphrase42 alpha beta "
    started = time.monotonic()
    flagged = {i for i, t in enumerate(texts) if evaluate_with(ruleset, t).outcome == "red_flag"}
    elapsed = time.monotonic() - started
    assert flagged == planted  # every planted phrase found, nothing else flagged
    assert elapsed < RUN_TIME_BOUND_SECONDS, f"{len(texts)} messages x {len(ruleset.rules)} rules took {elapsed:.1f}s"


def test_the_context_assembler_handles_many_assemblies_within_budget_every_time():
    started = time.monotonic()
    for i in range(5000):
        a = assemble("do the task", budget=200, patient_facts=[(f"f{i}", "fact " * 20)], evidence=[(f"e{i}", "evidence " * 40)], history=[(f"h{j}", "turn " * 30) for j in range(12)])
        assert a.tokens <= a.budget and "instruction" in a.included
    assert time.monotonic() - started < RUN_TIME_BOUND_SECONDS
