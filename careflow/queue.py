"""The Postgres-backed queue and transactional outbox (BUILD-SPEC.md §7.2, §7.3, §7.4, §17.1 `ops`).

`QueuePort` is the interface (`enqueue`, `claim`, `acknowledge`, `dead_letter`); `PostgresQueue` implements
it over SQLAlchemy Core async. A second, unwired Redis/SQS-style adapter is a future option (§7.4) and is
deliberately not built: this workload (jobs that wait days) does not need it.

Every method takes an `AsyncConnection` already inside a transaction, running as the restricted app role
with the verified claims set (`careflow.db.set_request_claims`). Row-level security decides who may do what:
a producer (front desk, nurse, doctor, admin) can enqueue, only a `queue_worker` of the clinic can claim,
acknowledge or dead-letter, and nobody sees another clinic's jobs. A refused write surfaces as `NotPermitted`.

What "exactly once" does and does not mean here
  * EXCLUSIVE delivery: `claim` uses `FOR UPDATE SKIP LOCKED`, so at any instant a job is held by at most one
    worker, however many workers poll in parallel. Two workers can never both be handed the same job.
  * AT-LEAST-ONCE across failures: if a worker dies, its job reappears when the visibility timeout passes and
    is handed to someone else. Handlers must therefore be idempotent, with an idempotency key on every side
    effect (§7.2); the queue cannot make a handler's external effect happen once on its own.
  * ENQUEUE is idempotent: a retried `enqueue` with the same key returns the original job; the same key with
    different parameters raises IdempotencyConflict. The outbox relay uses the event's key as the job's key,
    so an event becomes exactly one job even if two relays race.

Attempts and the ceiling (§7.3): `claim` increments `attempt_count`. A job whose visibility timeout has
expired after its fifth claim is dead-lettered by the next `claim` instead of being handed out a sixth time;
`dead_letter` can also be called earlier for a failure that is not worth retrying. The database refuses an
`attempt_count` above 5 regardless of this code.

Payloads carry identifiers and small facts, never patient text or demographics (rules 6 and 7). Nothing here
sends a message or calls out of the process; a job is a row.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

MAX_ATTEMPTS: Final = 5  # BUILD-SPEC.md §7.3; mirrored by a CHECK on ops.queue.attempt_count
MAX_ERROR_CHARS: Final = 8000  # mirrored by a CHECK on ops.queue.last_error
Origin = Literal["live", "test", "eval", "canary"]
ORIGINS: Final = ("live", "test", "eval", "canary")


class QueueError(Exception):
    """Base class: the request was refused and nothing was changed."""


class IdempotencyConflict(QueueError):
    """This idempotency key is already used by a request with different parameters."""


class JobNotFound(QueueError):
    """No such job, or the caller's role cannot see it."""


class NotOwner(QueueError):
    """The job is not currently claimed by this worker (it finished, expired and was re-claimed, or was never its)."""


class NotPermitted(QueueError):
    """Row-level security refused the write: the caller's role may not do this."""


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    clinic_id: uuid.UUID
    origin: str
    task_type: str
    payload: dict[str, Any]
    attempt_count: int
    claimed_by: str
    visible_after: datetime
    idempotency_key: str


@dataclass(frozen=True)
class EnqueueResult:
    job_id: uuid.UUID
    replayed: bool = False


class QueuePort(Protocol):
    async def enqueue(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, task_type: str,
        payload: dict[str, Any], idempotency_key: str,
    ) -> EnqueueResult: ...

    async def claim(self, conn: AsyncConnection, worker_id: str, visibility_timeout_seconds: float) -> Job | None: ...

    async def acknowledge(self, conn: AsyncConnection, job_id: uuid.UUID, *, worker_id: str) -> None: ...

    async def dead_letter(self, conn: AsyncConnection, job_id: uuid.UUID, error_message: str, *, worker_id: str) -> None: ...


def _check_key(key: object) -> str:
    if not isinstance(key, str) or not key.strip():
        raise QueueError("an idempotency key is required on every write")
    return key


def _check_worker(worker_id: object) -> str:
    if not isinstance(worker_id, str) or not worker_id.strip() or len(worker_id) > 200:
        raise QueueError("worker_id must be a non-empty string of at most 200 characters")
    return worker_id


def _check_event(origin: object, name: object, payload: object) -> None:
    if origin not in ORIGINS:
        raise ValueError(f"origin must be one of {ORIGINS}, got {origin!r}")
    if not isinstance(name, str) or not name:
        raise ValueError("a task/event type is required")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object")


def _raise_for_dbapi(exc: DBAPIError) -> None:
    if getattr(exc.orig, "sqlstate", None) == "42501":
        raise NotPermitted("your role may not make this change") from exc


class PostgresQueue:
    """`QueuePort` over `ops.queue`, plus the outbox relay (`record_event`, `relay_outbox`)."""

    # ---- enqueue -----------------------------------------------------------------------------------

    async def enqueue(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, task_type: str,
        payload: dict[str, Any], idempotency_key: str,
    ) -> EnqueueResult:
        key = _check_key(idempotency_key)
        _check_event(origin, task_type, payload)
        body = json.dumps(payload, sort_keys=True)
        try:
            async with conn.begin_nested():
                created = (
                    await conn.execute(
                        text(
                            """insert into ops.queue (clinic_id, origin, task_type, payload, idempotency_key)
                               values (:c, :o, :t, cast(:p as jsonb), :k)
                               on conflict (idempotency_key) do nothing
                               returning id"""
                        ),
                        {"c": clinic_id, "o": origin, "t": task_type, "p": body, "k": key},
                    )
                ).first()
        except (IntegrityError, DBAPIError) as exc:
            _raise_for_dbapi(exc)
            raise
        if created is not None:
            return EnqueueResult(created[0])
        prior = (
            await conn.execute(
                text("select id, clinic_id, origin, task_type, payload from ops.queue where idempotency_key = :k"), {"k": key}
            )
        ).first()
        if prior is None:  # the key exists but is not visible to this caller (another clinic's)
            raise IdempotencyConflict("this idempotency key is already used by another request")
        if (prior.clinic_id, prior.origin, prior.task_type, prior.payload) != (clinic_id, origin, task_type, json.loads(body)):
            raise IdempotencyConflict("this idempotency key was used for a different job")
        return EnqueueResult(prior.id, replayed=True)

    # ---- claim -------------------------------------------------------------------------------------

    async def claim(self, conn: AsyncConnection, worker_id: str, visibility_timeout_seconds: float) -> Job | None:
        """Hand the oldest eligible job to `worker_id` and hide it for `visibility_timeout_seconds`.

        Eligible: unclaimed, or claimed but past its visibility time (the worker is presumed dead), and under
        the attempt ceiling. Locked rows are skipped, never waited on. Returns None when nothing is available.
        """
        worker = _check_worker(worker_id)
        if isinstance(visibility_timeout_seconds, bool) or not isinstance(visibility_timeout_seconds, int | float) or visibility_timeout_seconds <= 0:
            raise ValueError("visibility_timeout_seconds must be a positive number")
        try:
            # A job that used its last attempt and then went invisible-and-silent is finished, not retried.
            await conn.execute(
                text(
                    """update ops.queue
                       set state = 'dead_lettered', dead_lettered_at = clock_timestamp(),
                           last_error = coalesce(last_error, '') || :why
                       where state = 'claimed' and visible_after <= clock_timestamp() and attempt_count >= :max"""
                ),
                {"max": MAX_ATTEMPTS, "why": f"\n[attempt ceiling {MAX_ATTEMPTS} reached; visibility timeout expired without acknowledgement]"},
            )
            row = (
                await conn.execute(
                    text(
                        """with next as (
                               select id from ops.queue
                               where state in ('unclaimed', 'claimed')
                                 and visible_after <= clock_timestamp()
                                 and attempt_count < :max
                               order by created_at, id
                               for update skip locked
                               limit 1)
                           update ops.queue q
                           set state = 'claimed', attempt_count = q.attempt_count + 1,
                               claimed_at = clock_timestamp(), claimed_by = :w,
                               visible_after = clock_timestamp() + make_interval(secs => :t)
                           from next where q.id = next.id
                           returning q.id, q.clinic_id, q.origin, q.task_type, q.payload, q.attempt_count,
                                     q.claimed_by, q.visible_after, q.idempotency_key"""
                    ),
                    {"max": MAX_ATTEMPTS, "w": worker, "t": float(visibility_timeout_seconds)},
                )
            ).first()
        except DBAPIError as exc:
            _raise_for_dbapi(exc)
            raise
        if row is None:
            return None
        return Job(row.id, row.clinic_id, row.origin, row.task_type, row.payload, row.attempt_count,
                   row.claimed_by, row.visible_after, row.idempotency_key)

    # ---- acknowledge / dead_letter -----------------------------------------------------------------

    async def acknowledge(self, conn: AsyncConnection, job_id: uuid.UUID, *, worker_id: str) -> None:
        """Mark the job completed. Only the worker that currently holds the claim may do so, so a worker whose
        job expired and was re-claimed cannot complete someone else's attempt. Repeating a successful
        acknowledgement is a no-op."""
        worker = _check_worker(worker_id)
        await self._finish(
            conn, job_id, worker, done_state="completed",
            sql="update ops.queue set state = 'completed', completed_at = clock_timestamp() "
                "where id = :j and state = 'claimed' and claimed_by = :w returning id",
            params={},
        )

    async def dead_letter(self, conn: AsyncConnection, job_id: uuid.UUID, error_message: str, *, worker_id: str) -> None:
        """Move the job permanently to `dead_lettered`, recording the error text (truncated to the column cap).
        Same ownership rule as `acknowledge`. Used at the attempt ceiling, or sooner for a failure not worth retrying."""
        worker = _check_worker(worker_id)
        if not isinstance(error_message, str) or not error_message.strip():
            raise QueueError("a dead-lettered job needs an error message")
        message = error_message if len(error_message) <= MAX_ERROR_CHARS else error_message[: MAX_ERROR_CHARS - 12] + "\n[truncated]"
        await self._finish(
            conn, job_id, worker, done_state="dead_lettered",
            sql="update ops.queue set state = 'dead_lettered', dead_lettered_at = clock_timestamp(), last_error = :err "
                "where id = :j and state = 'claimed' and claimed_by = :w returning id",
            params={"err": message},
        )

    async def _finish(self, conn: AsyncConnection, job_id: uuid.UUID, worker: str, *, done_state: str, sql: str, params: dict) -> None:
        try:
            async with conn.begin_nested():
                changed = (await conn.execute(text(sql), {"j": job_id, "w": worker, **params})).first()
        except DBAPIError as exc:
            _raise_for_dbapi(exc)
            raise
        if changed is not None:
            return
        current = (await conn.execute(text("select state, claimed_by from ops.queue where id = :j"), {"j": job_id})).first()
        if current is None:
            raise JobNotFound("job not found")
        if current.state == done_state and current.claimed_by == worker:
            return  # already finished by this worker: a replay
        raise NotOwner(f"job is {current.state} and not held by this worker")

    # ---- outbox ------------------------------------------------------------------------------------

    async def record_event(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: Origin, event_type: str,
        payload: dict[str, Any], idempotency_key: str,
    ) -> EnqueueResult:
        """Write an outbound event in the CALLER'S transaction, next to the business change that caused it: both
        commit or neither does. Idempotent on the key."""
        key = _check_key(idempotency_key)
        _check_event(origin, event_type, payload)
        body = json.dumps(payload, sort_keys=True)
        try:
            async with conn.begin_nested():
                created = (
                    await conn.execute(
                        text(
                            """insert into ops.outbox (clinic_id, origin, event_type, payload, idempotency_key)
                               values (:c, :o, :t, cast(:p as jsonb), :k)
                               on conflict (idempotency_key) do nothing
                               returning id"""
                        ),
                        {"c": clinic_id, "o": origin, "t": event_type, "p": body, "k": key},
                    )
                ).first()
        except (IntegrityError, DBAPIError) as exc:
            _raise_for_dbapi(exc)
            raise
        if created is not None:
            return EnqueueResult(created[0])
        prior = (
            await conn.execute(
                text("select id, clinic_id, origin, event_type, payload from ops.outbox where idempotency_key = :k"), {"k": key}
            )
        ).first()
        if prior is None or (prior.clinic_id, prior.origin, prior.event_type, prior.payload) != (clinic_id, origin, event_type, json.loads(body)):
            raise IdempotencyConflict("this idempotency key was used for a different event")
        return EnqueueResult(prior.id, replayed=True)

    async def relay_outbox(self, conn: AsyncConnection, limit: int = 100) -> int:
        """Turn pending outbox events into queue jobs (event type becomes task type, the event's key becomes the
        job's key), marking each event dispatched, all in the caller's transaction. Rows locked by another
        relay are skipped, and the job's unique key means an event can never become two jobs. Returns how many
        events this call dispatched. Requires the `queue_worker` role."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        try:
            async with conn.begin_nested():
                pending = (
                    await conn.execute(
                        text(
                            """select id, clinic_id, origin, event_type, payload, idempotency_key from ops.outbox
                               where dispatched_at is null order by created_at, id
                               for update skip locked limit :n"""
                        ),
                        {"n": limit},
                    )
                ).all()
                for ev in pending:
                    job = (
                        await conn.execute(
                            text(
                                """insert into ops.queue (clinic_id, origin, task_type, payload, idempotency_key)
                                   values (:c, :o, :t, cast(:p as jsonb), :k)
                                   on conflict (idempotency_key) do nothing returning id"""
                            ),
                            {"c": ev.clinic_id, "o": ev.origin, "t": ev.event_type, "p": json.dumps(ev.payload, sort_keys=True), "k": ev.idempotency_key},
                        )
                    ).first()
                    if job is None:  # the job already exists under this key (an earlier, partly-failed relay)
                        job = (await conn.execute(text("select id from ops.queue where idempotency_key = :k"), {"k": ev.idempotency_key})).one()
                    await conn.execute(
                        text("update ops.outbox set dispatched_at = clock_timestamp(), job_id = :j where id = :e"),
                        {"j": job[0], "e": ev.id},
                    )
        except DBAPIError as exc:
            _raise_for_dbapi(exc)
            raise
        return len(pending)
