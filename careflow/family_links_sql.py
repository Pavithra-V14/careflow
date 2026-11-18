"""Family links (migration 0091; owner instruction 2026-11-14, deferred item 21). MECHANISM ONLY, OFF unless `CAREFLOW_FAMILY_LINKS` is exactly `on`, and DENY BY DEFAULT.

A patient account asks to be linked as the guardian of a child or the carer of an adult at a clinic; a FRONT-DESK person verifies the link (choosing the dependant's record, with the dependant's age checked against the
clinic's rule and the linked person's consent recorded); the patient can revoke it. A VERIFIED LINK GRANTS NOTHING in this build: no route lets one account read or act for another.

THE RULES ARE COUNSEL'S. The age limits and the consent requirement live in `iam.family_link_rules`, an owner-managed table that SHIPS EMPTY (no default, no seed, no grant). With no row for a clinic and link type nothing can be
requested or verified and the answer is `not_configured`, in fixed wording. The dependant's age is computed here from the stored, encrypted date of birth (never printed or returned) and passed to the database, which
checks it against the rule.
"""

import os
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate
from careflow.crypto import CryptoError, DemographicVault

SETTING: Final = "CAREFLOW_FAMILY_LINKS"
NOT_CONFIGURED: Final = "family links are not set up: the clinic has not entered its rules"  # the fixed wording while counsel's values are not entered


class Refused(Exception):
    """Not the caller's, wrong role, no second factor, another clinic's record, or malformed: one answer for every reason."""


class Conflict(Exception):
    """An idempotency key reused for a different request."""


class CannotAge(Exception):
    """The dependant's date of birth cannot be read (fixed words; the value is never echoed)."""


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    return ((environ if environ is not None else os.environ).get(SETTING) or "").strip().lower() == "on"


@dataclass(frozen=True)
class MyLink:
    link_id: uuid.UUID
    clinic_id: uuid.UUID
    link_type: str
    status: str
    created_at: datetime


@dataclass(frozen=True)
class StaffLink:
    link_id: uuid.UUID
    link_type: str
    status: str
    requester_patient_id: uuid.UUID
    dependant_patient_id: uuid.UUID | None
    created_at: datetime


async def _rows(conn: AsyncConnection, sql: str, params: dict) -> list:
    try:
        async with conn.begin_nested():
            return list((await conn.execute(text(sql), params)).all())
    except DBAPIError as exc:
        state = sqlstate(exc)
        if state == "42501":
            raise Refused("refused") from None
        if state == "23505":
            raise Conflict("conflict") from None
        raise


async def request_link(conn: AsyncConnection, *, clinic_id: uuid.UUID, link_type: str, idempotency_key: str) -> tuple[str, uuid.UUID | None]:
    row = (await _rows(conn, "select o_result, o_link_id from iam.family_link_request(:c, :t, :k)", {"c": clinic_id, "t": link_type, "k": idempotency_key}))[0]
    return row.o_result, row.o_link_id


async def my_links(conn: AsyncConnection) -> list[MyLink]:
    return [MyLink(r.o_link_id, r.o_clinic_id, r.o_link_type, r.o_status, r.o_created_at) for r in await _rows(conn, "select o_link_id, o_clinic_id, o_link_type, o_status, o_created_at from iam.patient_family_links()", {})]


async def revoke(conn: AsyncConnection, *, link_id: uuid.UUID, idempotency_key: str) -> str:
    return (await _rows(conn, "select o_result from iam.family_link_revoke(:l, :k)", {"l": link_id, "k": idempotency_key}))[0].o_result


async def staff_links(conn: AsyncConnection, clinic_id: uuid.UUID) -> list[StaffLink]:
    rows = await _rows(conn, "select o_link_id, o_link_type, o_status, o_requester_patient_id, o_dependant_patient_id, o_created_at from iam.staff_family_links(:c)", {"c": clinic_id})
    return [StaffLink(r.o_link_id, r.o_link_type, r.o_status, r.o_requester_patient_id, r.o_dependant_patient_id, r.o_created_at) for r in rows]


def age_years(dob_iso: str, today: date) -> int:
    """Whole years from an ISO date of birth to `today` (a birthday not yet reached this year does not count). Raises CannotAge for anything that is not a past ISO date."""
    try:
        born = date.fromisoformat(dob_iso)
    except ValueError:
        raise CannotAge("the date of birth cannot be read") from None
    if born > today:
        raise CannotAge("the date of birth cannot be read")
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


async def dependant_age(conn: AsyncConnection, vault: DemographicVault, *, clinic_id: uuid.UUID, patient_id: uuid.UUID, today: date) -> int:
    """The dependant's age in whole years, from the stored encrypted date of birth (read as the signed-in front desk through row-level security). Refused if the record is not visible in this clinic."""
    row = (await conn.execute(text("select dob_ct, dob_key_version from mpi.patients where id = :p and clinic_id = :c and erased_at is null"), {"p": patient_id, "c": clinic_id})).first()
    if row is None or row.dob_ct is None:
        raise Refused("refused")
    try:
        dob = vault.decrypt(bytes(row.dob_ct), field="dob", key_version=row.dob_key_version)
    except (CryptoError, ValueError, TypeError):
        raise CannotAge("the date of birth cannot be read") from None
    return age_years(dob, today)


async def decide(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, link_id: uuid.UUID, decision: str, dependant_patient_id: uuid.UUID | None, age: int | None, consent: bool | None, reason: str | None, idempotency_key: str
) -> str:
    return (await _rows(
        conn, "select o_result from iam.family_link_decide(:c, :l, :d, :p, :a, :s, :r, :k)",
        {"c": clinic_id, "l": link_id, "d": decision, "p": dependant_patient_id, "a": age, "s": consent, "r": reason, "k": idempotency_key},
    ))[0].o_result
