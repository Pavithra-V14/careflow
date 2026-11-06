"""The SQL halves of migration 0049 (patient registration, consent, activation, claim), on the CALLER's own transaction under row-level security. Every state change is a database function that checks the signed-in
caller itself; the application passes no account id (the application role cannot call auth.uid() by name, and a function that took an id would have to trust it).

The claim tokens are computed HERE from the verified phone claim and the submitted name and date of birth with the same keyed hashes ingestion uses (careflow.mpi / careflow.crypto), so the database compares bytes
and never sees a name, a date of birth or a phone number.
"""

import uuid
from typing import Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate
from careflow.crypto import DemographicVault
from careflow.mpi import (
    NAME_DOB,
    PHONE,
    IngestionError,
    canonicalize_phone,
    parse_date_of_birth,
)

PURPOSES: Final = ("care_delivery", "ai_processing", "service_improvement")


class PortalRefused(Exception):
    """The database refused the caller (42501): not a patient account, wrong state, or no such clinic. One answer for all of them."""


async def _call(conn: AsyncConnection, sql: str, params: dict) -> object:
    try:
        async with conn.begin_nested():
            return (await conn.execute(text(sql), params)).scalar()
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise PortalRefused from None
        raise


async def register(conn: AsyncConnection, origin: str) -> str:
    return str(await _call(conn, "select iam.register_patient_account(:o)", {"o": origin}))


async def activate(conn: AsyncConnection) -> str:
    return str(await _call(conn, "select iam.activate_patient_account()", {}))


async def account_state(conn: AsyncConnection, account_id: uuid.UUID) -> dict:
    """Status, whether the account is bound to a patient record, and the newest decision per purpose (None = never decided)."""
    status = (await conn.execute(text("select status from iam.accounts where id = :a"), {"a": account_id})).scalar()
    bound = (await conn.execute(text("select 1 from mpi.patients where account_id = :a"), {"a": account_id})).first() is not None
    return {"status": status, "bound": bound, "consents": await latest_decisions(conn, account_id)}


async def latest_decisions(conn: AsyncConnection, account_id: uuid.UUID) -> dict[str, bool | None]:
    rows = (
        await conn.execute(
            text(
                "select distinct on (e.purpose) e.purpose, e.action, n.version = (select max(version) from iam.consent_notices x where x.purpose = e.purpose) as current "
                "from iam.consent_events e join iam.consent_notices n on n.id = e.notice_id where e.account_id = :a order by e.purpose, e.occurred_at desc, e.id desc"
            ),
            {"a": account_id},
        )
    ).all()
    decided: dict[str, bool | None] = dict.fromkeys(PURPOSES)
    for r in rows:
        decided[r.purpose] = (r.action == "granted") if r.current else None  # a decision about an OLD notice text is no decision about the current one
    return decided


async def current_notices(conn: AsyncConnection) -> list:
    return list(
        (
            await conn.execute(
                text("select distinct on (purpose) id, purpose, version, body from iam.consent_notices order by purpose, version desc")
            )
        ).all()
    )


async def record_consent(conn: AsyncConnection, *, account_id: uuid.UUID, purpose: str, notice_id: uuid.UUID, action: str, idempotency_key: str) -> bool:
    """True when a new event was written; False for a repeat of the key. Raises PortalRefused when the notice is not the current one for the purpose, or the account may not consent."""
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("insert into iam.consent_events (account_id, purpose, notice_id, action, idempotency_key) values (:a, :p, :n, :x, :k)"),
                {"a": account_id, "p": purpose, "n": notice_id, "x": action, "k": idempotency_key},
            )
        return True
    except IntegrityError as exc:
        state = sqlstate(exc)
        if state == "23505":
            return False
        if state == "23503":
            raise PortalRefused from None  # no such notice for that purpose
        raise
    except DBAPIError as exc:
        if sqlstate(exc) == "42501":
            raise PortalRefused from None
        raise


def claim_tokens(vault: DemographicVault, *, phone: str, name: str, dob: str, key_version: int) -> tuple[bytes, bytes]:
    """(phone token, name+date-of-birth token) exactly as ingestion derives them. Raises ValueError for a phone that cannot be canonical or a date that is not a real past date."""
    try:
        canonical = canonicalize_phone(phone)
        dob_iso = parse_date_of_birth(dob).isoformat()
    except (IngestionError, TypeError):
        raise ValueError("bad phone or date of birth") from None
    clean_name = " ".join(name.split())
    if not clean_name:
        raise ValueError("no name")
    return vault.identity_token(PHONE, canonical, key_version=key_version), vault.identity_token(NAME_DOB, f"{clean_name} {dob_iso}", key_version=key_version)


async def claim(conn: AsyncConnection, *, clinic_id: uuid.UUID, phone_token: bytes, name_dob_token: bytes, key_version: int) -> str:
    return str(
        await _call(
            conn,
            "select mpi.claim_patient_record(:c, :p, :n, cast(:v as smallint))",
            {"c": clinic_id, "p": phone_token, "n": name_dob_token, "v": key_version},
        )
    )
