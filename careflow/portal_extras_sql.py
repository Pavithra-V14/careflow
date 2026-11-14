"""Patient profile, in-app notifications, patient requests and the public grievance writer (migration 0080; frontend requests 20, 22, 24, 26). MECHANISM ONLY.

Every write goes through a database function (the application role has no table grant). The word lists are REQUIRED SETTINGS with no defaults:
  * `CAREFLOW_SEX_WORDS`: the closed list a patient may pick a sex from (comma-separated lower-case words). Unset means the field cannot be set (an answer is refused, never guessed). The words are the clinic's and counsel's, not this file's.
  * `CAREFLOW_PUBLIC_GRIEVANCE_IP_PER_HOUR`: how many public grievances one network address may send in an hour (in memory, per process). Unset means the public route answers 503: no limit, no route.
"""

import os
import re
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable, Mapping
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gap_sql import sqlstate

SEX_SETTING: Final = "CAREFLOW_SEX_WORDS"
PUBLIC_IP_SETTING: Final = "CAREFLOW_PUBLIC_GRIEVANCE_IP_PER_HOUR"
PUBLIC_READ_SETTING: Final = "CAREFLOW_PUBLIC_READ_IP_PER_MINUTE"  # the per-address limit on the public READ routes (the category list); required, no default
LANGUAGES: Final = ("en", "ta")  # spec 19.4: English or Tamil
_WORD = re.compile(r"^[a-z][a-z_]{0,19}$")


class Refused(Exception):
    """The database refused the caller or the request. One fixed answer."""


class LimitReached(Exception):
    """The owner-managed hourly cap of public grievances is reached."""


class NothingPublished(Exception):
    """The owner has published no clinic."""


def sex_words(environ: Mapping[str, str] | None = None) -> frozenset[str] | None:
    """The configured closed list, or None when unset or empty. A word that is not lower-case letters and underscores makes the WHOLE list unusable (None), never a silent drop."""
    raw = ((environ if environ is not None else os.environ).get(SEX_SETTING) or "").strip()
    if not raw:
        return None
    words = [w.strip() for w in raw.split(",") if w.strip()]
    if not words or any(not _WORD.match(w) for w in words):
        return None
    return frozenset(words)


def public_ip_limit(environ: Mapping[str, str] | None = None) -> int | None:
    raw = ((environ if environ is not None else os.environ).get(PUBLIC_IP_SETTING) or "").strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 1000 else None


def public_read_limit(environ: Mapping[str, str] | None = None) -> int | None:
    raw = ((environ if environ is not None else os.environ).get(PUBLIC_READ_SETTING) or "").strip()
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= 1000 else None


class HourlyLimiter:
    """At most `limit` events per key in any `window` seconds (3600 unless told otherwise; a sliding window, in memory, per process)."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic, window: float = 3600.0) -> None:
        self._limit, self._clock, self._window = limit, clock, window
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, window = self._clock(), self._events[key]
        while window and now - window[0] >= self._window:
            window.popleft()
        if len(window) >= self._limit:
            return False
        window.append(now)
        return True


def _refuse(exc: DBAPIError) -> None:
    if sqlstate(exc) == "42501":
        raise Refused from None


async def get_profile(conn: AsyncConnection, account_id: uuid.UUID) -> Any:
    return (await conn.execute(text("select sex, preferred_language from iam.patient_profiles where account_id = :a"), {"a": account_id})).first()


async def set_profile(conn: AsyncConnection, *, sex: str | None, language: str | None) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(text("select iam.set_patient_profile(:s, :l)"), {"s": sex, "l": language})
    except DBAPIError as exc:
        _refuse(exc)
        raise


async def list_notifications(conn: AsyncConnection, account_id: uuid.UUID, limit: int) -> list[Any]:
    return list((await conn.execute(text("select id, kind, ref, created_at, read_at from iam.notifications where account_id = :a order by created_at desc, id limit :n"), {"a": account_id, "n": limit})).all())


async def mark_read(conn: AsyncConnection, notification_id: uuid.UUID) -> bool:
    try:
        async with conn.begin_nested():
            return bool((await conn.execute(text("select iam.mark_notification_read(:i)"), {"i": notification_id})).scalar())
    except DBAPIError as exc:
        _refuse(exc)
        raise


async def notify_patient(conn: AsyncConnection, *, clinic_id: uuid.UUID, patient_id: uuid.UUID, kind: str, ref: str | None, key: str) -> bool:
    """Tell a patient's bound account something happened (a closed kind and an opaque reference). False when the patient has no account or it was told already."""
    try:
        async with conn.begin_nested():
            return bool((await conn.execute(text("select iam.notify_patient(:c, :p, :k, :r, :i)"), {"c": clinic_id, "p": patient_id, "k": kind, "r": ref, "i": key})).scalar())
    except DBAPIError as exc:
        _refuse(exc)
        raise


async def file_request(conn: AsyncConnection, *, clinic_id: uuid.UUID, kind: str, field: str | None, request_id: uuid.UUID, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            row = (await conn.execute(text("select request_id, created from iam.file_patient_request(:c, :k, :f, :i, :key)"), {"c": clinic_id, "k": kind, "f": field, "i": request_id, "key": idempotency_key})).one()
    except DBAPIError as exc:
        _refuse(exc)
        raise
    return row.request_id, row.created


async def list_own_requests(conn: AsyncConnection, account_id: uuid.UUID, limit: int) -> list[Any]:
    return list((await conn.execute(text("select id, clinic_id, kind, field, status, created_at, updated_at from iam.patient_requests where account_id = :a order by created_at desc, id limit :n"), {"a": account_id, "n": limit})).all())


async def list_clinic_requests(conn: AsyncConnection, clinic_id: uuid.UUID, limit: int) -> list[Any]:
    return list(
        (
            await conn.execute(
                text("select id, clinic_id, patient_id, kind, field, status, erasure_request_id, created_at, updated_at from iam.patient_requests where clinic_id = :c order by created_at, id limit :n"),
                {"c": clinic_id, "n": limit},
            )
        ).all()
    )


async def request_clinic(conn: AsyncConnection, request_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
    row = (await conn.execute(text("select clinic_id, patient_id from iam.patient_requests where id = :i"), {"i": request_id})).first()
    return None if row is None else (row.clinic_id, row.patient_id)


async def update_request(conn: AsyncConnection, *, request_id: uuid.UUID, status: str, erasure_request_id: uuid.UUID | None) -> str:
    try:
        async with conn.begin_nested():
            return str((await conn.execute(text("select iam.update_patient_request(:i, :s, :e)"), {"i": request_id, "s": status, "e": erasure_request_id})).scalar())
    except DBAPIError as exc:
        _refuse(exc)
        raise


async def file_public_grievance(conn: AsyncConnection, *, category: str, body_ct: bytes, key_version: int, grievance_id: uuid.UUID, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text("select grievance_id, created from iam.file_public_grievance(:c, :b, cast(:v as smallint), :g, :k)"),
                    {"c": category, "b": body_ct, "v": key_version, "g": grievance_id, "k": idempotency_key},
                )
            ).one()
    except DBAPIError as exc:
        state = sqlstate(exc)
        if state == "42501":
            raise Refused from None
        if state == "54000":
            raise LimitReached from None
        if state == "P0002":
            raise NothingPublished from None
        raise
    return row.grievance_id, row.created
