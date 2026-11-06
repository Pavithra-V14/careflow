"""The SQL halves of the Care Gaps panel (careflow/care_gaps.py; migration 0044). Each source runs on the CALLER's own transaction under row-level security; no policy is decided here and nothing is written
except the barrier record/resolve helpers (the only application writes the migration allows).

* `RecurrenceSource`: open episodes' recurrence rules whose due date plus grace has passed. A rule whose `last_done_on` is unknown (NULL) is NOT on the panel as overdue and NOT silently dropped: the count of
  such rules is kept in `unknown_dates` and shown (unknown is not a value).
* `BarrierSource`: social/resource barriers still open (transport, cost, childcare, other). A scheduling conflict or "forgot" is resolved by rebooking/reminders and is not a care gap.
* `SampleSource`: auto-approved decisions sampled for after-the-fact review and not yet re-checked.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.care_gaps import GapItem, RecurrenceRule, is_overdue, next_due

SOCIAL_BARRIERS = ("transport", "cost", "childcare", "other")


def sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", exc)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if code is None:
        cause = getattr(orig, "__cause__", None)
        code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    return str(code) if code else None


def _as_dt(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


class RecurrenceSource:
    source = "overdue_recurrence"

    def __init__(self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str) -> None:
        self._conn, self._clinic, self._origin = conn, clinic_id, origin
        self.unknown_dates = 0

    _SELECT = (
        "select er.id, er.last_done_on, r.interval_days, r.annual_month, r.overdue_after_days, current_date as today "
        "from ops.care_episode_rules er join ops.care_recurrence_rules r on r.id = er.rule_id and r.clinic_id = er.clinic_id "
        "join ops.care_episodes e on e.id = er.episode_id and e.clinic_id = er.clinic_id "
        "where er.clinic_id = :c and er.origin = :o and e.closed_on is null"
    )

    def _item(self, row) -> GapItem | None:
        rule = RecurrenceRule(str(row.id), int(row.overdue_after_days), row.interval_days, row.annual_month)
        verdict = is_overdue(rule, row.last_done_on, row.today)
        if verdict is None:
            return None
        due = next_due(rule, row.last_done_on)
        if verdict is not True or due is None:
            return None
        return GapItem(self.source, str(row.id), _as_dt(due + timedelta(days=rule.overdue_after_days)))

    async def open_items(self) -> list[GapItem]:
        rows = (await self._conn.execute(text(self._SELECT + " order by er.id limit 2000"), {"c": self._clinic, "o": self._origin})).all()
        self.unknown_dates = sum(1 for r in rows if r.last_done_on is None)
        return [i for i in (self._item(r) for r in rows) if i is not None]

    async def resolve(self, item_id: str) -> GapItem | None:
        try:
            ident = uuid.UUID(item_id)
        except ValueError:
            return None
        row = (await self._conn.execute(text(self._SELECT + " and er.id = :i"), {"c": self._clinic, "o": self._origin, "i": ident})).first()
        return self._item(row) if row is not None else None


class BarrierSource:
    source = "unresolved_barrier"

    def __init__(self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str) -> None:
        self._conn, self._clinic, self._origin = conn, clinic_id, origin

    async def open_items(self) -> list[GapItem]:
        rows = (
            await self._conn.execute(
                text("select id, opened_at from ops.care_barriers where clinic_id = :c and origin = :o and status = 'open' and barrier = any(cast(:b as text[])) order by opened_at, id limit 2000"),
                {"c": self._clinic, "o": self._origin, "b": list(SOCIAL_BARRIERS)},
            )
        ).all()
        return [GapItem(self.source, str(r.id), r.opened_at) for r in rows]

    async def resolve(self, item_id: str) -> GapItem | None:
        try:
            ident = uuid.UUID(item_id)
        except ValueError:
            return None
        row = (
            await self._conn.execute(
                text("select id, opened_at, status, barrier from ops.care_barriers where id = :i and clinic_id = :c and origin = :o"), {"i": ident, "c": self._clinic, "o": self._origin}
            )
        ).first()
        if row is None:
            return None
        return GapItem(self.source, str(row.id), row.opened_at, is_open=row.status == "open" and row.barrier in SOCIAL_BARRIERS)


class SampleSource:
    source = "after_the_fact_review"

    def __init__(self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str) -> None:
        self._conn, self._clinic, self._origin = conn, clinic_id, origin

    async def open_items(self) -> list[GapItem]:
        rows = (
            await self._conn.execute(
                text("select id, sampled_at from ops.care_review_samples where clinic_id = :c and origin = :o and rechecked_at is null order by sampled_at, id limit 2000"),
                {"c": self._clinic, "o": self._origin},
            )
        ).all()
        return [GapItem(self.source, str(r.id), r.sampled_at) for r in rows]

    async def resolve(self, item_id: str) -> GapItem | None:
        try:
            ident = uuid.UUID(item_id)
        except ValueError:
            return None
        row = (
            await self._conn.execute(
                text("select id, sampled_at, rechecked_at from ops.care_review_samples where id = :i and clinic_id = :c and origin = :o"), {"i": ident, "c": self._clinic, "o": self._origin}
            )
        ).first()
        return GapItem(self.source, str(row.id), row.sampled_at, is_open=row.rechecked_at is None) if row is not None else None


def sources_for(conn: AsyncConnection, clinic_id: uuid.UUID, origin: str):
    return [RecurrenceSource(conn, clinic_id, origin), SampleSource(conn, clinic_id, origin), BarrierSource(conn, clinic_id, origin)]


async def record_barrier(conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID, barrier: str, idempotency_key: str) -> tuple[uuid.UUID, bool]:
    """(barrier id, created). A plain insert in a savepoint (never ON CONFLICT: the SELECT policy would also be checked, see careflow/phase4_sql.py); a repeat of the key returns the first row.
    A patient who is not of this clinic is a foreign-key refusal and raises LookupError."""
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text("insert into ops.care_barriers (clinic_id, origin, patient_id, barrier, idempotency_key) values (:c, :o, :p, :b, :k) returning id"),
                    {"c": clinic_id, "o": origin, "p": patient_id, "b": barrier, "k": idempotency_key},
                )
            ).one()
        return row.id, True
    except IntegrityError as exc:
        state = sqlstate(exc)
        if state == "23503":
            raise LookupError("no such patient in this clinic") from None
        if state != "23505":
            raise
    existing = (await conn.execute(text("select id from ops.care_barriers where clinic_id = :c and idempotency_key = :k"), {"c": clinic_id, "k": idempotency_key})).first()
    if existing is None:
        raise LookupError("the key is held by a row you cannot see")
    return existing.id, False


async def resolve_barrier(conn: AsyncConnection, *, barrier_id: uuid.UUID, resolved_by: uuid.UUID) -> str:
    """'resolved', 'already_resolved' or 'not_found'. `resolved_by` is the caller's id from the VERIFIED claims (the application role cannot call auth.uid() by name); the policy still checks it equals auth.uid()."""
    row = (await conn.execute(text("select status, clinic_id from ops.care_barriers where id = :i"), {"i": barrier_id})).first()
    if row is None:
        return "not_found"
    if row.status != "open":
        return "already_resolved"
    done = (
        await conn.execute(
            text("update ops.care_barriers set status = 'resolved', resolved_by = :me where id = :i and status = 'open' returning id"),
            {"i": barrier_id, "me": resolved_by},
        )
    ).first()
    return "resolved" if done is not None else "already_resolved"
