"""Break-glass access (migration 0058; BUILD-SPEC.md 18.4).

Thin wrappers over the three definer functions. The database decides everything: who may open a grant, for how long, whether a read is still inside the window, and whether a review is by another person.
`refused` is what a 42501 from the database becomes; the route turns it into the one generic 403. Reasons are CODES from a required setting; no free text is ever stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate


class UnknownReason(Exception):
    """The reason code is not on the clinic list (the database answered 22023)."""


class NotConfigured(Exception):
    """The clinic has no break-glass limits row (owner-managed, migration 0066): no grant can be opened (the database answered 55000)."""


class LimitReached(Exception):
    """The person already opened the allowed number of grants for this patient in the last 24 hours (the database answered 54000)."""


class Refused(Exception):
    """The database refused the caller (not allowed, no second factor, window closed, not the holder)."""


@dataclass(frozen=True)
class Opened:
    grant_id: uuid.UUID
    expires_at: datetime
    created: bool


async def open_grant(conn: AsyncConnection, *, clinic_id: uuid.UUID, patient_id: uuid.UUID, reason_code: str, minutes: int | None = None, idempotency_key: str) -> Opened:
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select grant_id, expires_at, created from iam.open_break_glass(:c, :p, :r, cast(:m as integer), :k)"), {"c": clinic_id, "p": patient_id, "r": reason_code, "m": minutes, "k": idempotency_key})).one()
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        if sqlstate(exc) == "22023":
            raise UnknownReason from None
        if sqlstate(exc) == "54000":
            raise LimitReached from None
        if sqlstate(exc) == "55000":
            raise NotConfigured from None
        raise
    return Opened(row.grant_id, row.expires_at, bool(row.created))


async def read_messages(conn: AsyncConnection, *, grant_id: uuid.UUID) -> list[Any] | None:
    """The patient's messages as stored (ciphertext); the caller decrypts them with careflow.messages and owns the text. Every call writes an audit row in the database. A refusal past the identity gate is audited
    too: the database answers status 'refused' instead of raising, and this returns None so the ROUTE can answer 403 WITHOUT raising (a raise would roll the request back and lose the audit row)."""
    try:
        async with conn.begin_nested():
            rows = list((await conn.execute(text("select * from msg.break_glass_messages(:g)"), {"g": grant_id})).all())
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        raise
    if rows and rows[0].status == "refused":
        return None
    return [r for r in rows if r.status == "ok"]


async def review(conn: AsyncConnection, *, grant_id: uuid.UUID, outcome: str) -> str:
    """'reviewed', 'already_reviewed' or 'same_person'."""
    try:
        async with conn.begin_nested():
            return str((await conn.execute(text("select iam.review_break_glass(:g, :o)"), {"g": grant_id, "o": outcome})).scalar())
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        raise


async def reason_codes(conn: AsyncConnection, clinic_id: uuid.UUID) -> list[str]:
    """The clinic's break-glass reason codes (owner-kept list), through the definer function that checks the caller."""
    try:
        async with conn.begin_nested():
            return [r.code for r in (await conn.execute(text("select code from iam.break_glass_reason_codes(:c)"), {"c": clinic_id})).all()]
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        raise


async def my_open_grants(conn: AsyncConnection, *, clinic_id: uuid.UUID, account_id: uuid.UUID) -> list[Any]:
    """The caller's own grants that are neither expired nor reviewed (row-level security shows a holder their own)."""
    return list(
        (
            await conn.execute(
                text(
                    "select id, patient_id, reason_code, opened_at, expires_at from iam.break_glass_grants "
                    "where clinic_id = :c and opened_by = :a and reviewed_at is null and expires_at > clock_timestamp() order by expires_at, id"
                ),
                {"c": clinic_id, "a": account_id},
            )
        ).all()
    )


async def grant_clinic(conn: AsyncConnection, grant_id: uuid.UUID) -> uuid.UUID | None:
    """The clinic of a grant you may see (row-level security decides), or None."""
    row = (await conn.execute(text("select clinic_id from iam.break_glass_grants where id = :g"), {"g": grant_id})).first()
    return row.clinic_id if row else None


async def list_grants(conn: AsyncConnection, *, clinic_id: uuid.UUID, unreviewed_only: bool, limit: int) -> list[Any]:
    return list(
        (
            await conn.execute(
                text(
                    "select id, patient_id, opened_by, reason_code, opened_at, expires_at, reviewed_by, reviewed_at, review_outcome from iam.break_glass_grants "
                    "where clinic_id = :c and (not :u or reviewed_at is null) order by opened_at desc, id limit :n"
                ),
                {"c": clinic_id, "u": unreviewed_only, "n": limit},
            )
        ).all()
    )
