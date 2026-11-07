"""The Scheduling agent's graph (BUILD-SPEC.md 1, 4, 7, 12.1; docs/design/phase3-scheduling-agent.md). MECHANISM ONLY: every port is an interface with a fake in the tests, and every clinician-owned value is a
required setting. A freed slot goes to the best-ranked waiting patient FIRST by clinical priority, never to the one who waited longest (careflow/scheduling.py ranks; this graph only walks the ranking).

    rank -> draft_offer -> await_send (PAUSE: a person presses send) -> send_offer -> await_reply (PAUSE: the patient's reply is entered)
         -> screen_reply -> interpret_reply -> decide -> book_slot | release_hold -> (next candidate) | hold_for_human

  * `rank`            reads the ranked waitlist for the freed slot's visit type (nothing is held yet). No candidate ends the run (`no_candidates`).
  * `draft_offer`     picks the current candidate (pure). No candidate left ends the run (`exhausted`).
  * `await_send`      PAUSES for a person: send, skip this candidate, or stop. No side effect, no port (it only asks and validates). Nothing is ever offered without this pause.
  * `send_offer`      holds the slot for the candidate and records the offer message (idempotent). The side effect sits IMMEDIATELY before the next pause (7.2). Nothing is delivered by the system.
  * `await_reply`     PAUSES until the front desk enters the patient's reply (or says it timed out). No side effect.
  * `screen_reply`    the deterministic red-flag rules run on the reply FIRST, before any model (CLAUDE.md rule 2). A red flag or an unassessable reply raises the nurse task and ENDS the run.
  * `interpret_reply` the `decision` tier suggests accept, decline or unclear. A SUGGESTION: it never books by itself.
  * `decide`          the approval matrix (careflow/autonomy.py) decides whether a booking may run without a person: in `shadow` mode it never does. Reads the policy only.
  * `book_slot`       books the held slot for the patient (the approval matrix's `booking` row says it may be automatic in assisted and autonomous modes).
  * `release_hold`    on a decline or a timeout, gives the slot back and moves to the next candidate.
  * `hold_for_human`  NO RUN ENDS SILENTLY: anything unclear, any booking a person must decide, any unknown status becomes a review task (required priority and role, never defaulted).

THE STRUCTURAL RULE (7.2): a node that pauses never has a side effect and touches no port. Each node has a `NodeSpec`; tests/test_scheduling_graph_structure.py checks the declarations against the node SOURCE and the
compiled topology as a hard gate, like the Front Door's. WHAT IS IN THE STATE: ids, small integers and closed words. NO reply text, NO name, NO patient detail.
"""

import os
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Final, Protocol, TypedDict

from langgraph._internal._constants import CONFIG_KEY_DURABILITY
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.autonomy import Policy
from careflow.front_door import FrontDoorError, NodeSpec, assert_tracing_off

RECURSION_LIMIT: Final = 60
SEND_ACTIONS: Final = ("send", "skip", "stop")
REPLY_OUTCOMES: Final = ("replied", "timed_out")
ANSWERS: Final = ("accept", "decline", "unclear")
STATUSES: Final = (
    "started", "ranked", "no_candidates", "drafted", "exhausted", "offer_sent", "stopped", "reply_in", "timed_out", "red_flag_escalated", "held_unassessable", "interpreted", "to_book", "declined",
    "needs_human", "booked", "released", "booking_needs_human", "reply_unclear",
)


class SchedulingState(TypedDict, total=False):
    clinic_id: str
    origin: str
    slot_id: str
    visit_type_id: str
    candidates: list[dict[str, Any]]  # waitlist id, patient id, rank: ids and a number
    index: int
    current: dict[str, Any]
    send_action: str
    hold_id: str
    message_id: str
    reply_outcome: str
    answer: str
    reason: str
    task_id: str
    status: str
    reasons: list[str]


# ---- the ports ---------------------------------------------------------------------------------------------------------------------------


class Ranker(Protocol):
    async def rank(self, conn: AsyncConnection, clinic_id: uuid.UUID, visit_type_id: uuid.UUID) -> list[dict[str, Any]]: ...


class OfferSender(Protocol):
    async def send(self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, slot_id: uuid.UUID, visit_type_id: uuid.UUID, waitlist_id: uuid.UUID, idempotency_key: str) -> dict[str, str]: ...


class ReplyScreener(Protocol):
    async def screen(self, conn: AsyncConnection, *, message_id: uuid.UUID) -> str:
        """`clear`, `red_flag_escalated` or `held_unassessable`. The rules only; no model."""
        ...


class ReplyInterpreter(Protocol):
    async def interpret(self, conn: AsyncConnection, *, message_id: uuid.UUID, clinic_id: uuid.UUID) -> dict[str, Any]:
        """{"answer": accept | decline | unclear, "reason": a closed word or None}. The decision tier; a suggestion only."""
        ...


class Booker(Protocol):
    async def book(self, conn: AsyncConnection, *, slot_id: uuid.UUID, patient_id: uuid.UUID, idempotency_key: str) -> None: ...


class HoldReleaser(Protocol):
    async def release(self, conn: AsyncConnection, *, hold_id: uuid.UUID) -> None: ...


class TaskWriter(Protocol):
    async def raise_task(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, slot_id: uuid.UUID, status: str, reasons: list[str], priority: str, assigned_role: str, idempotency_key: str
    ) -> uuid.UUID: ...


@dataclass
class SchedulingDeps:
    session: Callable[[], AbstractAsyncContextManager[AsyncConnection]]
    ranker: Ranker
    sender: OfferSender
    screener: ReplyScreener
    interpreter: ReplyInterpreter
    booker: Booker
    releaser: HoldReleaser
    tasks: TaskWriter
    policy: Policy
    max_candidates: int  # the clinic's `backfill_candidates` setting (a required setting with no default)
    task_priority: str  # required settings, never defaulted: how urgent and for whom a scheduling task is is a clinical-governance decision
    task_role: str


SIDE_EFFECT_PORTS: Final = {"sender": "offer", "screener": "escalation", "booker": "booking", "releaser": "hold_release", "tasks": "review_task"}
MODEL_PORTS: Final = {"interpreter": "model"}
NEUTRAL_PORTS: Final = {"session", "ranker", "policy", "max_candidates", "task_priority", "task_role"}

NODE_SPECS: Final = {
    "rank": NodeSpec("rank", frozenset(), pauses=False, models=False),
    "draft_offer": NodeSpec("draft_offer", frozenset(), pauses=False, models=False),
    "await_send": NodeSpec("await_send", frozenset(), pauses=True, models=False),
    "advance": NodeSpec("advance", frozenset(), pauses=False, models=False),
    "send_offer": NodeSpec("send_offer", frozenset({"offer"}), pauses=False, models=False),
    "await_reply": NodeSpec("await_reply", frozenset(), pauses=True, models=False),
    "screen_reply": NodeSpec("screen_reply", frozenset({"escalation"}), pauses=False, models=False),
    "interpret_reply": NodeSpec("interpret_reply", frozenset(), pauses=False, models=True),
    "decide": NodeSpec("decide", frozenset(), pauses=False, models=False),
    "book_slot": NodeSpec("book_slot", frozenset({"booking"}), pauses=False, models=False),
    "release_hold": NodeSpec("release_hold", frozenset({"hold_release"}), pauses=False, models=False),
    "hold_for_human": NodeSpec("hold_for_human", frozenset({"review_task"}), pauses=False, models=False),
}


def _ids(state: SchedulingState) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    return uuid.UUID(state["clinic_id"]), uuid.UUID(state["slot_id"]), uuid.UUID(state["visit_type_id"])


def thread_id_for(slot_id: uuid.UUID) -> str:
    return f"scheduling:{slot_id}"


def initial_state(clinic_id: uuid.UUID, slot_id: uuid.UUID, visit_type_id: uuid.UUID, origin: str) -> SchedulingState:
    return {"clinic_id": str(clinic_id), "slot_id": str(slot_id), "visit_type_id": str(visit_type_id), "origin": origin, "status": "started", "reasons": [], "index": 0}


def run_config(slot_id: uuid.UUID, clinic_id: uuid.UUID | None = None, origin: str | None = None) -> dict[str, Any]:
    """The config of one run. With a clinic and origin it is the config the Postgres checkpointer scopes every row by (rule 8, no origin default) and writes synchronously (a step is saved before the next
    starts, 7.2); without them it is the in-memory form used by tests."""
    configurable: dict[str, Any] = {"thread_id": thread_id_for(slot_id)}
    if clinic_id is not None and origin is not None:
        configurable.update({"clinic_id": str(clinic_id), "origin": origin, CONFIG_KEY_DURABILITY: "sync"})
    return {"configurable": configurable, "recursion_limit": RECURSION_LIMIT}


def build_scheduling_graph(deps: SchedulingDeps, checkpointer: Any, *, environ: dict[str, str] | None = None):
    """Compile the Scheduling graph. A checkpointer is required (no default); hosted tracing is refused (it would send reply text to a third party)."""
    assert_tracing_off(environ)
    if checkpointer is None:
        raise FrontDoorError("a checkpointer is required")
    for variable in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ.setdefault(variable, "false")

    async def rank(state: SchedulingState) -> SchedulingState:
        assert_tracing_off()
        clinic_id, _, visit_type_id = _ids(state)
        async with deps.session() as conn:
            ranked = await deps.ranker.rank(conn, clinic_id, visit_type_id)
        top = [{"waitlist_id": str(c["waitlist_id"]), "patient_id": str(c["patient_id"]), "rank": int(c["rank"])} for c in ranked[: max(1, deps.max_candidates)]]
        if not top:
            return {"status": "no_candidates", "candidates": [], "index": 0}
        return {"status": "ranked", "candidates": top, "index": 0}

    def draft_offer(state: SchedulingState) -> SchedulingState:
        candidates, index = state.get("candidates", []), state.get("index", 0)
        if index >= len(candidates):
            return {"status": "exhausted"}
        return {"status": "drafted", "current": candidates[index]}

    def await_send(state: SchedulingState) -> SchedulingState:
        # PAUSE. No side effect and no port: a person decides whether this candidate gets the offer. Re-executes from its first line on resume, which is harmless.
        answer = interrupt({"slot_id": state["slot_id"], "candidate": state["current"], "actions": list(SEND_ACTIONS)})
        if not isinstance(answer, dict) or answer.get("action") not in SEND_ACTIONS:
            raise FrontDoorError("a send decision needs one of the listed actions")
        return {"send_action": answer["action"]}

    def advance(state: SchedulingState) -> SchedulingState:
        return {"index": state.get("index", 0) + 1, "status": "ranked"}

    async def send_offer(state: SchedulingState) -> SchedulingState:
        assert_tracing_off()
        clinic_id, slot_id, visit_type_id = _ids(state)
        current = state["current"]
        async with deps.session() as conn:
            sent = await deps.sender.send(
                conn, clinic_id=clinic_id, origin=state["origin"], slot_id=slot_id, visit_type_id=visit_type_id, waitlist_id=uuid.UUID(current["waitlist_id"]),
                idempotency_key=f"sched:{slot_id}:{current['waitlist_id']}:offer",
            )
        return {"status": "offer_sent", "hold_id": sent["hold_id"], "message_id": sent["message_id"]}

    def await_reply(state: SchedulingState) -> SchedulingState:
        # PAUSE until the front desk enters the patient's reply (a stored staff-entered message) or reports that none came. No side effect, no port.
        answer = interrupt({"hold_id": state["hold_id"], "waiting_for": "reply", "outcomes": list(REPLY_OUTCOMES)})
        if not isinstance(answer, dict) or answer.get("outcome") not in REPLY_OUTCOMES:
            raise FrontDoorError("a reply entry needs one of the listed outcomes")
        if answer["outcome"] == "replied":
            try:
                uuid.UUID(str(answer.get("message_id")))
            except ValueError:
                raise FrontDoorError("a reply needs the id of the stored message") from None
            return {"reply_outcome": "replied", "message_id": str(answer["message_id"]), "status": "reply_in"}
        return {"reply_outcome": "timed_out", "status": "timed_out"}

    async def screen_reply(state: SchedulingState) -> SchedulingState:
        assert_tracing_off()
        # Rules first, before ANY model, on every path (CLAUDE.md rule 2). A red flag or an unassessable reply ends the run: the nurse task is the screener's.
        async with deps.session() as conn:
            outcome = await deps.screener.screen(conn, message_id=uuid.UUID(state["message_id"]))
        if outcome == "clear":
            return {"status": "reply_in"}
        if outcome == "red_flag_escalated":
            return {"status": "red_flag_escalated"}
        return {"status": "held_unassessable", "reasons": ["reply_unassessable"]}

    async def interpret_reply(state: SchedulingState) -> SchedulingState:
        assert_tracing_off()
        clinic_id, _, _ = _ids(state)
        async with deps.session() as conn:
            result = await deps.interpreter.interpret(conn, message_id=uuid.UUID(state["message_id"]), clinic_id=clinic_id)
        answer = result.get("answer") if isinstance(result, dict) else None
        if answer not in ANSWERS:
            return {"status": "reply_unclear", "answer": "unclear", "reasons": ["no_suggestion"]}  # unknown is not a value: anything else is unclear
        reason = result.get("reason")
        return {"status": "interpreted", "answer": answer, "reason": reason if isinstance(reason, str) and len(reason) <= 40 else "none"}

    def decide(state: SchedulingState) -> SchedulingState:
        answer = state.get("answer")
        if answer == "accept":
            return {"status": "to_book"} if deps.policy.may_run_without_a_human_first("booking") else {"status": "booking_needs_human", "reasons": ["human_decides"]}
        if answer == "decline":
            return {"status": "declined"}
        return {"status": "needs_human", "reasons": [state.get("reason") or "no_suggestion"]}

    async def book_slot(state: SchedulingState) -> SchedulingState:
        assert_tracing_off()
        _, slot_id, _ = _ids(state)
        async with deps.session() as conn:
            await deps.booker.book(conn, slot_id=slot_id, patient_id=uuid.UUID(state["current"]["patient_id"]), idempotency_key=f"sched:{slot_id}:{state['current']['waitlist_id']}:book")
        return {"status": "booked"}

    async def release_hold(state: SchedulingState) -> SchedulingState:
        assert_tracing_off()
        async with deps.session() as conn:
            await deps.releaser.release(conn, hold_id=uuid.UUID(state["hold_id"]))
        return {"status": "released"}

    async def hold_for_human(state: SchedulingState) -> SchedulingState:
        assert_tracing_off()
        clinic_id, slot_id, _ = _ids(state)
        status = state.get("status", "")
        async with deps.session() as conn:
            task_id = await deps.tasks.raise_task(
                conn, clinic_id=clinic_id, origin=state["origin"], slot_id=slot_id, status=status, reasons=[r for r in state.get("reasons", []) if isinstance(r, str)][:20],
                priority=deps.task_priority, assigned_role=deps.task_role, idempotency_key=f"sched-hold:{slot_id}:{status}",
            )
        return {"task_id": str(task_id)}

    def after_rank(state: SchedulingState) -> str:
        return "draft_offer" if state.get("status") == "ranked" else END if state.get("status") == "no_candidates" else "hold_for_human"

    def after_draft(state: SchedulingState) -> str:
        status = state.get("status")
        return "await_send" if status == "drafted" else END if status == "exhausted" else "hold_for_human"

    def after_await_send(state: SchedulingState) -> str:
        action = state.get("send_action")
        return "send_offer" if action == "send" else "advance" if action == "skip" else END if action == "stop" else "hold_for_human"

    def after_await_reply(state: SchedulingState) -> str:
        status = state.get("status")
        return "screen_reply" if status == "reply_in" else "release_hold" if status == "timed_out" else "hold_for_human"

    def after_screen(state: SchedulingState) -> str:
        status = state.get("status")
        return "interpret_reply" if status == "reply_in" else END if status == "red_flag_escalated" else "hold_for_human"

    def after_interpret(state: SchedulingState) -> str:
        return "decide" if state.get("status") in {"interpreted", "reply_unclear"} else "hold_for_human"

    def after_decide(state: SchedulingState) -> str:
        status = state.get("status")
        return "book_slot" if status == "to_book" else "release_hold" if status == "declined" else "hold_for_human"

    def after_release(state: SchedulingState) -> str:
        return "advance" if state.get("status") == "released" else "hold_for_human"

    functions = {
        "rank": rank, "draft_offer": draft_offer, "await_send": await_send, "advance": advance, "send_offer": send_offer, "await_reply": await_reply, "screen_reply": screen_reply,
        "interpret_reply": interpret_reply, "decide": decide, "book_slot": book_slot, "release_hold": release_hold, "hold_for_human": hold_for_human,
    }
    builder = StateGraph(SchedulingState)
    for name, fn in functions.items():
        builder.add_node(name, fn)
    builder.add_edge(START, "rank")
    builder.add_conditional_edges("rank", after_rank, {"draft_offer": "draft_offer", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("draft_offer", after_draft, {"await_send": "await_send", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("await_send", after_await_send, {"send_offer": "send_offer", "advance": "advance", "hold_for_human": "hold_for_human", END: END})
    builder.add_edge("advance", "draft_offer")
    builder.add_edge("send_offer", "await_reply")
    builder.add_conditional_edges("await_reply", after_await_reply, {"screen_reply": "screen_reply", "release_hold": "release_hold", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("screen_reply", after_screen, {"interpret_reply": "interpret_reply", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("interpret_reply", after_interpret, {"decide": "decide", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("decide", after_decide, {"book_slot": "book_slot", "release_hold": "release_hold", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("release_hold", after_release, {"advance": "advance", "hold_for_human": "hold_for_human"})
    builder.add_edge("book_slot", END)
    builder.add_edge("hold_for_human", END)
    graph = builder.compile(checkpointer=checkpointer)
    graph.node_functions = functions  # type: ignore[attr-defined]  # for the structural test
    graph.router_functions = {  # type: ignore[attr-defined]
        "after_rank": after_rank, "after_draft": after_draft, "after_await_send": after_await_send, "after_await_reply": after_await_reply, "after_screen": after_screen,
        "after_interpret": after_interpret, "after_decide": after_decide, "after_release": after_release,
    }
    return graph
