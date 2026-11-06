"""The Care Journey agent offline (careflow/care_journey.py): the closed plan schema, the condition rule, the prefix wait-claim, and the graph driven end to end with fakes sharing ONE event log. The test the
spec names for this phase: a report lacking the needed value routes to a DOCTOR rather than the loop silently closing as "not met". Synthetic labels only (DEMO-*); no clinical content."""

import copy
import uuid
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from careflow.care_journey import (
    CareJourneyDeps,
    Condition,
    Result,
    Step,
    Wait,
    build_care_journey_graph,
    evaluate,
    initial_state,
    match_wait,
    parse_proposal,
    run_config,
)

pytestmark = pytest.mark.asyncio
ON = {"FEATURE_CARE_JOURNEY": "1"}
CLINIC, PLAN, PATIENT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
GOOD = {
    "steps": [
        {"key": "book_review", "kind": "task", "label": "DEMO book a review visit"},
        {"key": "demo_test", "kind": "wait_result", "label": "DEMO wait for the test", "result_prefix": "DEMO-T1", "condition": {"field": "demo_value", "op": "lt", "threshold": 10}, "on_met": "propose_referral"},
    ]
}


# ---- the closed plan schema -----------------------------------------------------------------------------------------------------------------------------------------


def test_a_good_proposal_parses_to_steps():
    steps, problems = parse_proposal(GOOD)
    assert problems == [] and [s.key for s in steps] == ["book_review", "demo_test"]
    assert steps[1].condition == Condition("demo_value", "lt", 10.0) and steps[1].on_met == "propose_referral"


def _mutated(fn):
    data = copy.deepcopy(GOOD)
    fn(data)
    return data


@pytest.mark.parametrize(
    "bad",
    [
        None, [], {}, {"steps": []}, {"steps": "x"}, {"steps": [{}]}, {"steps": [{"key": "a"}]},
        {"steps": [{"key": "a", "kind": "task", "label": "x"}] * 2},  # duplicate key
        _mutated(lambda d: d["steps"][0].update(kind="other")),
        _mutated(lambda d: d["steps"][0].update(label="")),
        _mutated(lambda d: d["steps"][0].update(label="x" * 121)),
        _mutated(lambda d: d["steps"][0].update(extra=1)),  # unknown key: a closed schema
        _mutated(lambda d: d["steps"][0].update(result_prefix="DEMO")),  # a task with wait fields
        _mutated(lambda d: d["steps"][1].pop("result_prefix")),
        _mutated(lambda d: d["steps"][1].update(result_prefix="bad prefix!")),
        _mutated(lambda d: d["steps"][1]["condition"].update(op="approx")),
        _mutated(lambda d: d["steps"][1]["condition"].update(threshold="10")),  # text, not a number
        _mutated(lambda d: d["steps"][1]["condition"].update(threshold=True)),
        _mutated(lambda d: d["steps"][1]["condition"].update(threshold=float("nan"))),
        _mutated(lambda d: d["steps"][1]["condition"].pop("field")),
        _mutated(lambda d: d["steps"][1].update(on_met="send_everything")),
        _mutated(lambda d: (d["steps"][1].pop("condition"), None)),  # on_met without a condition
        {"steps": [{"key": "a", "kind": "task", "label": "x"}] * 21},
    ],
)
def test_a_malformed_proposal_is_rejected_whole_and_never_repaired(bad):
    steps, problems = parse_proposal(bad)
    assert steps == [] and problems


def test_one_bad_step_rejects_the_whole_proposal():
    data = _mutated(lambda d: d["steps"].append({"key": "z", "kind": "nope", "label": "x"}))
    assert parse_proposal(data)[0] == []


# ---- the condition: unknown is not a value --------------------------------------------------------------------------------------------------------------------------


def result(values, *, verified=True, code="DEMO-T1.a", patient=None, rid=None):
    return Result(rid or str(uuid.uuid4()), patient or str(PATIENT), code, values, verified)


@pytest.mark.parametrize(
    ("op", "value", "expected"),
    [("lt", 9, "met"), ("lt", 10, "not_met"), ("le", 10, "met"), ("gt", 11, "met"), ("gt", 10, "not_met"), ("ge", 10, "met"), ("eq", 10, "met"), ("eq", 11, "not_met")],
)
def test_a_verified_value_is_met_or_not_met(op, value, expected):
    assert evaluate(Condition("demo_value", op, 10.0), result({"demo_value": value})) == expected


@pytest.mark.parametrize(
    "r",
    [
        result({}),  # the report lacks the value
        result({"demo_value": None}),
        result({"demo_value": "10"}),  # text
        result({"demo_value": float("nan")}),
        result({"demo_value": float("inf")}),
        result({"demo_value": True}),
        result({"demo_value": 5}, verified=False),  # an unverified value satisfies nothing, even one that would be met
        result({"other_field": 5}),
    ],
)
def test_a_missing_or_unverified_value_is_unknown_in_both_directions(r):
    for op in ("lt", "gt", "eq", "ge", "le"):
        assert evaluate(Condition("demo_value", op, 10.0), r) == "unknown"


# ---- the prefix wait-claim ------------------------------------------------------------------------------------------------------------------------------------------


def wait(key, prefix, patient=None, open_=True):
    return Wait(key, str(PLAN), key, patient or str(PATIENT), prefix, open_)


def test_the_longest_matching_prefix_claims_and_a_tie_is_ambiguous():
    waits = [wait("short", "DEMO-T"), wait("long", "DEMO-T1"), wait("other", "DEMO-X")]
    assert match_wait(waits, result({}, code="DEMO-T1.a"))[1].wait_key == "long"
    assert match_wait(waits, result({}, code="DEMO-T2"))[1].wait_key == "short"
    assert match_wait([wait("a", "DEMO-T1"), wait("b", "demo-t1")], result({}, code="DEMO-T1"))[0] == "ambiguous"  # same prefix, case aside
    assert match_wait(waits, result({}, code="ZZZ")) == ("none", None)


def test_a_result_of_another_patient_or_for_a_closed_wait_matches_nothing():
    assert match_wait([wait("w", "DEMO-T1")], result({}, patient=str(uuid.uuid4())))[0] == "none"
    assert match_wait([wait("w", "DEMO-T1", open_=False)], result({}))[0] == "none"


# ---- the graph, end to end with fakes -------------------------------------------------------------------------------------------------------------------------------


class World:
    def __init__(self, proposal=None, results=None, claims=None, screen="clear"):
        self.screen = screen
        self.log: list[str] = []
        self.proposal = GOOD if proposal is None else proposal
        self.results = results or {}
        self.held: dict[str, str] = dict(claims or {})
        self.tasks: list[dict] = []
        self.closed: list[dict] = []
        self.steps: dict[str, Step] = {}
        self.open_waits: list[str] = []
        self.keys: set[str] = set()

    def deps(self) -> CareJourneyDeps:
        w = self

        class Conn:
            pass

        @asynccontextmanager
        async def session():
            yield Conn()

        class Screener:
            async def screen(self, conn, *, plan_id, clinic_id):
                w.log.append("effect:screen")
                return w.screen

        class Parser:
            async def parse(self, conn, *, plan_id, clinic_id):
                w.log.append("MODEL:parse")
                return w.proposal

        class Proposals:
            async def save(self, conn, *, plan_id, steps, idempotency_key):
                w.log.append("effect:save_proposal")
                assert idempotency_key.startswith("cj:")
                w.steps = {s.key: s for s in steps}

        class Activator:
            async def activate(self, conn, *, plan_id, patient_id, idempotency_key):
                w.log.append("effect:activate")
                w.open_waits = [k for k, s in w.steps.items() if s.kind == "wait_result"]

        class Plans:
            async def next_open_wait(self, conn, *, plan_id):
                return {"step_key": w.open_waits[0], "wait_key": f"wait:{w.open_waits[0]}"} if w.open_waits else None

            async def step(self, conn, *, plan_id, step_key):
                return w.steps[step_key]

            async def wait(self, conn, *, wait_key):
                step = w.steps[wait_key.removeprefix("wait:")]
                return Wait(wait_key, str(PLAN), step.key, str(PATIENT), step.result_prefix or "", True)

        class Results:
            async def get(self, conn, *, result_id):
                return w.results.get(str(result_id))

        class Waits:
            async def claim(self, conn, *, wait_key, result_id, idempotency_key):
                w.log.append("effect:claim")
                assert idempotency_key.startswith("cj:")
                return w.held.setdefault(wait_key, str(result_id)) == str(result_id)

        class Closer:
            async def close_step(self, conn, *, plan_id, step_key, outcome, evidence_result_id, idempotency_key):
                w.log.append(f"effect:close_step:{outcome}")
                assert evidence_result_id is not None  # a close always cites the result that justified it
                w.closed.append({"step": step_key, "outcome": outcome, "evidence": evidence_result_id})
                w.open_waits = [k for k in w.open_waits if k != step_key]

            async def complete(self, conn, *, plan_id, idempotency_key):
                w.log.append("effect:complete")

            async def reject(self, conn, *, plan_id, idempotency_key):
                w.log.append("effect:reject")

        class Tasks:
            async def raise_task(self, conn, *, clinic_id, origin, plan_id, reason, reasons, priority, assigned_role, idempotency_key):
                w.log.append(f"effect:task:{reason}")
                assert priority == "P-TEST" and assigned_role == "doctor" and idempotency_key.startswith("cj:")
                w.tasks.append({"reason": reason, "reasons": reasons})
                if reason == "referral_proposal":
                    w.open_waits = w.open_waits[1:]
                return uuid.uuid4()

        return CareJourneyDeps(session, Screener(), Parser(), Proposals(), Activator(), Plans(), Results(), Waits(), Closer(), Tasks(), "P-TEST", "doctor")  # type: ignore[arg-type]


async def start(world):
    graph = build_care_journey_graph(world.deps(), InMemorySaver(), environ=ON)
    cfg = run_config(PLAN)
    await graph.ainvoke(initial_state(CLINIC, PLAN, PATIENT, "test"), cfg)
    return graph, cfg


async def confirm_and_wait(world):
    graph, cfg = await start(world)
    out = await graph.ainvoke(Command(resume={"action": "approve"}), cfg)
    return graph, cfg, out


def add_result(world, values, **kw):
    r = result(values, **kw)
    world.results[r.result_id] = r
    return r


async def test_nothing_executes_until_a_doctor_confirms_and_the_confirmation_is_unconditional():
    world = World()
    graph, cfg = await start(world)
    assert world.log == ["effect:screen", "MODEL:parse", "effect:save_proposal", "effect:task:plan_confirmation"]  # proposed and asked for, nothing active
    assert (await graph.aget_state(cfg)).next == ("await_confirmation",)
    assert "effect:activate" not in world.log


async def test_a_rejected_plan_activates_nothing():
    world = World()
    graph, cfg = await start(world)
    out = await graph.ainvoke(Command(resume={"action": "reject"}), cfg)
    assert out["status"] == "rejected" and world.log[-1] == "effect:reject" and "effect:activate" not in world.log


async def test_a_malformed_proposal_goes_to_a_doctor_and_saves_nothing():
    world = World(proposal={"steps": [{"key": "a", "kind": "nope", "label": "x"}]})
    graph = build_care_journey_graph(world.deps(), InMemorySaver(), environ=ON)
    out = await graph.ainvoke(initial_state(CLINIC, PLAN, PATIENT, "test"), run_config(PLAN))
    assert "effect:save_proposal" not in world.log and world.tasks == [{"reason": "needs_doctor", "reasons": ["step_kind_or_label"]}] and out["status"] == "plan_invalid"


async def test_a_confirmed_plan_waits_for_its_result():
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    assert (await graph.aget_state(cfg)).next == ("await_result",) and world.log[-1] == "effect:activate"


async def test_a_met_condition_proposes_a_referral_to_a_doctor_and_sends_nothing_out():
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    r = add_result(world, {"demo_value": 5})
    out = await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
    assert out["status"] == "completed" and world.tasks[-1]["reason"] == "referral_proposal"
    assert not [e for e in world.log if "referral" in e and not e.startswith("effect:task")]  # the only referral effect is the doctor's task


async def test_a_verified_value_that_fails_the_condition_closes_the_step_with_its_evidence():
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    r = add_result(world, {"demo_value": 50})
    out = await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
    assert out["status"] == "completed" and world.closed == [{"step": "demo_test", "outcome": "not_met", "evidence": uuid.UUID(r.result_id)}]
    assert world.tasks[-1]["reason"] == "plan_confirmation"  # no doctor task for a clean, evidenced close


@pytest.mark.parametrize(
    "values",
    [{}, {"demo_value": None}, {"demo_value": "n/a"}, {"other": 3}],
)
async def test_a_report_lacking_the_needed_value_routes_to_a_doctor_and_never_closes_as_not_met(values):
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    r = add_result(world, values)
    out = await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
    assert world.closed == [] and "effect:close_step:not_met" not in world.log
    assert world.tasks[-1] == {"reason": "needs_doctor", "reasons": ["value_missing_or_unverified"]} and out["outcome"] == "unknown"


async def test_an_unverified_result_routes_to_a_doctor_even_when_its_value_would_be_met():
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    r = add_result(world, {"demo_value": 1}, verified=False)
    await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
    assert world.tasks[-1]["reason"] == "needs_doctor" and not [t for t in world.tasks if t["reason"] == "referral_proposal"] and world.closed == []


async def test_a_result_for_another_code_or_patient_is_not_evaluated():
    for kw in ({"code": "OTHER-1"}, {"patient": str(uuid.uuid4())}):
        world = World()
        graph, cfg, _ = await confirm_and_wait(world)
        r = add_result(world, {"demo_value": 1}, **kw)
        await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
        assert world.tasks[-1]["reasons"] == ["result_does_not_match_wait"] and world.closed == []


async def test_an_unreadable_result_goes_to_a_doctor():
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    await graph.ainvoke(Command(resume={"kind": "result", "result_id": str(uuid.uuid4())}), cfg)
    assert world.tasks[-1]["reasons"] == ["result_unreadable"]


async def test_an_overdue_wait_goes_to_a_doctor_not_to_a_close():
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    await graph.ainvoke(Command(resume={"kind": "overdue"}), cfg)
    assert world.tasks[-1] == {"reason": "needs_doctor", "reasons": ["result_overdue"]} and world.closed == []


async def test_a_second_result_cannot_take_a_wait_another_result_already_holds():
    world = World(claims={"wait:demo_test": "someone-elses-result"})
    graph, cfg, _ = await confirm_and_wait(world)
    r = add_result(world, {"demo_value": 1})
    await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
    assert (await graph.aget_state(cfg)).next == ("await_result",)  # ignored: still waiting, nothing evaluated, nothing raised
    assert world.closed == [] and [t["reason"] for t in world.tasks] == ["plan_confirmation"]


async def test_a_plan_with_no_result_steps_completes_after_confirmation():
    world = World(proposal={"steps": [{"key": "only_task", "kind": "task", "label": "DEMO a task"}]})
    graph, cfg = await start(world)
    out = await graph.ainvoke(Command(resume={"action": "approve"}), cfg)
    assert out["status"] == "completed"


async def test_a_step_with_no_condition_is_read_by_a_doctor():
    world = World(proposal={"steps": [{"key": "demo_test", "kind": "wait_result", "label": "DEMO", "result_prefix": "DEMO-T1"}]})
    graph, cfg, _ = await confirm_and_wait(world)
    r = add_result(world, {"demo_value": 1})
    await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
    assert world.tasks[-1]["reasons"] == ["no_condition_to_evaluate"]


async def test_the_state_never_holds_plan_text_labels_or_values():
    world = World()
    graph, cfg, _ = await confirm_and_wait(world)
    r = add_result(world, {"demo_value": 5})
    await graph.ainvoke(Command(resume={"kind": "result", "result_id": r.result_id}), cfg)
    stored = repr((await graph.aget_state(cfg)).values)
    for needle in ("DEMO book a review visit", "DEMO wait for the test", "demo_value", "DEMO-T1"):
        assert needle not in stored, needle


async def test_the_rules_screen_the_plan_before_any_model_and_a_flag_ends_the_run_without_a_model_call():
    for screen, status in (("red_flag_escalated", "red_flag_escalated"), ("held_unassessable", "held_unassessable")):
        world = World(screen=screen)
        graph = build_care_journey_graph(world.deps(), InMemorySaver(), environ=ON)
        out = await graph.ainvoke(initial_state(CLINIC, PLAN, PATIENT, "test"), run_config(PLAN))
        assert out["status"] == status or out["status"] == "held_unassessable"
        assert "MODEL:parse" not in world.log and world.log[0] == "effect:screen"
        assert ([t["reason"] for t in world.tasks] == ["needs_doctor"]) == (screen == "held_unassessable")  # a flag's nurse task is the screener's, not this graph's
