"""Patient booking, database side (migration 0083; frontend request 28). A patient has no table grant on sched.*: every call is a definer function that checks the caller itself. Refusals are mapped to small exceptions:
42501 Refused (one answer for missing, not yours, not offered, another clinic's), 55006 SlotNotOpen, 55000 NotCancellable, 54000 CapReached, 23505 KeyReused. No clinical content is involved; which visit types a patient may book is the
owner's data (sched.patient_bookable_visit_types)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate

SETTING: Final = "CAREFLOW_PATIENT_BOOKING"


class Refused(Exception):
    """The database refused the caller or the request. One fixed answer."""


class SlotNotOpen(Exception):
    """The slot is taken, held for someone else, or not (or no longer) open."""


class NotCancellable(Exception):
    """The booking is already cancelled (by another request) or its slot has started."""


class CapReached(Exception):
    """The patient already has the owner's maximum of confirmed future bookings for this visit type."""


class KeyReused(Exception):
    """The idempotency key was used for a different request."""


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    """Off unless the owner sets CAREFLOW_PATIENT_BOOKING to exactly `on`. Unknown is off."""
    return ((environ if environ is not None else os.environ).get(SETTING) or "").strip().lower() == "on"


def _map(exc: DBAPIError) -> None:
    code = sqlstate(exc)
    if code == "42501":
        raise Refused from None
    if code == "55006":
        raise SlotNotOpen from None
    if code == "55000":
        raise NotCancellable from None
    if code == "54000":
        raise CapReached from None
    if code == "23505":
        raise KeyReused from None


@dataclass(frozen=True)
class OpenSlot:
    slot_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    visit_type_id: uuid.UUID
    visit_type_name: str
    location_name: str


async def open_slots(conn: AsyncConnection, clinic_id: uuid.UUID, limit: int) -> list[OpenSlot]:
    try:
        async with conn.begin_nested():
            rows = (await conn.execute(text("select slot_id, starts_at, ends_at, visit_type_id, visit_type_name, location_name from sched.patient_open_slots(:c, :n)"), {"c": clinic_id, "n": limit})).all()
    except DBAPIError as exc:
        _map(exc)
        raise
    return [OpenSlot(r.slot_id, r.starts_at, r.ends_at, r.visit_type_id, r.visit_type_name, r.location_name) for r in rows]


async def book(conn: AsyncConnection, slot_id: uuid.UUID, idempotency_key: str) -> tuple[uuid.UUID, uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select o_booking_id, o_slot_id, o_created from sched.patient_book_slot(:s, :k)"), {"s": slot_id, "k": idempotency_key})).one()
    except DBAPIError as exc:
        _map(exc)
        raise
    return row.o_booking_id, row.o_slot_id, row.o_created


@dataclass(frozen=True)
class BookingRow:
    booking_id: uuid.UUID
    clinic_id: uuid.UUID
    slot_id: uuid.UUID
    status: str
    starts_at: datetime
    ends_at: datetime
    visit_type_name: str
    location_name: str
    cancellable: bool


async def my_bookings(conn: AsyncConnection) -> list[BookingRow]:
    try:
        async with conn.begin_nested():
            rows = (await conn.execute(text("select booking_id, clinic_id, slot_id, status, starts_at, ends_at, visit_type_name, location_name, cancellable from sched.patient_bookings()"))).all()
    except DBAPIError as exc:
        _map(exc)
        raise
    return [BookingRow(r.booking_id, r.clinic_id, r.slot_id, r.status, r.starts_at, r.ends_at, r.visit_type_name, r.location_name, bool(r.cancellable)) for r in rows]


async def cancel(conn: AsyncConnection, booking_id: uuid.UUID, idempotency_key: str) -> bool:
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select o_booking_id, o_changed from sched.patient_cancel_booking(:b, :k)"), {"b": booking_id, "k": idempotency_key})).one()
    except DBAPIError as exc:
        _map(exc)
        raise
    return bool(row.o_changed)
