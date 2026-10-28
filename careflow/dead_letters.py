"""A human-visible task for a job that was dead-lettered (BUILD-SPEC.md §7.3, §16.2; CLAUDE.md rules 1, 3, 5, 6). Owner decision of 2026-10-24.

THE GAP THIS CLOSES. The Front Door graph never ends silently (`hold_for_human`), but a JOB can: the queue dead-letters a `front_door_run` job after five failed attempts (or at
once, for a missing handler or a ceiling), and nothing told a person. This sweep raises a nurse's review task, kind `referral_dead_letter`, for every dead-lettered job of the
listed task types that has none yet. It runs at the end of every tick (careflow.tick), as the worker account, so it covers BOTH ways a job dead-letters: inside `claim` (the
attempt ceiling) and through `dead_letter()` (no handler, a budget ceiling).

WHAT THE TASK HOLDS: ids and numbers only: the job id, the task type, the number of attempts, and `document:<id>` as the source. NEVER the job's error text (it is not copied),
a name, a date or any letter text. Its `priority` is a REQUIRED argument of the writer, never defaulted: how urgent a stuck referral is, is a clinical decision.
# CLINICAL-PLACEHOLDER: needs clinician sign-off

IDEMPOTENT: the task's key is `dead-letter:<job id>`, and the sweep skips jobs whose key already exists, so running it twice (or two workers at once) creates one task.
No migration is needed: `agents.review_tasks` takes any lower-case kind, the worker may insert (0007/0013) and a nurse sees every kind.
"""

import json
import uuid
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

KIND: Final = "referral_dead_letter"
DEFAULT_TASK_TYPES: Final = ("front_door_run",)
BATCH: Final = 50


class DeadLetterError(Exception):
    """Fixed words only."""


class SqlDeadLetterTaskWriter:
    def __init__(self, *, priority: str) -> None:
        if priority not in ("immediate", "standard"):  # CLINICAL-PLACEHOLDER: needs clinician sign-off (which priority a stuck referral's task carries)
            raise ValueError("priority must be 'immediate' or 'standard'")
        self._priority = priority

    async def raise_task(
        self, conn: AsyncConnection, *, job_id: uuid.UUID, clinic_id: uuid.UUID, origin: str, task_type: str, payload: dict[str, Any], attempts: int
    ) -> bool:
        """True if a task was created, False if one already existed for this job."""
        document = payload.get("document_id") if isinstance(payload, dict) else None
        try:
            source = f"document:{uuid.UUID(str(document))}"
        except (ValueError, TypeError):
            source = f"job:{job_id}"
        provenance = {"job_id": str(job_id), "task_type": task_type, "attempts": int(attempts)}
        try:
            async with conn.begin_nested():
                created = (
                    await conn.execute(
                        text(
                            "insert into agents.review_tasks (clinic_id, origin, kind, priority, assigned_role, source_message_id, provenance, idempotency_key) "
                            "values (:c, :o, :k, :pr, 'nurse', :s, cast(:p as jsonb), :ik) on conflict (idempotency_key) do nothing returning id"
                        ),
                        {"c": clinic_id, "o": origin, "k": KIND, "pr": self._priority, "s": source, "p": json.dumps(provenance), "ik": f"dead-letter:{job_id}"},
                    )
                ).first()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise DeadLetterError("this role may not raise a dead-letter task") from None
            raise
        return created is not None


async def raise_dead_letter_tasks(
    conn: AsyncConnection, writer: SqlDeadLetterTaskWriter, *, task_types: tuple[str, ...] = DEFAULT_TASK_TYPES, limit: int = BATCH
) -> int:
    """One task for each dead-lettered job of these types that has none. Returns how many tasks it created."""
    rows = (
        await conn.execute(
            text(
                "select q.id, q.clinic_id, q.origin, q.task_type, q.payload, q.attempt_count from ops.queue q "
                "where q.state = 'dead_lettered' and q.task_type = any(cast(:t as text[])) "
                "and not exists (select 1 from agents.review_tasks r where r.idempotency_key = 'dead-letter:' || q.id::text) "
                "order by q.dead_lettered_at, q.id limit :n"
            ),
            {"t": list(task_types), "n": limit},
        )
    ).all()
    created = 0
    for r in rows:
        if await writer.raise_task(conn, job_id=r.id, clinic_id=r.clinic_id, origin=r.origin, task_type=r.task_type, payload=r.payload, attempts=r.attempt_count):
            created += 1
    return created
