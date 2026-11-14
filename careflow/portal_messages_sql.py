"""Patient messaging, database side (migration 0082; frontend request 29). A patient has no table grant on msg.*: every call here is a definer function that checks the caller itself and refuses with ONE fixed answer (42501) for every reason,
so a thread that is not yours, does not exist, or is hidden by your account stage all look the same. No model is used anywhere in this path; the deterministic rules screen the message in the same transaction (the route does that, using PatientRuleEngine).
No message text is logged or returned from here."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate
from careflow.rules import RedFlagEngine

SETTING: Final = "CAREFLOW_PATIENT_MESSAGING"
SEND_TOPICS: Final = ("care", "appointments")  # `safety` is never patient-sent (spec 17.5)


class Refused(Exception):
    """The database refused the caller or the request. One fixed answer."""


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Off unless the owner sets CAREFLOW_PATIENT_MESSAGING to exactly `on`. Unknown is off."""
    return ((environ if environ is not None else os.environ).get(SETTING) or "").strip().lower() == "on"


class PatientRuleEngine(RedFlagEngine):
    """The same engine, reading the rules through kb.patient_screening_rules (a bound patient of the clinic only), because a patient session cannot read kb.red_flag_rules."""

    RULES_SQL = "select id, clinic_id, rule_code, version_id, status, phrases, patterns, token_sets from kb.patient_screening_rules(:c, :o)"


def _refuse(exc: DBAPIError) -> None:
    if sqlstate(exc) == "42501":
        raise Refused from None


@dataclass(frozen=True)
class OpenedThread:
    thread_id: uuid.UUID
    clinic_id: uuid.UUID
    origin: str
    created: bool


async def open_thread(conn: AsyncConnection, *, clinic_id: uuid.UUID, topic: str, new_thread_id: uuid.UUID, idempotency_key: str) -> OpenedThread:
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select thread_id, clinic_id, origin, created from msg.patient_open_thread(:c, :t, :n, :k)"), {"c": clinic_id, "t": topic, "n": new_thread_id, "k": idempotency_key})).one()
    except DBAPIError as exc:
        _refuse(exc)
        raise
    return OpenedThread(row.thread_id, row.clinic_id, row.origin, row.created)


async def post_message(conn: AsyncConnection, *, thread_id: uuid.UUID, message_id: uuid.UUID, body_ct: bytes, key_version: int, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select message_id, created from msg.patient_post_message(:t, :m, :b, :v, :k)"), {"t": thread_id, "m": message_id, "b": body_ct, "v": key_version, "k": idempotency_key})).one()
    except DBAPIError as exc:
        _refuse(exc)
        raise
    return row.message_id, row.created


@dataclass(frozen=True)
class ThreadRow:
    thread_id: uuid.UUID
    clinic_id: uuid.UUID
    topic: str
    message_count: int
    last_message_at: datetime | None


async def list_threads(conn: AsyncConnection) -> list[ThreadRow]:
    try:
        async with conn.begin_nested():
            rows = (await conn.execute(text("select thread_id, clinic_id, topic, message_count, last_message_at from msg.patient_threads()"))).all()
    except DBAPIError as exc:
        _refuse(exc)
        raise
    return [ThreadRow(r.thread_id, r.clinic_id, r.topic, int(r.message_count), r.last_message_at) for r in rows]


async def thread_messages(conn: AsyncConnection, thread_id: uuid.UUID) -> list[Any]:
    """The raw rows (with ciphertext) of one of the caller's own threads, oldest first; an empty list for a thread that is not theirs."""
    try:
        async with conn.begin_nested():
            return list((await conn.execute(text("select message_id, clinic_id, thread_id, topic, sender_kind, created_at, body_ct, body_key_version, erased from msg.patient_thread_messages(:t)"), {"t": thread_id})).all())
    except DBAPIError as exc:
        _refuse(exc)
        raise
