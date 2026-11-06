"""The Visit Readiness agent offline (careflow/visit_readiness.py): the gap computation, the readiness lock, the barrier route, the brief, and the graph end to end with fakes sharing ONE event log.
The tests the phase names: a volunteered danger phrase mid-conversation still escalates, and any not-done or unclear readiness answer reaches a nurse in every autonomy mode. Synthetic labels only (DEMO-*);
no question, checklist item or threshold here is clinical content."""

import uuid
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from careflow.autonomy import Policy
from careflow.grounding import FactTier, GroundedFact, Grounding
from careflow.visit_readiness import (
    BARRIERS,
    ChecklistItem,
    IntakeCard,
    ReadinessChecklist,
    VisitReadinessDeps,
    barrier_route,
    build_visit_readiness_graph,
    card_problems,
    checklist_problems,
    compose_brief,
    fact_gap,
    initial_state,
    readiness_outcome,
    run_config,
)

pytestmark = pytest.mark.asyncio
ON = {"FEATURE_VISIT_READINESS": "1"}
CLINIC, VISIT, PATIENT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def fact(name, *, verified=True, tier=FactTier.STRUCTURED):
    return GroundedFact(name, tier, Grounding.VERIFIED if verified else Grounding.UNVERIFIED, "t", "v")


# ---- the gap: code selects, the clinician words ------------------------------------------------------------------------------------------------------------------------


CARD = IntakeCard("DEMO-card", 1, ("demo_a", "demo_b", "demo_c"), {"demo_a": "DEMO question A?", "demo_b": "DEMO question B?"})


def test_only_the_facts_still_missing_or_unverified_are_asked_in_the_cards_order():
    assert fact_gap(CARD, [fact("demo_a")]) == (["demo_b"], ["demo_c"])  # demo_c has no clinician question: unaskable, not guessed
    assert fact_gap(CARD, []) == (["demo_a", "demo_b"], ["demo_c"])
    assert fact_gap(CARD, [fact("demo_a", verified=False)]) == (["demo_a", "demo_b"], ["demo_c"])  # unverified is unknown, so it is asked
    assert fact_gap(CARD, [fact("demo_a"), fact("demo_b"), fact("demo_c")]) == ([], [])  # a detailed referral: no questions


def test_two_conflicting_entries_for_a_fact_count_as_unknown_if_either_is_unusable():
    assert fact_gap(CARD, [fact("demo_a"), fact("demo_a", verified=False), fact("demo_b"), fact("demo_c")])[0] == ["demo_a"]


def test_a_free_text_fact_is_never_usable_so_it_is_still_asked():
    free = GroundedFact("demo_a", FactTier.FREE_TEXT, Grounding.MODEL_GENERATED, "t", "v")
    assert "demo_a" in fact_gap(CARD, [free, fact("demo_b"), fact("demo_c")])[0]


def test_card_structure_problems_are_reported_without_judging_wording():
    assert card_problems(CARD) == []
    assert "question_for_undeclared_fact" in card_problems(IntakeCard("c", 1, ("a",), {"zzz": "q"}))
    assert "empty_question" in card_problems(IntakeCard("c", 1, ("a",), {"a": "  "}))
    assert "fact_names" in card_problems(IntakeCard("c", 1, ("a", "a")))
    assert "fact_names" in card_problems(IntakeCard("c", 1, ("Bad Name",)))


def test_a_checklist_is_structure_checked_and_an_empty_one_is_a_problem():
    ok = ReadinessChecklist("vt", 1, (ChecklistItem("r1", "DEMO item one?"), ChecklistItem("r2", "DEMO item two?")))
    assert checklist_problems(ok) == []
    assert "empty_checklist" in checklist_problems(ReadinessChecklist("vt", 1, ()))
    assert "item_ids" in checklist_problems(ReadinessChecklist("vt", 1, (ChecklistItem("r1", "a"), ChecklistItem("r1", "b"))))
    assert "empty_item_text" in checklist_problems(ReadinessChecklist("vt", 1, (ChecklistItem("r1", " "),)))


# ---- readiness: locked toward a nurse -----------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ({"i1": "confirmed", "i2": "confirmed"}, "proceed"),
        ({"i1": "confirmed", "i2": "not_done"}, "escalate_nurse"),
        ({"i1": "confirmed", "i2": "unclear"}, "escalate_nurse"),
        ({"i1": "confirmed"}, "escalate_nurse"),  # no answer to an item
        ({"i1": "confirmed", "i2": "yes"}, "escalate_nurse"),  # outside the closed set
        ({}, "escalate_nurse"),
    ],
)
def test_proceed_only_when_every_item_is_confirmed(answers, expected):
    assert readiness_outcome(["i1", "i2"], answers)[0] == expected


def test_an_empty_checklist_never_counts_as_ready():
    assert readiness_outcome([], {})[0] == "escalate_nurse"


# ---- barriers ----------------------------------------------------------------------------------------------------------------------------------------------


def test_only_a_scheduling_conflict_hands_off_and_the_matrix_decides_whether_a_person_confirms():
    assert barrier_route("scheduling_conflict", Policy(mode="assisted")) == "rebook"
    assert barrier_route("scheduling_conflict", Policy(mode="autonomous")) == "rebook"
    assert barrier_route("scheduling_conflict", Policy(mode="shadow")) == "rebook_needs_confirmation"
    for barrier in [b for b in BARRIERS if b != "scheduling_conflict"] + ["nonsense", None]:
        for mode in ("shadow", "assisted", "autonomous"):
            assert barrier_route(barrier, Policy(mode=mode)) == "front_desk"


# ---- the brief ---------------------------------------------------------------------------------------------------------------------------------------------


def test_the_brief_labels_every_line_and_upgrades_nothing():
    lines = compose_brief([fact("a"), fact("b", verified=False), GroundedFact("n", FactTier.FREE_TEXT, Grounding.MODEL_GENERATED, "m", "text")])
    assert [(ln.field_name, ln.status) for ln in lines] == [("a", "verified"), ("b", "unverified"), ("n", "model_generated")]


# ---- the graph end to end -------------------------------------------------------------------------------------------------------------------------------------


class World:
    def __init__(self, *, intake=("demo_a", "demo_b"), readiness=("r1", "r2"), unaskable=(), intents=("answers", "answers"), screen=("clear",) * 6, readiness_answers=("confirmed", "confirmed"),
                 grounded=True, barrier="scheduling_conflict", brief_fails=False):
        self.log: list[str] = []
        self.tasks: list[dict] = []
        self.plan = {"intake": list(intake), "readiness": list(readiness), "unaskable": list(unaskable)}
        self.intents, self.screens, self.ready = list(intents), list(screen), list(readiness_answers)
        self.grounded, self.barrier, self.brief_fails = grounded, barrier, brief_fails
        self.rebook: list[bool] = []
        self.briefs: list = []
        self.barrier_records: list[str] = []

    def deps(self, policy=None) -> VisitReadinessDeps:
        w = self

        class Conn:
            pass

        @asynccontextmanager
        async def session():
            yield Conn()

        class Planner:
            async def plan(self, conn, *, visit_id, clinic_id):
                w.log.append("plan")
                return w.plan

        class Sender:
            async def ask(self, conn, *, visit_id, phase, item, idempotency_key):
                w.log.append(f"effect:ask:{phase}:{item}")
                assert idempotency_key.startswith("vr:")
                return uuid.uuid4()

        class Screener:
            async def screen(self, conn, *, message_id):
                w.log.append("effect:screen")
                return w.screens.pop(0)

        class Intents:
            async def classify(self, conn, *, message_id):
                w.log.append("MODEL:intent")
                return w.intents.pop(0)

        class Resolver:
            async def resolve(self, conn, *, visit_id, message_id, fact):
                w.log.append(f"MODEL:resolve:{fact}")
                return w.grounded

        class Facts:
            async def record(self, conn, *, visit_id, fact, message_id, idempotency_key):
                w.log.append(f"effect:fact:{fact}")

        class ReadyClassifier:
            async def classify(self, conn, *, message_id, item):
                w.log.append(f"MODEL:ready:{item}")
                return w.ready.pop(0)

        class ReadyRecorder:
            async def record(self, conn, *, visit_id, item, answer, message_id, idempotency_key):
                w.log.append(f"effect:ready:{item}:{answer}")

        class BarrierClassifier:
            async def classify(self, conn, *, visit_id):
                w.log.append("MODEL:barrier")
                return w.barrier

        class Barriers:
            async def record(self, conn, *, visit_id, barrier, idempotency_key):
                w.barrier_records.append(barrier)

        class Rebooker:
            async def hand_off(self, conn, *, visit_id, needs_confirmation, idempotency_key):
                w.log.append("effect:rebook")
                w.rebook.append(needs_confirmation)

        class Briefs:
            async def generate(self, conn, *, visit_id, clinic_id):
                w.log.append("MODEL:brief")
                if w.brief_fails:
                    raise RuntimeError("model down")
                return [fact("demo_a")]

            async def store(self, conn, *, visit_id, lines, idempotency_key):
                w.briefs.append(lines)

        class Tasks:
            async def raise_task(self, conn, *, clinic_id, origin, visit_id, reason, reasons, role, idempotency_key):
                w.log.append(f"effect:task:{reason}")
                w.tasks.append({"reason": reason, "reasons": reasons, "role": role})
                return uuid.uuid4()

        return VisitReadinessDeps(
            session, Planner(), Sender(), Screener(), Intents(), Resolver(), Facts(), ReadyClassifier(), ReadyRecorder(), BarrierClassifier(), Barriers(), Rebooker(), Briefs(), Tasks(),  # type: ignore[arg-type]
            policy or Policy(mode="assisted"),
        )


async def begin(world, trigger="intake", policy=None):
    graph = build_visit_readiness_graph(world.deps(policy), InMemorySaver(), environ=ON)
    cfg = run_config(VISIT, trigger)
    out = await graph.ainvoke(initial_state(CLINIC, VISIT, PATIENT, "test", trigger), cfg)
    return graph, cfg, out


async def reply(graph, cfg):
    return await graph.ainvoke(Command(resume={"outcome": "replied", "message_id": str(uuid.uuid4())}), cfg)


async def test_a_sparse_referral_asks_each_missing_fact_then_the_checklist_and_proceeds():
    world = World()
    graph, cfg, _ = await begin(world)
    for _ in range(4):
        out = await reply(graph, cfg)
    assert [e for e in world.log if e.startswith("effect:ask")] == ["effect:ask:intake:demo_a", "effect:ask:intake:demo_b", "effect:ask:readiness:r1", "effect:ask:readiness:r2"]
    assert world.tasks == [] and out["readiness_verdict"] == "proceed"


async def test_a_detailed_referral_skips_straight_to_the_checklist_and_with_nothing_left_there_is_no_conversation():
    world = World(intake=(), readiness=())
    _, _, out = await begin(world)
    assert not [e for e in world.log if e.startswith("effect:ask")] and world.tasks == [] and out["status"] == "nothing_to_ask"


async def test_a_danger_phrase_volunteered_in_an_unrelated_answer_still_escalates_before_any_model():
    world = World(screen=("clear", "red_flag_escalated"))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)  # the first answer is fine
    out = await reply(graph, cfg)  # the second answer carries a danger phrase
    assert out["status"] == "red_flag_escalated"
    last_screen = len(world.log) - 1 - world.log[::-1].index("effect:screen")
    assert not [e for e in world.log[last_screen:] if e.startswith("MODEL:")]  # no model read the flagged reply
    assert "effect:fact:demo_b" not in world.log  # the flagged answer was not written as a fact
    assert world.tasks == []  # the screener raised the nurse task and the fixed safety response, not this graph


async def test_every_reply_is_screened_before_a_model_reads_it_in_both_phases():
    world = World(intake=("demo_a",), readiness=("r1",), intents=("answers",), readiness_answers=("confirmed",))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    await reply(graph, cfg)
    for i, entry in enumerate(world.log):
        if entry.startswith("MODEL:") and entry not in {"MODEL:brief"}:
            assert world.log[: i][-1 - world.log[:i][::-1].index("effect:screen")] == "effect:screen"  # a screen precedes the model call
    assert world.log.index("effect:screen") < world.log.index("MODEL:intent")


@pytest.mark.parametrize("mode", ["shadow", "assisted", "autonomous"])
@pytest.mark.parametrize("bad", ["not_done", "unclear"])
async def test_any_not_done_or_unclear_readiness_answer_reaches_a_nurse_in_every_autonomy_mode(mode, bad):
    world = World(intake=(), readiness=("r1", "r2"), readiness_answers=("confirmed", bad))
    graph, cfg, _ = await begin(world, policy=Policy(mode=mode))
    await reply(graph, cfg)
    await reply(graph, cfg)
    assert world.tasks == [{"reason": "readiness_escalation", "reasons": ["item:r2"], "role": "nurse"}]


async def test_an_answer_outside_the_closed_set_counts_as_unclear_and_goes_to_a_nurse():
    world = World(intake=(), readiness=("r1",), readiness_answers=("sure!",))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    assert world.tasks[0]["reason"] == "readiness_escalation" and "effect:ready:r1:unclear" in world.log


async def test_a_clinical_question_is_never_answered_it_goes_to_front_desk():
    world = World(intake=("demo_a",), readiness=(), intents=("clinical_question",))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    assert world.tasks == [{"reason": "clinical_question", "reasons": [], "role": "front_desk"}] and "effect:fact:demo_a" not in world.log


async def test_an_unclear_reply_is_asked_once_more_then_goes_to_front_desk():
    world = World(intake=("demo_a",), readiness=(), intents=("unclear", "unclear"))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    await reply(graph, cfg)
    assert [e for e in world.log if e.startswith("effect:ask")] == ["effect:ask:intake:demo_a"] * 2
    assert world.tasks == [{"reason": "unclear_reply", "reasons": [], "role": "front_desk"}]


async def test_an_intent_outside_the_closed_set_is_unclear():
    world = World(intake=("demo_a",), readiness=(), intents=("whatever", "whatever"))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    await reply(graph, cfg)
    assert world.tasks[0]["reason"] == "unclear_reply"


async def test_an_answer_that_does_not_ground_is_not_written_and_the_fact_is_flagged_as_incomplete():
    world = World(intake=("demo_a",), readiness=(), grounded=False, unaskable=("demo_z",))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    assert "effect:fact:demo_a" not in world.log
    assert world.tasks == [{"reason": "incomplete_intake", "reasons": ["fact:demo_z", "fact:demo_a"], "role": "front_desk"}]


async def test_an_unaskable_fact_is_never_asked_and_is_flagged_at_the_end():
    world = World(intake=(), readiness=(), unaskable=("demo_z",))
    await begin(world)
    assert not [e for e in world.log if e.startswith("effect:ask")]
    assert world.tasks == [{"reason": "incomplete_intake", "reasons": ["fact:demo_z"], "role": "front_desk"}]


async def test_leftover_intake_gaps_are_still_flagged_after_a_readiness_escalation():
    world = World(intake=(), readiness=("r1",), unaskable=("demo_z",), readiness_answers=("not_done",))
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    assert [t["reason"] for t in world.tasks] == ["readiness_escalation", "incomplete_intake"]


async def test_no_reply_before_the_window_closes_goes_to_a_person_and_is_never_taken_as_an_answer():
    world = World(intake=("demo_a",), readiness=())
    graph, cfg, _ = await begin(world)
    await graph.ainvoke(Command(resume={"outcome": "no_reply"}), cfg)
    assert world.tasks[0]["role"] == "front_desk" and "effect:fact:demo_a" not in world.log and "MODEL:intent" not in world.log


@pytest.mark.parametrize(("mode", "needs"), [("shadow", True), ("assisted", False), ("autonomous", False)])
async def test_a_scheduling_conflict_hands_off_to_the_scheduling_flow(mode, needs):
    world = World(barrier="scheduling_conflict")
    _, _, out = await begin(world, "barrier", Policy(mode=mode))
    assert world.rebook == [needs] and world.barrier_records == ["scheduling_conflict"] and out["status"] == "rebooking_handed_off" and world.tasks == []


@pytest.mark.parametrize("barrier", ["transport", "cost", "childcare", "forgot", "other"])
async def test_every_other_barrier_goes_to_front_desk_in_every_mode(barrier):
    for mode in ("shadow", "assisted", "autonomous"):
        world = World(barrier=barrier)
        await begin(world, "barrier", Policy(mode=mode))
        assert world.rebook == [] and world.barrier_records == [barrier] and [t["role"] for t in world.tasks] == ["front_desk"]


async def test_a_barrier_outside_the_closed_set_goes_to_front_desk_and_is_not_recorded():
    world = World(barrier="made_up")
    await begin(world, "barrier")
    assert world.rebook == [] and world.barrier_records == [] and world.tasks[0]["reasons"] == ["barrier_unclassified"]


async def test_a_barrier_expressed_mid_intake_is_classified_not_answered():
    world = World(intake=("demo_a",), readiness=(), intents=("barrier",), barrier="transport")
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    assert world.barrier_records == ["transport"] and [t["role"] for t in world.tasks] == ["front_desk"] and "effect:fact:demo_a" not in world.log


async def test_the_brief_is_stored_labelled_and_a_failed_brief_blocks_nothing_and_raises_nothing():
    world = World()
    _, _, out = await begin(world, "brief")
    assert out["status"] == "brief_stored" and [ln.status for ln in world.briefs[0]] == ["verified"]
    failing = World(brief_fails=True)
    _, _, out = await begin(failing, "brief")
    assert out["status"] == "brief_unavailable" and failing.briefs == [] and failing.tasks == []  # decorative (22.1): the clinician reads the raw chart


async def test_the_state_never_holds_questions_replies_or_fact_values():
    world = World(intake=("demo_a",), readiness=())
    graph, cfg, _ = await begin(world)
    await reply(graph, cfg)
    stored = repr((await graph.aget_state(cfg)).values)
    assert "DEMO question" not in stored and "'v'" not in stored
