"""The Visit Readiness graph over the database (migration 0052) and the runner the routes drive. Everything runs on the CALLER's own transaction (a nurse or doctor, with a verified second factor): there is no worker
identity. The MODEL roles (intent, grounded fact extraction, readiness answer, barrier class, brief narrative) are PORTS with FAKES here: with no approved route `NoModelRoute` answers "unknown" for all of them,
which the graph reads as unclear / not grounded / unclassified, so every reply is re-asked once and then goes to front desk. Nothing is guessed.

NO QUESTION IS WRITTEN IN THIS FILE. A question or a checklist item is sent as the clinician's EXACT text from the required setting (careflow.visit_readiness_content) as a staff-recorded message in the patient's
`readiness` thread (nurse and doctor only; delivery to the patient is by hand, as for offers). The rules read every reply first (screen_stored_message); a red flag raises its own immediate nurse task.
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from langgraph.types import Command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.autonomy import Policy
from careflow.care_gap_sql import record_barrier, sqlstate
from careflow.checkpointer import PostgresCheckpointSaver, SqlCheckpointStore
from careflow.crypto import DemographicVault
from careflow.front_door import FrontDoorError
from careflow.front_door_runner import request_session
from careflow.grounding import FactTier, GroundedFact, Grounding
from careflow.messages import encrypt_body, screen_stored_message
from careflow.rules import RedFlagEngine
from careflow.visit_readiness import (
    BriefLine,
    VisitReadinessDeps,
    build_visit_readiness_graph,
    fact_gap,
    initial_state,
    run_config,
)
from careflow.visit_readiness_content import VisitContent

TRIGGER: Final = "intake"
TASK_PRIORITIES: Final = ("immediate", "standard")


class Unavailable(Exception):
    """The graph is not built (its feature flag is off). Fixed words."""


class NoContent(Exception):
    """The visit's type has no entry in the content setting, or the visit is not a confirmed booking. Fixed words."""


class WrongStep(Exception):
    """The run is not waiting for a reply. Fixed words."""


class NoSuchRun(Exception):
    """No run exists for this visit. Fixed words."""


@dataclass(frozen=True)
class Visit:
    visit_id: uuid.UUID
    clinic_id: uuid.UUID
    origin: str
    patient_id: uuid.UUID
    visit_type: str


async def load_visit(conn: AsyncConnection, visit_id: uuid.UUID, *, confirmed_only: bool = True) -> Visit | None:
    row = (
        await conn.execute(
            text(
                "select b.id, b.clinic_id, b.origin, b.patient_id, vt.name as visit_type from sched.bookings b "
                "join sched.slots s on s.id = b.slot_id and s.clinic_id = b.clinic_id join sched.visit_types vt on vt.id = s.visit_type_id and vt.clinic_id = s.clinic_id "
                "where b.id = :v" + (" and b.status = 'confirmed'" if confirmed_only else "")
            ),
            {"v": visit_id},
        )
    ).first()
    return Visit(row.id, row.clinic_id, row.origin, row.patient_id, row.visit_type) if row else None


async def _insert_or_ignore(conn: AsyncConnection, sql: str, params: dict) -> bool:
    """True when written; False for a REPLAY (the idempotency key already exists). Any other unique violation (a different constraint) is a real conflict and is raised, never swallowed as a replay."""
    try:
        async with conn.begin_nested():
            await conn.execute(text(sql), params)
        return True
    except IntegrityError as exc:
        if sqlstate(exc) == "23505" and "idempotency_key" in str(exc):  # the DETAIL line names the columns of the violated key
            return False
        raise


class NoModelRoute:
    """The five model roles with NO approved route: every answer is 'unknown', which the graph treats as unclear, not grounded, unclassified. Tests inject scripted fakes instead."""

    async def classify(self, conn, **kw):  # intents, readiness answers and barriers share the shape; unknown is not a value
        return "unknown"

    async def resolve(self, conn, **kw):
        return False

    async def generate(self, conn, **kw):
        return []


class SqlGapPlanner:
    def __init__(self, content: VisitContent) -> None:
        self._content = content

    async def plan(self, conn: AsyncConnection, *, visit_id: uuid.UUID, clinic_id: uuid.UUID) -> dict[str, list[str]]:
        visit = await load_visit(conn, visit_id)
        entry = self._content.for_visit_type(visit.visit_type) if visit else None
        if visit is None or entry is None:
            raise NoContent
        rows = (await conn.execute(text("select fact from ops.visit_facts where visit_id = :v"), {"v": visit_id})).all()
        known = [GroundedFact(r.fact, FactTier.STRUCTURED, Grounding.VERIFIED, "recorded_from_a_reply") for r in rows]
        to_ask, unaskable = fact_gap(entry.card, known)
        return {"intake": to_ask, "unaskable": unaskable, "readiness": [i.item_id for i in entry.checklist.items]}


class SqlQuestionSender:
    """Sends the clinician's EXACT text for an item as a staff-recorded message in the patient's readiness thread."""

    def __init__(self, content: VisitContent, vault: DemographicVault, key_version: int, actor: uuid.UUID) -> None:
        self._content, self._vault, self._kv, self._actor = content, vault, key_version, actor

    async def ask(self, conn: AsyncConnection, *, visit_id: uuid.UUID, phase: str, item: str, idempotency_key: str) -> uuid.UUID:
        visit = await load_visit(conn, visit_id)
        entry = self._content.for_visit_type(visit.visit_type) if visit else None
        if visit is None or entry is None:
            raise NoContent
        wording = entry.card.intake_questions.get(item) if phase == "intake" else entry.item_text.get(item)
        if not wording:
            raise NoContent  # no wording, nothing is sent: the code never words a question
        thread_id = await thread_for(conn, visit, self._actor)
        message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-readiness-question:{idempotency_key}")
        body = encrypt_body(self._vault, wording, clinic_id=visit.clinic_id, thread_id=thread_id, key_version=self._kv, message_id=message_id)
        await _insert_or_ignore(
            conn,
            "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
            "values (:i, :c, :o, 'readiness', :t, 'staff', :u, :b, :v, :k)",
            {"i": body.message_id, "c": visit.clinic_id, "o": visit.origin, "t": thread_id, "u": self._actor, "b": body.body_ct, "v": body.body_key_version, "k": f"readiness-question:{idempotency_key}"},
        )
        return body.message_id


async def thread_for(conn: AsyncConnection, visit: Visit, actor: uuid.UUID) -> uuid.UUID:
    found = (await conn.execute(text("select id from msg.threads where patient_id = :p and topic = 'readiness' and clinic_id = :c"), {"p": visit.patient_id, "c": visit.clinic_id})).first()
    if found is not None:
        return found[0]
    await _insert_or_ignore(
        conn,
        "insert into msg.threads (clinic_id, patient_id, topic, created_by, origin, idempotency_key) values (:c, :p, 'readiness', :u, :o, :k)",
        {"c": visit.clinic_id, "p": visit.patient_id, "u": actor, "o": visit.origin, "k": f"readiness-thread:{visit.patient_id}"},
    )
    return (await conn.execute(text("select id from msg.threads where patient_id = :p and topic = 'readiness' and clinic_id = :c"), {"p": visit.patient_id, "c": visit.clinic_id})).scalar_one()


async def store_reply(conn: AsyncConnection, vault: DemographicVault, key_version: int, visit: Visit, actor: uuid.UUID, body_text: str, idempotency_key: str) -> uuid.UUID:
    """The patient's reply, RELAYED by the nurse or doctor who received it, as a staff-recorded message in the readiness thread (encrypted at rest). Returns the stored message id."""
    thread_id = await thread_for(conn, visit, actor)
    message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-readiness-reply:{idempotency_key}")
    body = encrypt_body(vault, body_text, clinic_id=visit.clinic_id, thread_id=thread_id, key_version=key_version, message_id=message_id)
    await _insert_or_ignore(
        conn,
        "insert into msg.messages (id, clinic_id, origin, topic, thread_id, sender_kind, recorded_by, body_ct, body_key_version, idempotency_key) "
        "values (:i, :c, :o, 'readiness', :t, 'staff', :u, :b, :v, :k)",
        {"i": body.message_id, "c": visit.clinic_id, "o": visit.origin, "t": thread_id, "u": actor, "b": body.body_ct, "v": body.body_key_version, "k": f"readiness-reply:{idempotency_key}"},
    )
    return body.message_id


class SqlReplyScreener:
    def __init__(self, vault: DemographicVault, engine: RedFlagEngine) -> None:
        self._vault, self._engine = vault, engine

    async def screen(self, conn: AsyncConnection, *, message_id: uuid.UUID) -> str:
        try:
            done = await screen_stored_message(conn, self._vault, self._engine, message_id, idempotency_key=f"vr-screen:{message_id}")
        except Exception:  # noqa: BLE001 - a reply that cannot be screened is never treated as clear
            return "held_unassessable"
        return {"clear": "clear", "red_flag": "red_flag_escalated"}.get(done.outcome, "held_unassessable")


class SqlFactWriter:
    def __init__(self, actor: uuid.UUID) -> None:
        self._actor = actor

    async def record(self, conn: AsyncConnection, *, visit_id: uuid.UUID, fact: str, message_id: uuid.UUID, idempotency_key: str) -> None:
        visit = await load_visit(conn, visit_id)
        if visit is None:
            raise NoContent
        thread_id = (await conn.execute(text("select thread_id from msg.messages where id = :m and clinic_id = :c"), {"m": message_id, "c": visit.clinic_id})).scalar_one()
        await _insert_or_ignore(
            conn,
            "insert into ops.visit_facts (clinic_id, origin, visit_id, patient_id, fact, thread_id, source_message_id, recorded_by, idempotency_key) values (:c, :o, :v, :p, :f, :t, :m, :u, :k)",
            {"c": visit.clinic_id, "o": visit.origin, "v": visit_id, "p": visit.patient_id, "f": fact, "t": thread_id, "m": message_id, "u": self._actor, "k": idempotency_key},
        )


class SqlReadinessRecorder:
    def __init__(self, actor: uuid.UUID) -> None:
        self._actor = actor

    async def record(self, conn: AsyncConnection, *, visit_id: uuid.UUID, item: str, answer: str, message_id: uuid.UUID, idempotency_key: str) -> None:
        visit = await load_visit(conn, visit_id)
        if visit is None:
            raise NoContent
        thread_id = (await conn.execute(text("select thread_id from msg.messages where id = :m and clinic_id = :c"), {"m": message_id, "c": visit.clinic_id})).scalar_one()
        await _insert_or_ignore(
            conn,
            "insert into ops.visit_readiness_answers (clinic_id, origin, visit_id, patient_id, item, answer, thread_id, source_message_id, recorded_by, idempotency_key) values (:c, :o, :v, :p, :i, :a, :t, :m, :u, :k)",
            {"c": visit.clinic_id, "o": visit.origin, "v": visit_id, "p": visit.patient_id, "i": item, "a": answer, "t": thread_id, "m": message_id, "u": self._actor, "k": idempotency_key},
        )


class SqlBarrierRecorder:
    async def record(self, conn: AsyncConnection, *, visit_id: uuid.UUID, barrier: str, idempotency_key: str) -> None:
        visit = await load_visit(conn, visit_id)
        if visit is None:
            raise NoContent
        await record_barrier(conn, clinic_id=visit.clinic_id, origin=visit.origin, patient_id=visit.patient_id, barrier=barrier, idempotency_key=idempotency_key)


class SqlPeopleTasks:
    def __init__(self, priority: str) -> None:
        self._priority = priority

    async def raise_task(self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, visit_id: uuid.UUID, reason: str, reasons: list[str], role: str, idempotency_key: str) -> uuid.UUID:
        params = {
            "c": clinic_id, "o": origin, "k": f"visit_readiness_{reason}", "p": self._priority, "r": role, "s": f"visit:{visit_id}", "v": json.dumps({"visit_id": str(visit_id), "reasons": reasons}), "i": idempotency_key,
        }
        await _insert_or_ignore(
            conn,
            "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) values (:c, :o, :k, :p, :r, :s, cast(:v as jsonb), :i)",
            params,
        )
        return (await conn.execute(text("select id from agents.review_tasks where idempotency_key = :i and clinic_id = :c"), {"i": idempotency_key, "c": clinic_id})).scalar_one()


class TaskRebooker:
    """Hands a scheduling-conflict barrier to FRONT DESK as a task that names the visit (this agent books nothing itself; the Scheduling agent's flow is driven by a person)."""

    def __init__(self, tasks: SqlPeopleTasks) -> None:
        self._tasks = tasks

    async def hand_off(self, conn: AsyncConnection, *, visit_id: uuid.UUID, needs_confirmation: bool, idempotency_key: str) -> None:
        visit = await load_visit(conn, visit_id, confirmed_only=False)
        if visit is None:
            raise NoContent
        await self._tasks.raise_task(
            conn, clinic_id=visit.clinic_id, origin=visit.origin, visit_id=visit_id, reason="rebook", reasons=["needs_confirmation" if needs_confirmation else "barrier_rebook"], role="front_desk", idempotency_key=idempotency_key
        )


class SqlBriefMaker:
    """A structured brief for the CLINICIAN: field and status only (no value), from the facts recorded for the visit. The narrative model role has no approved route, so nothing is generated beyond that."""

    def __init__(self, actor: uuid.UUID) -> None:
        self._actor = actor

    async def generate(self, conn: AsyncConnection, *, visit_id: uuid.UUID, clinic_id: uuid.UUID) -> list[GroundedFact]:
        rows = (await conn.execute(text("select fact from ops.visit_facts where visit_id = :v order by fact"), {"v": visit_id})).all()
        return [GroundedFact(r.fact, FactTier.STRUCTURED, Grounding.VERIFIED, "recorded_from_a_reply") for r in rows]

    async def store(self, conn: AsyncConnection, *, visit_id: uuid.UUID, lines: list[BriefLine], idempotency_key: str) -> None:
        visit = await load_visit(conn, visit_id, confirmed_only=False)
        if visit is None:
            raise NoContent
        await _insert_or_ignore(
            conn,
            "insert into ops.visit_briefs (clinic_id, origin, visit_id, patient_id, lines, created_by, idempotency_key) values (:c, :o, :v, :p, cast(:l as jsonb), :u, :k)",
            {"c": visit.clinic_id, "o": visit.origin, "v": visit_id, "p": visit.patient_id, "l": json.dumps([{"field": ln.field_name, "status": ln.status} for ln in lines]), "u": self._actor, "k": idempotency_key},
        )


@dataclass(frozen=True)
class RunView:
    visit_id: uuid.UUID
    status: str
    waiting_for: Literal["reply", "none"]
    current_item: str | None
    task_id: uuid.UUID | None
    finished: bool


def _view(visit_id: uuid.UUID, snapshot: Any) -> RunView:
    values = snapshot.values or {}
    pending = tuple(snapshot.next)
    try:
        task = uuid.UUID(str(values["task_id"])) if values.get("task_id") else None
    except ValueError:
        task = None
    waiting: Literal["reply", "none"] = "reply" if pending == ("await_reply",) else "none"
    return RunView(visit_id, str(values.get("status", "started")), waiting, values.get("current_item") if waiting == "reply" else None, task, not pending and bool(values))


@dataclass
class VisitReadinessComponents:
    vault: DemographicVault
    engine: RedFlagEngine
    content: VisitContent
    task_priority: str  # a required setting, never defaulted
    checkpointer: Any
    durable: bool = False
    models: Any = None  # the five model roles; None = NoModelRoute (all unknown)
    screener: Any = None  # a stand-in for tests; None = the rules over the stored reply
    policy: Policy = field(default_factory=Policy)

    def __post_init__(self) -> None:
        if self.task_priority not in TASK_PRIORITIES:
            raise ValueError("the task priority must be set to an allowed value")

    def runner(self, conn: AsyncConnection, *, visit: Visit, actor: uuid.UUID, key_version: int, policy: Policy | None = None) -> "VisitReadinessRunner":
        session = request_session(conn)
        models = self.models or NoModelRoute()
        tasks = SqlPeopleTasks(self.task_priority)
        deps = VisitReadinessDeps(
            session, SqlGapPlanner(self.content), SqlQuestionSender(self.content, self.vault, key_version, actor), self.screener or SqlReplyScreener(self.vault, self.engine), models, models,
            SqlFactWriter(actor), models, SqlReadinessRecorder(actor), models, SqlBarrierRecorder(), TaskRebooker(tasks), _BriefAdapter(models, SqlBriefMaker(actor)), tasks, policy or self.policy,
        )  # type: ignore[arg-type]
        checkpointer = PostgresCheckpointSaver(SqlCheckpointStore(session)) if self.durable else self.checkpointer
        try:
            graph = build_visit_readiness_graph(deps, checkpointer)
        except FrontDoorError:
            raise Unavailable from None
        return VisitReadinessRunner(graph, visit=visit, clinic_id=visit.clinic_id if self.durable else None, origin=visit.origin if self.durable else None)


class _BriefAdapter:
    """The brief port: facts from the database (and, when a model route is approved, its narrative); storage in the brief table."""

    def __init__(self, models: Any, maker: SqlBriefMaker) -> None:
        self._models, self._maker = models, maker

    async def generate(self, conn, *, visit_id, clinic_id):
        return [*await self._maker.generate(conn, visit_id=visit_id, clinic_id=clinic_id), *await self._models.generate(conn, visit_id=visit_id, clinic_id=clinic_id)]

    async def store(self, conn, *, visit_id, lines, idempotency_key):
        await self._maker.store(conn, visit_id=visit_id, lines=lines, idempotency_key=idempotency_key)


class VisitReadinessRunner:
    def __init__(self, graph: Any, *, visit: Visit, clinic_id: uuid.UUID | None, origin: str | None) -> None:
        self._graph, self._visit, self._clinic, self._origin = graph, visit, clinic_id, origin

    def _config(self) -> dict[str, Any]:
        return run_config(self._visit.visit_id, TRIGGER, self._clinic, self._origin)

    async def start(self) -> RunView:
        config = self._config()
        existing = await self._graph.aget_state(config)
        if existing.values:
            return _view(self._visit.visit_id, existing)
        v = self._visit
        await self._graph.ainvoke(initial_state(v.clinic_id, v.visit_id, v.patient_id, v.origin, TRIGGER), config)
        return _view(v.visit_id, await self._graph.aget_state(config))

    async def view(self) -> RunView:
        snapshot = await self._graph.aget_state(self._config())
        if not snapshot.values:
            raise NoSuchRun
        return _view(self._visit.visit_id, snapshot)

    async def reply(self, message_id: uuid.UUID | None) -> RunView:
        config = self._config()
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise NoSuchRun
        if tuple(snapshot.next) != ("await_reply",):
            raise WrongStep
        answer = {"outcome": "replied", "message_id": str(message_id)} if message_id else {"outcome": "no_reply"}
        await self._graph.ainvoke(Command(resume=answer), config)
        return _view(self._visit.visit_id, await self._graph.aget_state(config))
