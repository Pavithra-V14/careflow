"""The persisted spend ledger (migration 0016, `ops.spend_ledger`; BUILD-SPEC.md §6.1, §15.2): the `SpendLedger` the gateway uses in production.

Each read and write is its own short transaction as the WORKER account (row-level security lets only a `queue_worker` of the clinic insert,
and only that worker, admin, business owner or auditor read), committed at once, so a model call that is later rolled back with its job still
has its spend on record. The day is never computed here: the database stamps it from its own clock in India time, and `spent_today` asks the
database for "today" with the same expression, so the two cannot disagree by a time zone. The total is for one ORIGIN and includes the SAFETY
class (a ceiling limits ordinary calls, but safety spend is real spend). Nothing here logs.
"""

import uuid
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from careflow.db import set_request_claims
from careflow.gateway import SpendEntry

_TODAY_IST = "(clock_timestamp() at time zone 'Asia/Kolkata')::date"


class PostgresSpendLedger:
    def __init__(self, engine: AsyncEngine, worker_account_id: uuid.UUID) -> None:
        self._engine, self._account = engine, worker_account_id

    async def spent_today(self, clinic_id: uuid.UUID, origin: str) -> Decimal:
        async with self._engine.connect() as conn, conn.begin():
            await set_request_claims(conn, {"sub": str(self._account), "role": "authenticated"})
            total = (
                await conn.execute(
                    text(f"select coalesce(sum(usd_actual), 0) from ops.spend_ledger where clinic_id = :c and origin = :o and day = {_TODAY_IST}"),
                    {"c": clinic_id, "o": origin},
                )
            ).scalar_one()
        return Decimal(total)

    async def record(self, entry: SpendEntry) -> None:
        async with self._engine.connect() as conn, conn.begin():
            await set_request_claims(conn, {"sub": str(self._account), "role": "authenticated"})
            await conn.execute(
                text(
                    "insert into ops.spend_ledger (clinic_id, role, provider, model_id, call_class, usd_actual, usd_shadow, origin, idempotency_key) "
                    "values (:c, :r, :p, :m, :k, :a, :s, :o, :i)"
                ),
                {"c": entry.clinic_id, "r": entry.role, "p": entry.provider, "m": entry.model_id, "k": entry.call_class,
                 "a": entry.usd_actual, "s": entry.usd_shadow, "o": entry.origin, "i": entry.idempotency_key},
            )
