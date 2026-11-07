"""Driving the Scheduling graph from the staff routes (careflow/scheduling_graph.py; docs/design/phase3-scheduling-agent.md). MECHANISM ONLY: the runner wraps an already-compiled graph and the routes call it; with no
runner configured the routes answer a visible 503 (nothing is lost, nothing runs). It holds no state of its own: everything is in the graph's checkpoints, and a view returns ids, small numbers and closed words.

A run is one thread per freed slot (`scheduling:<slot id>`). `start` runs to the first pause (the person presses send); `decide` and `reply` resume the thread with the person's answer; `view` reads where it is.
Resuming a thread that is not at the pause the answer is for is refused (`WrongStep`), so a stale or replayed press can neither skip a step nor repeat one.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Final, Literal

from langgraph.types import Command

from careflow.autonomy import Policy
from careflow.checkpointer import PostgresCheckpointSaver, SqlCheckpointStore
from careflow.scheduling_graph import (
    REPLY_OUTCOMES,
    SEND_ACTIONS,
    SchedulingDeps,
    build_scheduling_graph,
    initial_state,
    run_config,
)

PAUSES: Final = ("await_send", "await_reply")
Waiting = Literal["send_decision", "reply_entry", "none"]


class WrongStep(Exception):
    """The thread is not waiting for that kind of answer. Fixed words."""


class NoSuchRun(Exception):
    """No run exists for this slot. Fixed words."""


@dataclass(frozen=True)
class RunView:
    slot_id: uuid.UUID
    status: str  # a closed word from scheduling_graph.STATUSES
    waiting_for: Waiting
    candidate_patient_id: uuid.UUID | None  # the candidate currently being offered
    candidate_rank: int | None
    hold_id: uuid.UUID | None
    task_id: uuid.UUID | None  # the review task a person must handle, if the run ended in one
    finished: bool


def _view(slot_id: uuid.UUID, snapshot: Any) -> RunView:
    values = snapshot.values or {}
    pending = tuple(snapshot.next)
    waiting: Waiting = "send_decision" if pending == ("await_send",) else "reply_entry" if pending == ("await_reply",) else "none"
    current = values.get("current") or {}

    def maybe(value: object) -> uuid.UUID | None:
        try:
            return uuid.UUID(str(value)) if value else None
        except ValueError:
            return None

    return RunView(
        slot_id=slot_id, status=str(values.get("status", "started")), waiting_for=waiting, candidate_patient_id=maybe(current.get("patient_id")), candidate_rank=current.get("rank"),
        hold_id=maybe(values.get("hold_id")), task_id=maybe(values.get("task_id")), finished=not pending and bool(values),
    )


@dataclass
class SchedulingComponents:
    """Everything the Scheduling graph needs from outside, as ports (all fakes in this package), plus the clinic's governance values. The graph is built PER REQUEST around the request's own transaction, so every
    effect (the hold, the offer message, the booking) is done by the signed-in front-desk person under row-level security: there is no worker identity in Phase 3."""

    ranker: Any
    sender: Any
    screener: Any
    interpreter: Any
    booker: Any
    releaser: Any
    tasks: Any
    policy: Policy
    max_candidates: int
    task_priority: str
    task_role: str
    checkpointer: Any
    durable: bool = False  # True: the runs are saved in our own Postgres checkpoint tables (migrations 0021, 0037), scoped to the clinic; False: `checkpointer` (memory, tests)

    def runner(self, session: Any, *, clinic_id: uuid.UUID | None = None, origin: str | None = None) -> "SchedulingRunner":
        deps = SchedulingDeps(
            session=session, ranker=self.ranker, sender=self.sender, screener=self.screener, interpreter=self.interpreter, booker=self.booker, releaser=self.releaser, tasks=self.tasks,
            policy=self.policy, max_candidates=self.max_candidates, task_priority=self.task_priority, task_role=self.task_role,
        )
        checkpointer = self.checkpointer
        if self.durable:
            if clinic_id is None or origin is None:
                raise ValueError("a durable run needs its clinic and origin")
            checkpointer = PostgresCheckpointSaver(SqlCheckpointStore(session))
        return SchedulingRunner(build_scheduling_graph(deps, checkpointer), clinic_id=clinic_id if self.durable else None, origin=origin if self.durable else None)


class SchedulingRunner:
    def __init__(self, graph: Any, *, clinic_id: uuid.UUID | None = None, origin: str | None = None) -> None:
        self._graph = graph
        self._clinic_id, self._origin = clinic_id, origin

    def _config(self, slot_id: uuid.UUID) -> dict[str, Any]:
        return run_config(slot_id, self._clinic_id, self._origin)

    async def start(self, *, clinic_id: uuid.UUID, slot_id: uuid.UUID, visit_type_id: uuid.UUID, origin: str) -> RunView:
        config = self._config(slot_id)
        existing = await self._graph.aget_state(config)
        if existing.values:  # a second start for the same freed slot is the same run, not a second one
            return _view(slot_id, existing)
        await self._graph.ainvoke(initial_state(clinic_id, slot_id, visit_type_id, origin), config)
        return _view(slot_id, await self._graph.aget_state(config))

    async def view(self, slot_id: uuid.UUID) -> RunView:
        snapshot = await self._graph.aget_state(self._config(slot_id))
        if not snapshot.values:
            raise NoSuchRun
        return _view(slot_id, snapshot)

    async def decide(self, slot_id: uuid.UUID, action: str) -> RunView:
        if action not in SEND_ACTIONS:
            raise WrongStep
        return await self._resume(slot_id, "await_send", {"action": action})

    async def reply(self, slot_id: uuid.UUID, outcome: str, message_id: uuid.UUID | None) -> RunView:
        if outcome not in REPLY_OUTCOMES or (outcome == "replied") != (message_id is not None):
            raise WrongStep
        return await self._resume(slot_id, "await_reply", {"outcome": outcome, **({"message_id": str(message_id)} if message_id else {})})

    async def _resume(self, slot_id: uuid.UUID, expected_pause: str, answer: dict[str, str]) -> RunView:
        config = self._config(slot_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise NoSuchRun
        if tuple(snapshot.next) != (expected_pause,):
            raise WrongStep
        await self._graph.ainvoke(Command(resume=answer), config)
        return _view(slot_id, await self._graph.aget_state(config))
