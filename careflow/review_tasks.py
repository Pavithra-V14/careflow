"""The review-task queue as the frontend needs it: list, open, acknowledge, resolve (docs/design/review-task-routes.md). Every statement runs in the CALLER's own transaction under row-level
security (migrations 0007, 0013, 0020): the database decides who sees and who may change a task, this module decides nothing about authority. No new grant, role or policy.

* `list_tasks` / `get_task`: whatever the caller's SELECT policy lets them see (nurse and doctor: all kinds; front desk and admin: `unmerge_review`; front desk also its own
  `referral_hold`s). An invisible task is the same as a missing one.
* `acknowledge`: `open` -> `acknowledged`, by the caller. The same person asking again is a replay; another person's acknowledgement is a conflict.
* `resolve`: `open | acknowledged` -> `resolved`. ONLY for the kinds in `RESOLVABLE_KINDS`. Releasing a referral hold IS resolving its `referral_hold` task. Other kinds close through
  their own route (a triage proposal through the referral review decision) or need a clinician's decision on what "resolved" requires (`red_flag`, the screening kinds): they are
  refused here with `NotResolvableHere`, never closed quietly.
* Nothing here reads or writes patient text: a task's provenance holds rule ids, record ids and closed words only (migration 0007).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

Status = Literal["open", "acknowledged", "resolved"]
STATUSES: Final = ("open", "acknowledged", "resolved")
# Kinds this route may close. Each one is a task whose resolution is an administrative act with no clinical judgement of its own.
RESOLVABLE_KINDS: Final = frozenset({"referral_hold", "unmerge_review", "referral_dead_letter", "identity_confirmation_revoked"})


class TaskError(Exception):
    """Fixed words only."""


class TaskNotFound(TaskError):
    """No such task is visible to the caller."""


class NotPermitted(TaskError):
    """The caller can see the task but may not change it."""


class TaskConflict(TaskError):
    """Another person holds the task, or it is already closed."""


class NotResolvableHere(TaskError):
    """This kind of task is closed through its own route or needs a clinician's decision first."""


@dataclass(frozen=True)
class TaskRow:
    id: uuid.UUID
    clinic_id: uuid.UUID
    origin: str
    kind: str
    priority: str
    assigned_role: str
    status: str
    created_at: datetime
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None
    resolved_at: datetime | None
    source_message_id: str | None = None
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class Transition:
    task_id: uuid.UUID
    clinic_id: uuid.UUID
    origin: str
    kind: str
    status: str
    replayed: bool


_LIST_COLUMNS = "id, clinic_id, origin, kind, priority, assigned_role, status, created_at, acknowledged_by, acknowledged_at, resolved_at"


def _row(r: Any, *, detail: bool = False) -> TaskRow:
    base = {k: r[i] for i, k in enumerate(_LIST_COLUMNS.replace(" ", "").split(","))}
    if detail:
        base["source_message_id"], base["provenance"] = r[11], dict(r[12])
    return TaskRow(**base)


async def list_tasks(
    conn: AsyncConnection, *, status: str | None, kind: str | None, assigned_role: str | None, limit: int
) -> list[TaskRow]:
    """Immediate before standard, then oldest first. Returns up to `limit` rows (the caller asks for one more to learn whether there is a next page)."""
    rows = await conn.execute(
        text(
            f"select {_LIST_COLUMNS} from agents.review_tasks "
            "where (cast(:s as text) is null or status = :s) and (cast(:k as text) is null or kind = :k) and (cast(:r as text) is null or assigned_role = :r) "
            "order by (priority = 'immediate') desc, created_at, id limit :n"
        ),
        {"s": status, "k": kind, "r": assigned_role, "n": limit},
    )
    return [_row(r) for r in rows]


async def get_task(conn: AsyncConnection, task_id: uuid.UUID) -> TaskRow:
    row = (
        await conn.execute(text(f"select {_LIST_COLUMNS}, source_message_id, provenance from agents.review_tasks where id = :i"), {"i": task_id})
    ).first()
    if row is None:
        raise TaskNotFound("not found")
    return _row(row, detail=True)


async def acknowledge(conn: AsyncConnection, task_id: uuid.UUID, by: uuid.UUID) -> Transition:
    task = await get_task(conn, task_id)
    if task.status == "acknowledged" and task.acknowledged_by == by:
        return Transition(task.id, task.clinic_id, task.origin, task.kind, "acknowledged", True)
    if task.status != "open":
        raise TaskConflict("this task is already taken or closed")
    try:
        async with conn.begin_nested():
            changed = (
                await conn.execute(
                    text(
                        "update agents.review_tasks set status = 'acknowledged', acknowledged_by = :u, acknowledged_at = now() "
                        "where id = :i and status = 'open' and acknowledged_by is null"
                    ),
                    {"u": by, "i": task_id},
                )
            ).rowcount
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":
            raise NotPermitted("your role may not change this task") from exc
        raise
    if changed == 1:
        return Transition(task.id, task.clinic_id, task.origin, task.kind, "acknowledged", False)
    # Zero rows: the policy hid the row from the update (this role may not change it) or somebody else took it between the read and the write. Read again to tell which.
    again = await get_task(conn, task_id)
    if again.status == "acknowledged" and again.acknowledged_by == by:
        return Transition(task.id, task.clinic_id, task.origin, task.kind, "acknowledged", True)
    if again.status != "open":
        raise TaskConflict("this task is already taken or closed")
    raise NotPermitted("your role may not change this task")


async def resolve(conn: AsyncConnection, task_id: uuid.UUID, by: uuid.UUID) -> Transition:
    task = await get_task(conn, task_id)
    if task.kind not in RESOLVABLE_KINDS:
        raise NotResolvableHere("this kind of task is closed through its own route")
    if task.status == "resolved":
        return Transition(task.id, task.clinic_id, task.origin, task.kind, "resolved", True)
    if task.status == "acknowledged" and task.acknowledged_by != by:
        raise TaskConflict("this task is held by another person")
    try:
        async with conn.begin_nested():
            changed = (
                await conn.execute(
                    text(
                        "update agents.review_tasks set status = 'resolved', acknowledged_by = coalesce(acknowledged_by, :u), acknowledged_at = coalesce(acknowledged_at, now()), "
                        "resolved_at = now() where id = :i and status <> 'resolved'"
                    ),
                    {"u": by, "i": task_id},
                )
            ).rowcount
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "42501":  # the WITH CHECK refuses a task another person acknowledged
            raise TaskConflict("this task is held by another person") from exc
        raise
    if changed == 1:
        return Transition(task.id, task.clinic_id, task.origin, task.kind, "resolved", False)
    again = await get_task(conn, task_id)
    if again.status == "resolved":
        return Transition(task.id, task.clinic_id, task.origin, task.kind, "resolved", True)
    raise NotPermitted("your role may not change this task")
