"""Clinical notes (migration 0061; BUILD-SPEC.md 9.6): append-only, a correction is an addendum. The body is stored encrypted (careflow.messages.encrypt_note_text) and returned as stored; the caller decrypts it
and owns the text. A plain insert in a savepoint and a catch of 23505: ON CONFLICT would also be checked against the select policy."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate


class Refused(Exception):
    """The note cannot be written: the patient or the note it adds to is not in this clinic, or the database refused the caller."""


async def write_note(
    conn: AsyncConnection, *, note_id: uuid.UUID, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID, author_id: uuid.UUID, parent_id: uuid.UUID | None, body_ct: bytes, key_version: int, idempotency_key: str
) -> tuple[uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "insert into clinical.notes (id, clinic_id, origin, patient_id, author_id, parent_id, body_ct, body_key_version, idempotency_key) "
                    "values (:i, :c, :o, :p, :a, :par, :ct, cast(:kv as smallint), :k)"
                ),
                {"i": note_id, "c": clinic_id, "o": origin, "p": patient_id, "a": author_id, "par": parent_id, "ct": body_ct, "kv": key_version, "k": idempotency_key},
            )
        return note_id, True
    except IntegrityError as exc:
        state = sqlstate(exc)
        if state in ("23503", "23514"):
            raise Refused from None
        if state != "23505":
            raise
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused from None
        raise
    row = (await conn.execute(text("select id from clinical.notes where clinic_id = :c and idempotency_key = :k and patient_id = :p and author_id = :a"), {"c": clinic_id, "k": idempotency_key, "p": patient_id, "a": author_id})).first()
    if row is None:
        raise Refused  # the key is held by another note
    return row.id, False


async def list_notes(conn: AsyncConnection, *, clinic_id: uuid.UUID, patient_id: uuid.UUID, limit: int) -> list[Any]:
    return list(
        (
            await conn.execute(
                text("select id, parent_id, author_id, created_at, body_ct, body_key_version, erased_at from clinical.notes where clinic_id = :c and patient_id = :p order by created_at, id limit :n"),
                {"c": clinic_id, "p": patient_id, "n": limit},
            )
        ).all()
    )
