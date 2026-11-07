"""The Scheduling graph's behaviour (careflow/scheduling_graph.py), offline: InMemorySaver for the checkpointer, every edge a fake sharing ONE event log, so the tests prove ORDER (a person presses send before anything is
held; the rules screen a reply before any model) and EFFECTS (nothing is booked without the approval matrix saying so; a decline releases and moves to the next candidate; no run ends silently). No database, no model."""

import uuid
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from careflow.autonomy import Policy
from careflow.scheduling_graph import (
    SchedulingDeps,
    build_scheduling_graph,
    initial_state,
    run_config,
)

pytestmark = pytest.mark.asyncio
CLINIC, SLOT, VT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
P1, P2, P3 = (uuid.uuid4() for _ in range(3))
W1, W2, W3 = (uuid.uuid4() for _ in range(3))
REPLY = uuid.uuid4()


class Log(list):
    pass


def world(*, mode="assisted", screen="clear", answer="accept", candidates=3, reason=None, interpreter_result="default"):
    log = Log()

    class Conn:
        pass

    @asynccontextmanager
    async def session():
        yield Conn()

    class Ranker:
        async def rank(self, conn, clinic_id, visit_type_id):
            log.append("rank")
            people = [(W1, P1), (W2, P2), (W3, P3)][:candidates]
            return [{"waitlist_id": w, "patient_id": p, "rank": i + 1} for i, (w, p) in enumerate(people)]

    class Sender:
        async def send(self, conn, **kw):
            log.append(f"effect:offer:{kw['waitlist_id']}")
            assert kw["idempotency_key"].startswith("sched:")
            return {"hold_id": str(uuid.uuid4()), "message_id": str(uuid.uuid4())}

    class Screener:
        async def screen(self, conn, *, message_id):
            log.append("effect:screen")
            return screen

    class Interpreter:
        async def interpret(self, conn, *, message_id, clinic_id):
            log.append("MODEL:interpret")
            if interpreter_result != "default":
                return interpreter_result
            return {"answer": answer, "reason": reason}

    class Booker:
        async def book(self, conn, *, slot_id, patient_id, idempotency_key):
            log.append(f"effect:book:{patient_id}")
            assert idempotency_key.startswith("sched:")

    class Releaser:
        async def release(self, conn, *, hold_id):
            log.append("effect:release")

    class Tasks:
        async def raise_task(self, conn, **kw):
            log.append(f"effect:task:{kw['status']}")
            assert kw["priority"] == "P-TEST" and kw["assigned_role"] == "front_desk" and kw["idempotency_key"].startswith("sched-hold:")
            return uuid.uuid4()

    deps = SchedulingDeps(
        session=session, ranker=Ranker(), sender=Sender(), screener=Screener(), interpreter=Interpreter(), booker=Booker(), releaser=Releaser(), tasks=Tasks(),  # type: ignore[arg-type]
        policy=Policy(mode), max_candidates=2, task_priority="P-TEST", task_role="front_desk",  # test inputs only
    )
    graph = build_scheduling_graph(deps, InMemorySaver(), environ={})
    return graph, log


CONFIG = run_config(SLOT)
START = initial_state(CLINIC, SLOT, VT, "test")


async def values(graph):
    return (await graph.aget_state(CONFIG)).values


async def to_reply_pause(graph):
    await graph.ainvoke(START, CONFIG)  # rank, draft, then pause at await_send
    await graph.ainvoke(Command(resume={"action": "send"}), CONFIG)  # send_offer, then pause at await_reply


async def test_nothing_is_held_or_offered_until_a_person_presses_send_and_only_the_best_ranked_get_a_turn():
    graph, log = world()
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == ("await_send",) and log == ["rank"]  # paused BEFORE any effect
    assert state.values["current"]["patient_id"] == str(P1) and len(state.values["candidates"]) == 2  # backfill_candidates caps the list


async def test_press_send_then_an_accepted_reply_books_the_slot_for_that_patient_in_assisted_mode():
    graph, log = world()
    await to_reply_pause(graph)
    assert (await graph.aget_state(CONFIG)).next == ("await_reply",)
    await graph.ainvoke(Command(resume={"outcome": "replied", "message_id": str(REPLY)}), CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == () and state.values["status"] == "booked"
    assert log == ["rank", f"effect:offer:{W1}", "effect:screen", "MODEL:interpret", f"effect:book:{P1}"]  # rules BEFORE the model; the book is last


async def test_in_shadow_mode_an_accepted_reply_is_a_task_for_a_person_and_nothing_is_booked():
    graph, log = world(mode="shadow")
    await to_reply_pause(graph)
    await graph.ainvoke(Command(resume={"outcome": "replied", "message_id": str(REPLY)}), CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.values["status"] == "booking_needs_human" and state.values["task_id"] and not any(e.startswith("effect:book") for e in log)
    assert log[-1] == "effect:task:booking_needs_human"


@pytest.mark.parametrize("screen", ["red_flag_escalated", "held_unassessable"])
async def test_a_reply_the_rules_flag_or_cannot_assess_ends_the_run_with_no_model_call(screen):
    graph, log = world(screen=screen)
    await to_reply_pause(graph)
    await graph.ainvoke(Command(resume={"outcome": "replied", "message_id": str(REPLY)}), CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == () and state.values["status"] == screen
    assert "MODEL:interpret" not in log and not any(e.startswith("effect:book") for e in log)
    assert (screen == "held_unassessable") == any(e.startswith("effect:task") for e in log)  # an unassessable reply is held for a person; a red flag already has the screener's nurse task


async def test_a_decline_releases_the_hold_and_offers_the_next_ranked_candidate_after_a_person_presses_send_again():
    graph, log = world(answer="decline")
    await to_reply_pause(graph)
    await graph.ainvoke(Command(resume={"outcome": "replied", "message_id": str(REPLY)}), CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == ("await_send",) and state.values["current"]["patient_id"] == str(P2) and "effect:release" in log  # a NEW pause for the next candidate
    assert not any(e.startswith("effect:book") for e in log)


async def test_a_timed_out_offer_releases_without_screening_or_a_model():
    graph, log = world()
    await to_reply_pause(graph)
    await graph.ainvoke(Command(resume={"outcome": "timed_out"}), CONFIG)
    assert "effect:screen" not in log and "MODEL:interpret" not in log and log.count("effect:release") == 1
    assert (await graph.aget_state(CONFIG)).next == ("await_send",)


@pytest.mark.parametrize("result", [{"answer": "unclear", "reason": "no_suggestion"}, {"answer": "maybe"}, None, "accept"])
async def test_an_unclear_or_malformed_suggestion_is_a_task_for_a_person_never_a_booking(result):
    graph, log = world(interpreter_result=result)
    await to_reply_pause(graph)
    await graph.ainvoke(Command(resume={"outcome": "replied", "message_id": str(REPLY)}), CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == () and state.values["task_id"] and not any(e.startswith("effect:book") for e in log)


async def test_skip_moves_on_without_offering_stop_ends_and_running_out_of_candidates_ends_visibly():
    graph, log = world()
    await graph.ainvoke(START, CONFIG)
    await graph.ainvoke(Command(resume={"action": "skip"}), CONFIG)
    assert (await graph.aget_state(CONFIG)).values["current"]["patient_id"] == str(P2) and not any(e.startswith("effect:offer") for e in log)
    await graph.ainvoke(Command(resume={"action": "skip"}), CONFIG)  # the second and last candidate is skipped: exhausted
    assert (await graph.aget_state(CONFIG)).values["status"] == "exhausted" and (await graph.aget_state(CONFIG)).next == ()
    other, other_log = world()
    await other.ainvoke(START, CONFIG)
    await other.ainvoke(Command(resume={"action": "stop"}), CONFIG)
    assert (await other.aget_state(CONFIG)).next == () and not any(e.startswith("effect") for e in other_log)


async def test_no_candidates_ends_the_run_and_a_malformed_decision_is_refused_without_an_effect():
    empty, log = world(candidates=0)
    await empty.ainvoke(START, CONFIG)
    assert (await empty.aget_state(CONFIG)).values["status"] == "no_candidates" and log == ["rank"]
    graph, log2 = world()
    await graph.ainvoke(START, CONFIG)
    with pytest.raises(Exception, match="listed actions"):
        await graph.ainvoke(Command(resume={"action": "book it"}), CONFIG)
    assert log2 == ["rank"]
    await to_reply_pause(world()[0])  # (a fresh graph still works)


async def test_the_state_never_holds_a_name_or_reply_text():
    graph, _ = world()
    await to_reply_pause(graph)
    await graph.ainvoke(Command(resume={"outcome": "replied", "message_id": str(REPLY)}), CONFIG)
    flat = str(await values(graph))
    assert all(key in flat for key in ("candidates", "status")) and "body" not in flat and "name" not in flat.lower().replace("waitlist", "")
