"""Staff account recovery through two admins (migration 0060; BUILD-SPEC.md 18.1). Thin wrappers over the two definer functions; the database decides who may do what."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate


class Refused(Exception):
    """The database refused the caller (not an admin of the clinic, no second factor, not a staff account of the clinic, a recovery already open)."""


async def request(conn: AsyncConnection, *, clinic_id: uuid.UUID, account_id: uuid.UUID, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select request_id, created from iam.request_staff_recovery(:c, :a, :k)"), {"c": clinic_id, "a": account_id, "k": idempotency_key})).one()
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        raise
    return row.request_id, bool(row.created)


async def resolve(conn: AsyncConnection, *, request_id: uuid.UUID, action: str) -> str:
    try:
        async with conn.begin_nested():
            return str((await conn.execute(text("select iam.resolve_staff_recovery(:r, :a)"), {"r": request_id, "a": action})).scalar())
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        raise


async def request_clinic(conn: AsyncConnection, request_id: uuid.UUID) -> uuid.UUID | None:
    """The clinic of a request you may see (row-level security: admins of the clinic with a second factor), or None."""
    row = (await conn.execute(text("select clinic_id from iam.recovery_requests where id = :r"), {"r": request_id})).first()
    return row.clinic_id if row else None


async def request_state(conn: AsyncConnection, request_id: uuid.UUID) -> Any:
    return (await conn.execute(text("select status, target_account_id from iam.recovery_requests where id = :r"), {"r": request_id})).first()


async def list_requests(conn: AsyncConnection, *, clinic_id: uuid.UUID, open_only: bool, limit: int) -> list[Any]:
    return list(
        (
            await conn.execute(
                text(
                    "select id, target_account_id, requested_by, approved_by, status, created_at, decided_at from iam.recovery_requests "
                    "where clinic_id = :c and (not :o or status in ('requested', 'approved')) order by created_at desc, id limit :n"
                ),
                {"c": clinic_id, "o": open_only, "n": limit},
            )
        ).all()
    )
