"""The second, UNWIRED queue adapter (BUILD-SPEC.md 3 and 7.4: "a Redis/SQS adapter is written but not wired").

It exists so `QueuePort` has a second implementation to be checked against and so a future move to a broker is a configuration change, not a redesign. It is NOT usable: it holds no client, imports no broker
library (no new dependency, CLAUDE.md rule 13), and every method refuses with `QueueError` in fixed words, so wiring it by mistake fails loudly instead of silently dropping a job. Nothing imports it from the
application or the worker; `tests/test_queue_broker_stub.py` keeps it that way. This workload (jobs that wait days) does not need a broker (spec Appendix A).
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.queue import EnqueueResult, Job, QueueError

NOT_WIRED = "the broker queue adapter is a stub and is not wired: the Postgres queue is the only queue"


class BrokerQueueStub:
    async def enqueue(self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str, task_type: str, payload: dict[str, Any], idempotency_key: str) -> EnqueueResult:
        raise QueueError(NOT_WIRED)

    async def claim(self, conn: AsyncConnection, worker_id: str, visibility_timeout_seconds: float) -> Job | None:
        raise QueueError(NOT_WIRED)

    async def acknowledge(self, conn: AsyncConnection, job_id: uuid.UUID, *, worker_id: str) -> None:
        raise QueueError(NOT_WIRED)

    async def dead_letter(self, conn: AsyncConnection, job_id: uuid.UUID, error_message: str, *, worker_id: str) -> None:
        raise QueueError(NOT_WIRED)
