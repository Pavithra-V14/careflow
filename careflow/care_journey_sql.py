"""The Care Journey graph's ports over the database (migration 0050), plus the runner the routes drive. Everything runs on the CALLER's own transaction under row-level security: the signed-in doctor or nurse does the
writes, so there is no worker identity here (as for the Scheduling runs). The MODEL is a port too: `PlanModel` is whatever approved model route turns plan text into a proposal; none is approved, so the default is
None and the graph then holds the plan for a doctor ("needs a person"), never a guess. The rules screening runs first on the decrypted plan text, with no model (rule 2); a red flag raises its own immediate nurse task.

A plan text is encrypted at rest (careflow.messages.encrypt_plan_text) and decrypted only here, for the rules and for the model port; it is never logged.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Literal

from langgraph.types import Command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate
from careflow.care_journey import (
    CareJourneyDeps,
    Condition,
    Result,
    Step,
    Wait,
    build_care_journey_graph,
    initial_state,
    run_config,
)
from careflow.checkpointer import PostgresCheckpointSaver, SqlCheckpointStore
from careflow.crypto import DecryptionError, DemographicVault, UnknownKeyVersionError
from careflow.front_door import FrontDoorError
from careflow.front_door_runner import RunnerError, request_session, worker_session
from careflow.messages import decrypt_plan_text
from careflow.rules import RedFlagEngine
from careflow.tick import Handler, JobContext

PlanModel = Callable[[str], Awaitable[object]]  # plan text in, a raw proposal out (validated by code afterwards): the approved model route, when there is one
PAUSES: Final = ("await_confirmation", "await_result")
TASK_PRIORITIES: Final = ("immediate", "standard")
TASK_ROLES: Final = ("nurse", "doctor")


class WrongStep(Exception):
    """The plan is not waiting for that kind of answer. Fixed words."""


class Unavailable(Exception):
    """The Care Journey graph is not built (its feature flag is off). Fixed words."""


class NoSuchPlan(Exception):
    """No run exists for this plan. Fixed words."""


async def _insert_or_existing(conn: AsyncConnection, sql: str, params: dict, existing_sql: str, existing_params: dict) -> uuid.UUID:
    try:
        async with conn.begin_nested():
            return (await conn.execute(text(sql + " returning id"), params)).one().id
    except IntegrityError as exc:
        if sqlstate(exc) != "23505":
            raise
    row = (await conn.execute(text(existing_sql), existing_params)).first()
    if row is None:
        raise RuntimeError("the key is held by a row this caller cannot see")
    return row.id


async def _clinic_of(conn: AsyncConnection, plan_id: uuid.UUID) -> tuple[uuid.UUID, str, uuid.UUID]:
    row = (await conn.execute(text("select clinic_id, origin, patient_id from ops.care_plans where id = :p"), {"p": plan_id})).one()
    return row.clinic_id, row.origin, row.patient_id


class SqlPlanScreener:
    def __init__(self, vault: DemographicVault, engine: RedFlagEngine) -> None:
        self._vault, self._engine = vault, engine

    async def screen(self, conn: AsyncConnection, *, plan_id: uuid.UUID, clinic_id: uuid.UUID) -> str:
        row = (await conn.execute(text("select plan_ct, plan_key_version, origin from ops.care_plans where id = :p and clinic_id = :c"), {"p": plan_id, "c": clinic_id})).first()
        if row is None or row.plan_ct is None:
            return "held_unassessable"  # no such plan, or its text was erased: unknown is not clear
        try:
            plain = decrypt_plan_text(self._vault, row.plan_ct, clinic_id=clinic_id, plan_id=plan_id, key_version=row.plan_key_version)
        except (UnknownKeyVersionError, DecryptionError):
            return "held_unassessable"  # unknown is not clear
        evaluation = await self._engine.evaluate_text(conn, clinic_id, row.origin, plain)
        del plain
        if evaluation.outcome == "red_flag":
            await self._engine.escalate_red_flag(conn, clinic_id, row.origin, evaluation, f"plan:{plan_id}")
            return "red_flag_escalated"
        return "clear" if evaluation.outcome == "clear" else "held_unassessable"


class SqlPlanParser:
    def __init__(self, vault: DemographicVault, model: PlanModel | None) -> None:
        self._vault, self._model = vault, model

    async def parse(self, conn: AsyncConnection, *, plan_id: uuid.UUID, clinic_id: uuid.UUID) -> object:
        if self._model is None:
            return None  # no approved model route: the graph holds the plan for a doctor
        row = (await conn.execute(text("select plan_ct, plan_key_version from ops.care_plans where id = :p and clinic_id = :c"), {"p": plan_id, "c": clinic_id})).one()
        if row.plan_ct is None:
            return None  # erased
        plain = decrypt_plan_text(self._vault, row.plan_ct, clinic_id=clinic_id, plan_id=plan_id, key_version=row.plan_key_version)
        try:
            return await self._model(plain)
        finally:
            del plain


class SqlPlanProposals:
    async def save(self, conn: AsyncConnection, *, plan_id: uuid.UUID, steps: list[Step], idempotency_key: str) -> None:
        clinic_id, _, _ = await _clinic_of(conn, plan_id)
        for position, s in enumerate(steps):
            c = s.condition
            await _insert_or_existing(
                conn,
                "insert into ops.care_plan_steps (clinic_id, plan_id, step_key, position, kind, label, result_prefix, cond_field, cond_op, cond_threshold, on_met, idempotency_key) "
                "values (:c, :p, :k, :pos, :kind, :l, :rp, :cf, :co, :ct, :om, :ik)",
                {"c": clinic_id, "p": plan_id, "k": s.key, "pos": position, "kind": s.kind, "l": s.label, "rp": s.result_prefix, "cf": c.field if c else None, "co": c.op if c else None,
                 "ct": c.threshold if c else None, "om": s.on_met, "ik": f"{idempotency_key}:{s.key}"},
                "select id from ops.care_plan_steps where plan_id = :p and step_key = :k", {"p": plan_id, "k": s.key},
            )
        await conn.execute(text("update ops.care_plans set status = 'awaiting_confirmation' where id = :p and status = 'screening'"), {"p": plan_id})


class SqlPlanActivator:
    async def activate(self, conn: AsyncConnection, *, plan_id: uuid.UUID, patient_id: uuid.UUID, idempotency_key: str) -> None:
        clinic_id, _, _ = await _clinic_of(conn, plan_id)
        await conn.execute(text("update ops.care_plans set status = 'active' where id = :p and status in ('awaiting_confirmation', 'held')"), {"p": plan_id})
        if (await conn.execute(text("select status from ops.care_plans where id = :p"), {"p": plan_id})).scalar() == "erased":
            raise Refused("the care plan was erased")  # no waits are opened for an erased plan
        for r in (await conn.execute(text("select step_key, result_prefix from ops.care_plan_steps where plan_id = :p and kind = 'wait_result' order by position"), {"p": plan_id})).all():
            await _insert_or_existing(
                conn,
                "insert into ops.care_plan_waits (clinic_id, plan_id, step_key, wait_key, patient_id, prefix, idempotency_key) values (:c, :p, :s, :w, :pt, :pf, :k)",
                {"c": clinic_id, "p": plan_id, "s": r.step_key, "w": f"wait:{plan_id}:{r.step_key}", "pt": patient_id, "pf": r.result_prefix, "k": f"{idempotency_key}:{r.step_key}"},
                "select id from ops.care_plan_waits where wait_key = :w", {"w": f"wait:{plan_id}:{r.step_key}"},
            )


def _step(row: Any) -> Step:
    cond = Condition(row.cond_field, row.cond_op, float(row.cond_threshold)) if row.cond_field else None
    return Step(row.step_key, row.kind, row.label, row.result_prefix, cond, row.on_met)


class SqlPlanReader:
    async def next_open_wait(self, conn: AsyncConnection, *, plan_id: uuid.UUID) -> dict[str, str] | None:
        row = (
            await conn.execute(
                text(
                    "select w.step_key, w.wait_key from ops.care_plan_waits w join ops.care_plan_steps s on s.plan_id = w.plan_id and s.step_key = w.step_key "
                    "where w.plan_id = :p and w.open and s.status = 'open' order by s.position limit 1"
                ),
                {"p": plan_id},
            )
        ).first()
        return {"step_key": row.step_key, "wait_key": row.wait_key} if row else None

    async def step(self, conn: AsyncConnection, *, plan_id: uuid.UUID, step_key: str) -> Step:
        return _step((await conn.execute(text("select * from ops.care_plan_steps where plan_id = :p and step_key = :k"), {"p": plan_id, "k": step_key})).one())

    async def wait(self, conn: AsyncConnection, *, wait_key: str) -> Wait:
        r = (await conn.execute(text("select wait_key, plan_id, step_key, patient_id, prefix, open from ops.care_plan_waits where wait_key = :w"), {"w": wait_key})).one()
        return Wait(r.wait_key, str(r.plan_id), r.step_key, str(r.patient_id), r.prefix, r.open)


class SqlResultReader:
    async def get(self, conn: AsyncConnection, *, result_id: uuid.UUID) -> Result | None:
        r = (await conn.execute(text("select id, patient_id, code, measures, verified from clinical.results where id = :i"), {"i": result_id})).first()
        if r is None:
            return None
        values = {k: (float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None) for k, v in dict(r.measures).items()}
        return Result(str(r.id), str(r.patient_id), r.code, values, bool(r.verified))


class SqlWaitClaims:
    async def claim(self, conn: AsyncConnection, *, wait_key: str, result_id: uuid.UUID, idempotency_key: str) -> bool:
        n = (await conn.execute(text("update ops.care_plan_waits set claimed_result_id = :r where wait_key = :w and claimed_result_id is null and open"), {"r": result_id, "w": wait_key})).rowcount
        if n == 1:
            return True
        held = (await conn.execute(text("select claimed_result_id from ops.care_plan_waits where wait_key = :w"), {"w": wait_key})).scalar()
        return held == result_id  # the same result again is still the holder; another result already holds it


class SqlPlanCloser:
    async def close_step(self, conn: AsyncConnection, *, plan_id: uuid.UUID, step_key: str, outcome: str, evidence_result_id: uuid.UUID | None, idempotency_key: str) -> None:
        if outcome not in ("met", "not_met") or evidence_result_id is None:
            raise ValueError("a step closes only as met or not_met and cites the result that justified it")
        await conn.execute(text("update ops.care_plan_steps set status = :o, evidence_result_id = :e where plan_id = :p and step_key = :k and status in ('open', 'needs_doctor')"), {"o": outcome, "e": evidence_result_id, "p": plan_id, "k": step_key})
        await conn.execute(text("update ops.care_plan_waits set open = false where plan_id = :p and step_key = :k and open"), {"p": plan_id, "k": step_key})

    async def complete(self, conn: AsyncConnection, *, plan_id: uuid.UUID, idempotency_key: str) -> None:
        await conn.execute(text("update ops.care_plans set status = 'completed' where id = :p and status = 'active'"), {"p": plan_id})

    async def reject(self, conn: AsyncConnection, *, plan_id: uuid.UUID, idempotency_key: str) -> None:
        await conn.execute(text("update ops.care_plans set status = 'rejected' where id = :p and status in ('awaiting_confirmation', 'held', 'screening')"), {"p": plan_id})


class SqlDoctorTasks:
    async def raise_task(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, plan_id: uuid.UUID, reason: str, reasons: list[str], priority: str, assigned_role: str, idempotency_key: str
    ) -> uuid.UUID:
        import json

        task_id = await _insert_or_existing(
            conn,
            "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) values (:c, :o, :k, :p, :r, :s, cast(:v as jsonb), :i)",
            {"c": clinic_id, "o": origin, "k": f"care_plan_{reason}", "p": priority, "r": assigned_role, "s": f"plan:{plan_id}", "v": json.dumps({"plan_id": str(plan_id), "reasons": reasons}), "i": idempotency_key},
            "select id from agents.review_tasks where idempotency_key = :i and clinic_id = :c", {"i": idempotency_key, "c": clinic_id},
        )
        if reason == "needs_doctor":
            await conn.execute(text("update ops.care_plans set status = 'held' where id = :p and status in ('screening', 'awaiting_confirmation')"), {"p": plan_id})
        elif reason == "referral_proposal":
            # a met condition that proposed a referral closes its step as met, citing the result that claimed the wait (key: cj:<plan>:<step>:referral)
            step_key = idempotency_key.split(":")[2]
            held = (await conn.execute(text("select claimed_result_id from ops.care_plan_waits where plan_id = :p and step_key = :k"), {"p": plan_id, "k": step_key})).scalar()
            if held is not None:
                await SqlPlanCloser().close_step(conn, plan_id=plan_id, step_key=step_key, outcome="met", evidence_result_id=held, idempotency_key=idempotency_key)
        return task_id


@dataclass(frozen=True)
class PlanView:
    plan_id: uuid.UUID
    status: str
    waiting_for: Literal["confirmation", "result", "none"]
    task_id: uuid.UUID | None
    finished: bool


def _view(plan_id: uuid.UUID, snapshot: Any) -> PlanView:
    values = snapshot.values or {}
    pending = tuple(snapshot.next)
    waiting: Literal["confirmation", "result", "none"] = "confirmation" if pending == ("await_confirmation",) else "result" if pending == ("await_result",) else "none"
    try:
        task = uuid.UUID(str(values["task_id"])) if values.get("task_id") else None
    except ValueError:
        task = None
    return PlanView(plan_id, str(values.get("status", "started")), waiting, task, not pending and bool(values))


@dataclass
class CareJourneyComponents:
    """Everything the graph needs from outside, plus the clinic's governance values. The graph is built PER REQUEST around the request's own transaction."""

    vault: DemographicVault
    engine: RedFlagEngine
    model: PlanModel | None
    task_priority: str  # required settings, never defaulted: how urgent and for whom a care-plan task is is a clinical-governance decision
    task_role: str
    checkpointer: Any
    durable: bool = False
    screener: Any = None  # a stand-in for tests; None means the rules screening of the stored plan text

    def __post_init__(self) -> None:
        if self.task_priority not in TASK_PRIORITIES or self.task_role not in TASK_ROLES:
            raise ValueError("the task priority and role must be set to allowed values")

    def runner(self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str) -> "CareJourneyRunner":
        """A runner whose every step is a savepoint on the REQUEST's own transaction (as the signed-in person)."""
        return self.runner_with(request_session(conn), clinic_id=clinic_id, origin=origin)

    def runner_with(self, session: Any, *, clinic_id: uuid.UUID, origin: str) -> "CareJourneyRunner":
        """A runner over any session factory (the worker's: one transaction per use). Needs a durable checkpointer when the run spans transactions."""
        deps = CareJourneyDeps(
            session, self.screener or SqlPlanScreener(self.vault, self.engine), SqlPlanParser(self.vault, self.model), SqlPlanProposals(), SqlPlanActivator(), SqlPlanReader(), SqlResultReader(), SqlWaitClaims(),
            SqlPlanCloser(), SqlDoctorTasks(), self.task_priority, self.task_role,
        )  # type: ignore[arg-type]
        checkpointer = PostgresCheckpointSaver(SqlCheckpointStore(session)) if self.durable else self.checkpointer
        try:
            graph = build_care_journey_graph(deps, checkpointer)
        except FrontDoorError:
            raise Unavailable from None
        return CareJourneyRunner(graph, clinic_id=clinic_id if self.durable else None, origin=origin if self.durable else None)


class CareJourneyRunner:
    def __init__(self, graph: Any, *, clinic_id: uuid.UUID | None, origin: str | None) -> None:
        self._graph, self._clinic, self._origin = graph, clinic_id, origin

    def _config(self, plan_id: uuid.UUID) -> dict[str, Any]:
        return run_config(plan_id, self._clinic, self._origin)

    async def start(self, *, clinic_id: uuid.UUID, plan_id: uuid.UUID, patient_id: uuid.UUID, origin: str) -> PlanView:
        config = self._config(plan_id)
        existing = await self._graph.aget_state(config)
        if existing.values:
            return _view(plan_id, existing)
        await self._graph.ainvoke(initial_state(clinic_id, plan_id, patient_id, origin), config)
        return _view(plan_id, await self._graph.aget_state(config))

    async def view(self, plan_id: uuid.UUID) -> PlanView:
        snapshot = await self._graph.aget_state(self._config(plan_id))
        if not snapshot.values:
            raise NoSuchPlan
        return _view(plan_id, snapshot)

    async def confirm(self, plan_id: uuid.UUID, action: str) -> PlanView:
        if action not in ("approve", "reject"):
            raise WrongStep
        return await self._resume(plan_id, "await_confirmation", {"action": action})

    async def deliver_result(self, plan_id: uuid.UUID, result_id: uuid.UUID) -> PlanView:
        return await self._resume(plan_id, "await_result", {"kind": "result", "result_id": str(result_id)})

    async def _resume(self, plan_id: uuid.UUID, expected_pause: str, answer: dict[str, str]) -> PlanView:
        config = self._config(plan_id)
        snapshot = await self._graph.aget_state(config)
        if not snapshot.values:
            raise NoSuchPlan
        if tuple(snapshot.next) != (expected_pause,):
            raise WrongStep
        await self._graph.ainvoke(Command(resume=answer), config)
        return _view(plan_id, await self._graph.aget_state(config))


# ---- the routes' helpers -----------------------------------------------------------------------------------------------------------------------------------------------


class Refused(Exception):
    """A foreign key or a guard refused the write (no such patient or result in this clinic, an erased patient). Fixed words."""


def plan_id_for(clinic_id: uuid.UUID, idempotency_key: str) -> uuid.UUID:
    """The same key always names the same plan, so a replay recognises it (the ciphertext binds the plan id)."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-care-plan:{clinic_id}:{idempotency_key}")


async def create_plan(conn: AsyncConnection, vault: DemographicVault, *, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID, plan_text: str, created_by: uuid.UUID, key_version: int, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    from careflow.messages import encrypt_plan_text

    plan_id = plan_id_for(clinic_id, idempotency_key)
    ciphertext = encrypt_plan_text(vault, plan_text, clinic_id=clinic_id, plan_id=plan_id, key_version=key_version)
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "insert into ops.care_plans (id, clinic_id, origin, patient_id, plan_ct, plan_key_version, created_by, idempotency_key) values (:i, :c, :o, :p, :ct, :kv, :by, :k)"
                ),
                {"i": plan_id, "c": clinic_id, "o": origin, "p": patient_id, "ct": ciphertext, "kv": key_version, "by": created_by, "k": idempotency_key},
            )
        return plan_id, True
    except IntegrityError as exc:
        state = sqlstate(exc)
        if state in ("23503", "23001"):
            raise Refused from None
        if state != "23505":
            raise
    same = (await conn.execute(text("select 1 from ops.care_plans where id = :i and clinic_id = :c and patient_id = :p"), {"i": plan_id, "c": clinic_id, "p": patient_id})).first()
    if same is None:
        raise Refused  # the key belongs to a plan this caller cannot see or for another patient
    return plan_id, False


async def record_result(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID, code: str, measures: dict[str, float | None], verified: bool, valid_from: Any, valid_to: Any,
    supersedes_id: uuid.UUID | None, recorded_by: uuid.UUID, idempotency_key: str,
) -> tuple[uuid.UUID, bool]:
    import json

    return await _result_insert(
        conn,
        "insert into clinical.results (clinic_id, origin, patient_id, code, measures, verified, valid_from, valid_to, supersedes_id, recorded_by, idempotency_key) "
        "values (:c, :o, :p, :code, cast(:m as jsonb), :v, :vf, :vt, :s, :by, :k)",
        {"c": clinic_id, "o": origin, "p": patient_id, "code": code, "m": json.dumps(measures, sort_keys=True), "v": verified, "vf": valid_from, "vt": valid_to, "s": supersedes_id, "by": recorded_by, "k": idempotency_key},
        clinic_id, idempotency_key, patient_id,
    )


async def _result_insert(conn: AsyncConnection, sql: str, params: dict, clinic_id: uuid.UUID, key: str, patient_id: uuid.UUID) -> tuple[uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            return (await conn.execute(text(sql + " returning id"), params)).one().id, True
    except IntegrityError as exc:
        state = sqlstate(exc)
        if state in ("23503", "23514"):
            raise Refused from None
        if state != "23505":
            raise
    row = (await conn.execute(text("select id from clinical.results where clinic_id = :c and idempotency_key = :k and patient_id = :p"), {"c": clinic_id, "k": key, "p": patient_id})).first()
    if row is None:
        raise Refused  # the key is held by another result, or the result is superseded already
    return row.id, False


async def plans_waiting_for(conn: AsyncConnection, *, clinic_id: uuid.UUID, patient_id: uuid.UUID, code: str) -> list[uuid.UUID]:
    """Plans of this patient with an OPEN, unclaimed wait whose prefix this result code starts with (the longest prefix wins inside the graph; a tie is the graph's to hold for a doctor)."""
    rows = (
        await conn.execute(
            text(
                "select distinct plan_id from ops.care_plan_waits where clinic_id = :c and patient_id = :p and open and claimed_result_id is null and lower(:code) like lower(prefix) || '%' order by plan_id"
            ),
            {"c": clinic_id, "p": patient_id, "code": code},
        )
    ).all()
    return [r.plan_id for r in rows]


# ---- the outbox-woken wake-up (owner decision of 2026-11-07, item 11) ------------------------------------------------------------------------------------------------------------------------


TASK_TYPE: Final = "care_plan_result"  # the outbox event type, and so the job's task type: payload {"result_id"} only (an id, never a value)


def care_plan_result_handler(components: CareJourneyComponents, *, session_for: Any = worker_session) -> Handler:
    """The job handler for a recorded result: as the WORKER identity, find the plans of that patient with an open wait whose prefix the result's code carries, and resume each at its result pause. Safe to run
    twice: a plan not at a result pause (already woken, held, finished) raises WrongStep and is skipped; the claim, the closure and the tasks carry idempotency keys derived from the plan and the step."""

    async def handle(ctx: JobContext) -> None:
        try:
            result_id = uuid.UUID(str(ctx.job.payload["result_id"]))
        except (KeyError, ValueError):
            raise RunnerError("the job payload does not name a result") from None
        session = session_for(ctx.engine, ctx.worker_account_id)
        async with session() as conn:
            row = (await conn.execute(text("select clinic_id, origin, patient_id, code from clinical.results where id = :r"), {"r": result_id})).first()
            if row is None or row.clinic_id != ctx.job.clinic_id or row.origin != ctx.job.origin:
                raise RunnerError("the result is not in the job's clinic and origin")  # a tampered or mis-addressed job: retried, then dead-lettered, never run
            plans = await plans_waiting_for(conn, clinic_id=row.clinic_id, patient_id=row.patient_id, code=row.code)
        for plan_id in plans:
            try:
                await components.runner_with(session, clinic_id=ctx.job.clinic_id, origin=ctx.job.origin).deliver_result(plan_id, result_id)
            except (WrongStep, NoSuchPlan):
                continue

    return handle
