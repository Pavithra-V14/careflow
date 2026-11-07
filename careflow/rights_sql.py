"""Patient rights: grievances and the download of one's own data (migration 0059; BUILD-SPEC.md 19.3, 20.1).

The database writes everything through definer functions (no application table grant). Grievance texts are stored encrypted (careflow.messages.encrypt_grievance_text) and are returned as stored; the caller
decrypts them and owns the text.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate


class Refused(Exception):
    """The database refused the caller (not an active patient account bound to the clinic, not an admin, no second factor)."""


def _refuse_on_42501(exc: DBAPIError) -> None:
    if sqlstate(exc) == "42501":
        raise Refused from None


async def file_grievance(conn: AsyncConnection, *, clinic_id: uuid.UUID, category: str, body_ct: bytes, key_version: int, grievance_id: uuid.UUID, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text("select grievance_id, created from iam.file_grievance(:c, :cat, :ct, cast(:kv as smallint), :g, :k)"),
                    {"c": clinic_id, "cat": category, "ct": body_ct, "kv": key_version, "g": grievance_id, "k": idempotency_key},
                )
            ).one()
    except DBAPIError as exc:
        _refuse_on_42501(exc)
        raise
    return row.grievance_id, bool(row.created)


_LIST = (
    "select g.id, g.clinic_id, g.category, g.body_ct, g.body_key_version, g.erased_at, g.created_at, "
    "case when exists (select 1 from iam.grievance_updates u where u.grievance_id = g.id and u.status = 'resolved') then 'resolved' "
    "when exists (select 1 from iam.grievance_updates u where u.grievance_id = g.id and u.status = 'acknowledged') then 'acknowledged' else 'open' end as status "
    "from iam.grievances g where {where} order by g.created_at desc, g.id limit :n"
)


async def list_own(conn: AsyncConnection, *, account_id: uuid.UUID, limit: int) -> list[Any]:
    return list((await conn.execute(text(_LIST.format(where="g.account_id = :a")), {"a": account_id, "n": limit})).all())


async def list_clinic(conn: AsyncConnection, *, clinic_id: uuid.UUID, limit: int) -> list[Any]:
    return list((await conn.execute(text(_LIST.format(where="g.clinic_id = :c")), {"c": clinic_id, "n": limit})).all())


async def grievance_clinic(conn: AsyncConnection, grievance_id: uuid.UUID) -> uuid.UUID | None:
    row = (await conn.execute(text("select clinic_id from iam.grievances where id = :g"), {"g": grievance_id})).first()
    return row.clinic_id if row else None


async def update_status(conn: AsyncConnection, *, grievance_id: uuid.UUID, status: str) -> str:
    """'recorded', 'replay' or 'invalid_transition'."""
    try:
        async with conn.begin_nested():
            return str((await conn.execute(text("select iam.update_grievance(:g, :s)"), {"g": grievance_id, "s": status})).scalar())
    except DBAPIError as exc:
        _refuse_on_42501(exc)
        raise


async def export_messages(conn: AsyncConnection) -> list[Any]:
    try:
        async with conn.begin_nested():
            return list((await conn.execute(text("select * from iam.export_my_messages()"))).all())
    except DBAPIError as exc:
        _refuse_on_42501(exc)
        raise


async def export_bookings(conn: AsyncConnection) -> list[Any]:
    try:
        async with conn.begin_nested():
            return list((await conn.execute(text("select * from iam.export_my_bookings()"))).all())
    except DBAPIError as exc:
        _refuse_on_42501(exc)
        raise


async def own_records(conn: AsyncConnection, account_id: uuid.UUID) -> list[Any]:
    """The patient record(s) bound to the account (row-level security shows the account its own), ciphertext as stored."""
    return list(
        (
            await conn.execute(
                text("select id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, phone_ct, phone_key_version, erased_at from mpi.patients where account_id = :a order by clinic_id, id"),
                {"a": account_id},
            )
        ).all()
    )


async def consent_history(conn: AsyncConnection, account_id: uuid.UUID) -> list[Any]:
    return list(
        (
            await conn.execute(
                text(
                    "select e.purpose, e.action, e.occurred_at, n.version from iam.consent_events e join iam.consent_notices n on n.id = e.notice_id "
                    "where e.account_id = :a order by e.occurred_at, e.id"
                ),
                {"a": account_id},
            )
        ).all()
    )
