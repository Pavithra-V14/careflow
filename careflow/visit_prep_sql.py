"""Patient visit preparation (migration 0089; owner instruction 2026-11-14, deferred item 30). MECHANISM ONLY, OFF unless `CAREFLOW_PATIENT_VISIT_PREP` is exactly `on`.

A patient sees what to prepare for their OWN upcoming visit and marks each item done or not done; a nurse or doctor sees the same, read-only. THE ITEMS ARE CLINICIAN CONTENT: the checklist of the required setting
`CAREFLOW_VISIT_READINESS_CONTENT` (the one the Visit Readiness agent uses), chosen by the visit's type name. This module invents no item and no wording of its own except the one fixed sentence below; with nothing
configured for the visit type the patient is told `NOTHING_TO_PREPARE`, never an empty list that could be read as "all done". The database stores only the patient's marks (a closed item id, done or not, who, when).
"""

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
from careflow.visit_readiness_content import VisitContent

SETTING: Final = "CAREFLOW_PATIENT_VISIT_PREP"
NOTHING_TO_PREPARE: Final = "Nothing to prepare for this visit."  # the fixed wording when no checklist is configured for the visit type (a statement of what the clinic has configured, not a clinical statement)


class Refused(Exception):
    """Not the caller's visit, not confirmed, already started, or the caller's role may not: one answer for every reason."""


class KeyReused(Exception):
    """The idempotency key was used for a different mark."""


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    return ((environ if environ is not None else os.environ).get(SETTING) or "").strip().lower() == "on"


@dataclass(frozen=True)
class PrepState:
    visit_type: str
    starts_at: datetime | None
    marks: Mapping[str, tuple[bool, datetime]]  # item id -> (done, when marked): only items that have a mark


@dataclass(frozen=True)
class PrepItem:
    item_id: str
    text: str
    done: bool | None  # None = not marked yet (unknown is not "not done")
    marked_at: datetime | None


def checklist_for(content: VisitContent | None, visit_type: str) -> list[tuple[str, str]]:
    """The configured (item id, exact wording) pairs for the visit type; empty when nothing is configured."""
    entry = content.for_visit_type(visit_type) if content is not None else None
    return [(i.item_id, i.text) for i in entry.checklist.items] if entry is not None else []


def items_for(content: VisitContent | None, state: PrepState) -> list[PrepItem]:
    out = []
    for item_id, wording in checklist_for(content, state.visit_type):
        mark = state.marks.get(item_id)
        out.append(PrepItem(item_id, wording, mark[0] if mark else None, mark[1] if mark else None))
    return out


def _state(rows: list[Any], *, with_start: bool) -> PrepState:
    marks = {r.o_item: (bool(r.o_done), r.o_marked_at) for r in rows if r.o_item is not None}
    return PrepState(rows[0].o_visit_type, rows[0].o_starts_at if with_start else None, marks)


async def patient_prep(conn: AsyncConnection, booking_id: uuid.UUID) -> PrepState:
    try:
        async with conn.begin_nested():
            rows = (await conn.execute(text("select o_visit_type, o_starts_at, o_item, o_done, o_marked_at from sched.patient_visit_prep(:b)"), {"b": booking_id})).all()
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused("visit refused") from None
        raise
    return _state(rows, with_start=True)


async def staff_prep(conn: AsyncConnection, booking_id: uuid.UUID) -> PrepState:
    try:
        async with conn.begin_nested():
            rows = (await conn.execute(text("select o_visit_type, o_item, o_done, o_marked_at from sched.staff_visit_prep(:b)"), {"b": booking_id})).all()
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise Refused("visit refused") from None
        raise
    marks = {r.o_item: (bool(r.o_done), r.o_marked_at) for r in rows if r.o_item is not None}
    return PrepState(rows[0].o_visit_type, None, marks)


async def mark(conn: AsyncConnection, *, booking_id: uuid.UUID, item: str, done: bool, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    """(mark id, created). A replay of the same key and mark returns the first row."""
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select o_mark_id, o_created from sched.patient_mark_prep(:b, :i, :d, :k)"), {"b": booking_id, "i": item, "d": done, "k": idempotency_key})).one()
    except DBAPIError as exc:
        state = sqlstate(exc)
        if state == "42501":
            raise Refused("mark refused") from None
        if state == "23505":
            raise KeyReused("the key was used for a different mark") from None
        raise
    return row.o_mark_id, bool(row.o_created)
