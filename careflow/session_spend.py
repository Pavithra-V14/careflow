"""The durable spend ledger for a model call made under a person's own session (migration 0075; docs/design/spend-ledger-options.md option (b2); BUILD-SPEC.md 6.1, 15.2).

Each read and write is its own short transaction on a SECOND connection carrying the caller's verified claims, committed at once, so a call whose request later rolls back still has its spend on record. Only the
database functions `ops.record_session_spend` and `ops.session_spend_today` are used: no table grant exists. The function forces role `decision` and class `ordinary`; a safety-class or other-role entry is refused
HERE before any call is made (and again by the database). A refusal is `SpendRefused`: the caller treats it as "no suggestion: needs a person", never as permission to proceed unrecorded.
"""

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from careflow.db import set_request_claims
from careflow.gateway import SpendEntry


class SpendRefused(Exception):
    """The database refused the call (no second factor, wrong role, no limit set for the clinic, or an amount above it), or the entry is not one this ledger may record."""


class SessionSpendLedger:
    """A `SpendLedger` (careflow/gateway.py) for ONE request: built with that request's verified claims."""

    def __init__(self, engine: AsyncEngine, claims: dict[str, Any]) -> None:
        self._engine, self._claims = engine, dict(claims)

    async def spent_today(self, clinic_id: uuid.UUID, origin: str) -> Decimal:
        try:
            async with self._engine.connect() as conn, conn.begin():
                await set_request_claims(conn, self._claims)
                total = (await conn.execute(text("select ops.session_spend_today(:c, :o)"), {"c": clinic_id, "o": origin})).scalar_one()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise SpendRefused from None
            raise
        return Decimal(total)

    async def record(self, entry: SpendEntry) -> None:
        if entry.role != "decision" or entry.call_class != "ordinary":
            raise SpendRefused  # a safety call is never recorded or limited here; only the decision tier's ordinary calls
        try:
            async with self._engine.connect() as conn, conn.begin():
                await set_request_claims(conn, self._claims)
                await conn.execute(
                    text("select ops.record_session_spend(:c, :o, :p, :m, :a, :s, :k)"),
                    {"c": entry.clinic_id, "o": entry.origin, "p": entry.provider, "m": entry.model_id, "a": entry.usd_actual, "s": entry.usd_shadow, "k": entry.idempotency_key},
                )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) == "42501":
                raise SpendRefused from None
            raise
