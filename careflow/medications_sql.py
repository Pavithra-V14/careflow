"""Medication statements (migration 0074; BUILD-SPEC.md 9.5). MECHANISM ONLY.

# CLINICAL-PLACEHOLDER: needs clinician sign-off

A row is a CODE, a verified flag and times: no dose, route, frequency or free text. The allowed codes are the clinic's required setting `CAREFLOW_MEDICATION_CODES` (comma-separated, no default). With the
setting unset the route answers 503: unknown is not a value, and the assistant invents no code. A stop or a correction is a new row that supersedes the old one (append-only).
"""

import os
import uuid
from collections.abc import Mapping
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate

SETTING: Final = "CAREFLOW_MEDICATION_CODES"


class Refused(Exception):
    """The patient or the superseded row is not visible, the key belongs to another row, or the row is already superseded."""


def allowed_codes(environ: Mapping[str, str] | None = None) -> frozenset[str] | None:
    """The clinic's vocabulary, or None when the setting is unset or empty (not configured)."""
    raw = (environ if environ is not None else os.environ).get(SETTING, "")
    codes = frozenset(c.strip() for c in raw.split(",") if c.strip())
    return codes or None


async def record_medication(
    conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID, code: str, verified: bool, valid_from: Any, valid_to: Any, supersedes_id: uuid.UUID | None,
    recorded_by: uuid.UUID, idempotency_key: str,
) -> tuple[uuid.UUID, bool]:
    """(id, created). A replay of the same key for the same patient returns the same row."""
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text(
                        "insert into clinical.medications (clinic_id, origin, patient_id, code, verified, valid_from, valid_to, supersedes_id, recorded_by, idempotency_key) "
                        "values (:c, :o, :p, :code, :v, :vf, :vt, :s, :by, :k) returning id"
                    ),
                    {"c": clinic_id, "o": origin, "p": patient_id, "code": code, "v": verified, "vf": valid_from, "vt": valid_to, "s": supersedes_id, "by": recorded_by, "k": idempotency_key},
                )
            ).one()
            return row.id, True
    except IntegrityError as exc:
        state = sqlstate(exc)
        if state in ("23001", "23503", "23514"):  # 23001: a patient who is erased
            raise Refused from None
        if state != "23505":
            raise
    row = (
        await conn.execute(
            text(
                "select id from clinical.medications where clinic_id = :c and idempotency_key = :k and patient_id = :p and code = :code and verified = :v "
                "and valid_from = :vf and valid_to is not distinct from :vt and supersedes_id is not distinct from :s"
            ),
            {"c": clinic_id, "k": idempotency_key, "p": patient_id, "code": code, "v": verified, "vf": valid_from, "vt": valid_to, "s": supersedes_id},
        )
    ).first()
    if row is None:  # a different body under the same key is a refusal, never a silent "recorded"
        raise Refused  # the key is held by another row, or this row is superseded already
    return row.id, False


async def list_for_patient(conn: AsyncConnection, *, clinic_id: uuid.UUID, patient_id: uuid.UUID, limit: int) -> list[Any]:
    """The patient's statements, newest first, with whether each has been superseded."""
    return list(
        (
            await conn.execute(
                text(
                    "select m.id, m.code, m.verified, m.valid_from, m.valid_to, m.recorded_at, m.supersedes_id, "
                    "exists (select 1 from clinical.medications n where n.supersedes_id = m.id) as superseded "
                    "from clinical.medications m where m.clinic_id = :c and m.patient_id = :p order by m.valid_from desc, m.recorded_at desc, m.id limit :n"
                ),
                {"c": clinic_id, "p": patient_id, "n": limit},
            )
        ).all()
    )
