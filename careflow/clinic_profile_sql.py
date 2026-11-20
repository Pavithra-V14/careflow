"""The hospital's public profile (migration 0100): what the signed-out portal home page shows. Read through ONE public definer function (the published clinic only, nothing for an unpublished one) and written through
ONE admin definer function (aal2, idempotency key, audit). Every text field is nullable: null means "not supplied", never an empty string. Nothing here invents content.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate


class Refused(Exception):
    """Not an admin of that clinic, no second factor, or a field that is not allowed: one answer for every reason."""


class KeyReused(Exception):
    """An idempotency key reused with other content."""


@dataclass(frozen=True)
class PublicProfile:
    name: str
    about: str | None
    departments: list[dict[str, Any]] | None
    address: str | None
    opening_hours: str | None
    phone: str | None
    privacy_promise: str | None
    placeholder: bool
    has_bookable_type: bool
    has_family_rule: bool


async def read_public(conn: AsyncConnection) -> PublicProfile | None:
    row = (
        await conn.execute(
            text("select o_name, o_about, o_departments, o_address, o_opening_hours, o_phone, o_privacy_promise, o_placeholder, o_has_bookable_type, o_has_family_rule from iam.public_clinic_profile()")
        )
    ).first()
    if row is None:
        return None
    return PublicProfile(
        row.o_name, row.o_about, row.o_departments, row.o_address, row.o_opening_hours, row.o_phone, row.o_privacy_promise, bool(row.o_placeholder), bool(row.o_has_bookable_type), bool(row.o_has_family_rule)
    )


async def set_profile(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, about: str | None, departments: list[dict[str, Any]] | None, address: str | None, opening_hours: str | None, phone: str | None,
    privacy_promise: str | None, idempotency_key: str,
) -> str:
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text("select iam.set_clinic_public_profile(:c, :a, cast(:d as jsonb), :ad, :o, :p, :pp, :k) as r"),
                    {"c": clinic_id, "a": about, "d": None if departments is None else json.dumps(departments), "ad": address, "o": opening_hours, "p": phone, "pp": privacy_promise, "k": idempotency_key},
                )
            ).one()
    except DBAPIError as exc:
        state = sqlstate(exc)
        if state == "42501":
            raise Refused("refused") from None
        if state == "23505":
            raise KeyReused("reused") from None
        raise
    return str(row.r)
