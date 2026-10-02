"""Scheduling core: holds, bookings, cancellation and waitlist ranking (BUILD-SPEC.md §17.1, §17.5, §12.1).

`SlotManager` is deterministic Postgres bookkeeping. It makes no model call, no network call and sends
no message. Every method takes an `AsyncConnection` that is already inside a transaction, running as the
restricted app role with the verified claims set (`careflow.db.set_request_claims`); row-level security,
not this module, decides who may do what, and a refused write surfaces as `NotPermitted`.

Operational decisions (answers to the scoping questions for this module)

A. Hold expiry is a SOFT FILTER with an explicit sweep behind it. Correctness never depends on a job
   having run: every operation that touches a slot first treats any ACTIVE hold whose `expires_at` has
   passed as expired (under a row lock on the slot) and frees the slot, so a missed or late tick can never
   leave a slot unbookable. `release_expired_holds` is the explicit sweep a scheduled tick calls to tidy
   rows that nobody has touched; it is a convenience, not a dependency. (This inverts the "cron first, soft
   filter as backup" suggestion on purpose: a scheduler outage must not become a booking outage.) Expiry is
   judged against the database clock (`clock_timestamp()`), never an application clock.

B. No patient notification is created here. Nothing in this module writes to a message, outbox, queue or
   notification table, imports a messaging or HTTP client, or calls a template. A freed slot produces a
   RANKED LIST of waitlisted candidates for a person (or, in a later phase, the scheduling agent through
   the approval matrix) to act on; no candidate is held, booked or told anything automatically. This keeps
   §12.1's human-in-the-loop rule for patient messages. `tests/test_scheduling_live.py` asserts it.

C. Rule 4 (a node that pauses must never also cause a side effect): there is no graph and no pause in this
   module. Each public method is one non-interrupting transaction step: it runs to completion and commits
   or rolls back as a unit. Whoever wires a graph around it must put the single call to one of these
   methods in the step immediately before an interrupt, never in a node that also waits. The module
   imports no orchestration library (asserted by test).

Rule 5: every write carries an idempotency key. Replaying a key with identical parameters returns the
original result with `replayed=True`; the same key with different parameters raises IdempotencyConflict.

Waitlist order: strict clinical priority rank first (rank 1 first), then time joined, then id. A longer-
waiting lower-priority patient never outranks a higher-priority one. The priority levels themselves are
clinician-authored data in `sched.priority_levels`; nothing in this module defines or defaults one.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection


class SchedulingError(Exception):
    """Base class: the request was refused and nothing was changed."""


class SlotNotFound(SchedulingError):
    """No such slot, or the caller's role cannot see it."""


class BookingNotFound(SchedulingError):
    """No such booking, or the caller's role cannot see it."""


class SlotUnavailable(SchedulingError):
    """The slot is not open: it is held, booked or cancelled."""

    def __init__(self, message: str, status: str) -> None:
        super().__init__(message)
        self.status = status


class InvalidTransition(SchedulingError):
    """The requested state change does not apply (for example cancelling an already-cancelled booking)."""


class IdempotencyConflict(SchedulingError):
    """This idempotency key is already used by a request with different parameters."""


class UnknownReference(SchedulingError):
    """A patient, visit type or priority level does not exist in this clinic."""


class NotPermitted(SchedulingError):
    """Row-level security refused the write: the caller's role may not do this."""


@dataclass(frozen=True)
class HoldResult:
    hold_id: uuid.UUID
    slot_id: uuid.UUID
    expires_at: datetime
    replayed: bool = False


@dataclass(frozen=True)
class BookingResult:
    booking_id: uuid.UUID
    slot_id: uuid.UUID
    patient_id: uuid.UUID
    status: Literal["confirmed", "cancelled"]
    replayed: bool = False


@dataclass(frozen=True)
class WaitlistCandidate:
    waitlist_id: uuid.UUID
    patient_id: uuid.UUID
    priority_code: str
    rank: int
    joined_at: datetime


@dataclass(frozen=True)
class WaitlistEntry:
    waitlist_id: uuid.UUID
    patient_id: uuid.UUID
    visit_type_id: uuid.UUID
    priority_code: str
    replayed: bool = False


@dataclass(frozen=True)
class CancelResult:
    booking_id: uuid.UUID
    slot_id: uuid.UUID
    # Waitlisted patients who could take the freed slot, best first. Informational only: none is held,
    # booked or contacted by this call.
    candidates: tuple[WaitlistCandidate, ...]
    replayed: bool = False


# The ONE definition of queue order, shared by `rank_waitlist` and `list_waitlist`.
_WAITLIST_ORDER = "pl.rank asc, w.joined_at asc, w.id asc"


def _sqlstate(exc: DBAPIError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


def _require_key(key: object) -> str:
    if not isinstance(key, str) or not key.strip():
        raise SchedulingError("an idempotency key is required on every write")
    return key


class SlotManager:
    # ---- shared helpers ----------------------------------------------------------------------------

    async def _lock_slot(self, conn: AsyncConnection, slot_id: uuid.UUID):
        row = (
            await conn.execute(
                text("select id, clinic_id, status, visit_type_id from sched.slots where id = :s for update"),
                {"s": slot_id},
            )
        ).first()
        if row is None:
            raise SlotNotFound("slot not found")
        return row

    async def _settle_expired_holds(self, conn: AsyncConnection, slot_id: uuid.UUID) -> tuple[str, uuid.UUID | None, uuid.UUID | None]:
        """Soft filter (A). Under the slot lock: expire this slot's stale active holds, and report the slot's
        effective state as (status, live_hold_id, live_hold_patient_id). A slot marked 'held' with no live
        hold is effectively open and is freed here."""
        await conn.execute(
            text(
                "update sched.holds set status = 'expired' "
                "where slot_id = :s and status = 'active' and expires_at <= clock_timestamp()"
            ),
            {"s": slot_id},
        )
        live = (
            await conn.execute(
                text("select id, patient_id from sched.holds where slot_id = :s and status = 'active'"), {"s": slot_id}
            )
        ).first()
        status = (await conn.execute(text("select status from sched.slots where id = :s"), {"s": slot_id})).scalar_one()
        if status == "held" and live is None:
            await conn.execute(text("update sched.slots set status = 'open' where id = :s"), {"s": slot_id})
            status = "open"
        return status, (live[0] if live else None), (live[1] if live else None)

    # ---- hold --------------------------------------------------------------------------------------

    async def hold_slot(
        self,
        conn: AsyncConnection,
        slot_id: uuid.UUID,
        duration_minutes: int,
        *,
        idempotency_key: str,
        patient_id: uuid.UUID | None = None,
    ) -> HoldResult:
        """Atomically move an open slot to 'held' and record the hold. Fails with SlotUnavailable if the slot
        is held (and the hold has not expired), booked or cancelled."""
        key = _require_key(idempotency_key)
        if isinstance(duration_minutes, bool) or not isinstance(duration_minutes, int) or duration_minutes <= 0:
            raise ValueError("duration_minutes must be a positive integer")
        slot = await self._lock_slot(conn, slot_id)

        prior = (
            await conn.execute(
                text("select id, slot_id, patient_id, expires_at from sched.holds where idempotency_key = :k"), {"k": key}
            )
        ).first()
        if prior is not None:
            if prior.slot_id != slot_id or prior.patient_id != patient_id:
                raise IdempotencyConflict("this idempotency key was used for a different hold")
            return HoldResult(prior.id, prior.slot_id, prior.expires_at, replayed=True)

        status, _, _ = await self._settle_expired_holds(conn, slot_id)
        if status != "open":
            raise SlotUnavailable(f"slot is {status}", status)
        try:
            async with conn.begin_nested():
                row = (
                    await conn.execute(
                        text(
                            """insert into sched.holds (clinic_id, slot_id, patient_id, expires_at, origin, idempotency_key)
                               select clinic_id, id, :p, clock_timestamp() + make_interval(mins => :d), origin, :k
                               from sched.slots where id = :s
                               returning id, expires_at"""
                        ),
                        {"p": patient_id, "d": duration_minutes, "k": key, "s": slot_id},
                    )
                ).one()
                await conn.execute(text("update sched.slots set status = 'held' where id = :s"), {"s": slot_id})
        except IntegrityError as exc:
            self._raise_for_integrity(exc, slot.status)
            raise
        except DBAPIError as exc:
            self._raise_for_dbapi(exc)
            raise
        return HoldResult(row.id, slot_id, row.expires_at)

    # ---- book --------------------------------------------------------------------------------------

    async def book_appointment(
        self, conn: AsyncConnection, slot_id: uuid.UUID, patient_id: uuid.UUID, idempotency_key: str
    ) -> BookingResult:
        """Book an open slot, or a slot held with a live hold that this patient may use, idempotently."""
        key = _require_key(idempotency_key)
        await self._lock_slot(conn, slot_id)

        prior = (
            await conn.execute(
                text("select id, slot_id, patient_id, status from sched.bookings where idempotency_key = :k"), {"k": key}
            )
        ).first()
        if prior is not None:
            if prior.slot_id != slot_id or prior.patient_id != patient_id:
                raise IdempotencyConflict("this idempotency key was used for a different booking")
            return BookingResult(prior.id, prior.slot_id, prior.patient_id, prior.status, replayed=True)

        status, hold_id, hold_patient = await self._settle_expired_holds(conn, slot_id)
        if status in ("booked", "cancelled"):
            raise SlotUnavailable(f"slot is {status}", status)
        if status == "held" and hold_patient is not None and hold_patient != patient_id:
            raise SlotUnavailable("slot is held for another patient", "held")
        try:
            async with conn.begin_nested():
                row = (
                    await conn.execute(
                        text(
                            """insert into sched.bookings (clinic_id, slot_id, patient_id, hold_id, origin, idempotency_key)
                               select clinic_id, id, :p, :h, origin, :k from sched.slots where id = :s
                               returning id"""
                        ),
                        {"p": patient_id, "h": hold_id, "k": key, "s": slot_id},
                    )
                ).one()
                if hold_id is not None:
                    await conn.execute(text("update sched.holds set status = 'converted' where id = :h"), {"h": hold_id})
                await conn.execute(text("update sched.slots set status = 'booked' where id = :s"), {"s": slot_id})
                # Internal bookkeeping only: the patient's own waiting entry for this visit type is fulfilled.
                await conn.execute(
                    text(
                        """update sched.waitlist set status = 'fulfilled'
                           where patient_id = :p and status = 'waiting'
                             and visit_type_id = (select visit_type_id from sched.slots where id = :s)"""
                    ),
                    {"p": patient_id, "s": slot_id},
                )
        except IntegrityError as exc:
            self._raise_for_integrity(exc, status)
            raise
        except DBAPIError as exc:
            self._raise_for_dbapi(exc)
            raise
        return BookingResult(row.id, slot_id, patient_id, "confirmed")

    # ---- cancel ------------------------------------------------------------------------------------

    async def cancel_appointment(self, conn: AsyncConnection, booking_id: uuid.UUID, *, idempotency_key: str) -> CancelResult:
        """Cancel a booking, free its slot, and return the ranked waitlist candidates for that slot.
        Nothing is held, booked or sent to any candidate."""
        key = _require_key(idempotency_key)
        booking = (
            await conn.execute(
                text("select id, slot_id, clinic_id, status, cancel_idempotency_key from sched.bookings where id = :b"),
                {"b": booking_id},
            )
        ).first()
        if booking is None:
            raise BookingNotFound("booking not found")
        slot = await self._lock_slot(conn, booking.slot_id)
        if booking.status == "cancelled":
            if booking.cancel_idempotency_key != key:
                raise InvalidTransition("booking is already cancelled")
            return CancelResult(booking.id, booking.slot_id, await self.rank_waitlist(conn, slot.clinic_id, slot.visit_type_id), True)
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "update sched.bookings set status = 'cancelled', cancelled_at = now(), cancel_idempotency_key = :k "
                        "where id = :b"
                    ),
                    {"k": key, "b": booking_id},
                )
                await conn.execute(
                    text("update sched.slots set status = 'open' where id = :s and status = 'booked'"), {"s": booking.slot_id}
                )
        except IntegrityError as exc:
            if "cancel_idempotency_key" in str(exc.orig):
                raise IdempotencyConflict("this idempotency key was used for a different cancellation") from exc
            raise
        except DBAPIError as exc:
            self._raise_for_dbapi(exc)
            raise
        return CancelResult(booking.id, booking.slot_id, await self.rank_waitlist(conn, slot.clinic_id, slot.visit_type_id))

    # ---- waitlist ----------------------------------------------------------------------------------

    async def join_waitlist(
        self,
        conn: AsyncConnection,
        patient_id: uuid.UUID,
        visit_type_id: uuid.UUID,
        priority_code: str,
        *,
        idempotency_key: str,
    ) -> WaitlistEntry:
        """Queue a patient for a visit type at a clinician-defined priority level."""
        key = _require_key(idempotency_key)
        if not isinstance(priority_code, str) or not priority_code.strip():
            raise SchedulingError("a priority level is required; there is no default")
        prior = (
            await conn.execute(
                text("select id, patient_id, visit_type_id, priority_code from sched.waitlist where idempotency_key = :k"),
                {"k": key},
            )
        ).first()
        if prior is not None:
            if (prior.patient_id, prior.visit_type_id, prior.priority_code) != (patient_id, visit_type_id, priority_code):
                raise IdempotencyConflict("this idempotency key was used for a different waitlist entry")
            return WaitlistEntry(prior.id, patient_id, visit_type_id, priority_code, replayed=True)
        try:
            async with conn.begin_nested():
                row = (
                    await conn.execute(
                        text(
                            """insert into sched.waitlist (clinic_id, patient_id, visit_type_id, priority_code, origin, idempotency_key)
                               select clinic_id, :p, id, :c, origin, :k from sched.visit_types where id = :v
                               returning id"""
                        ),
                        {"p": patient_id, "v": visit_type_id, "c": priority_code, "k": key},
                    )
                ).first()
        except IntegrityError as exc:
            if "foreign key" in str(exc.orig).lower():
                raise UnknownReference("unknown patient or priority level for this clinic") from exc
            if "waitlist_one_waiting_entry" in str(exc.orig):
                raise InvalidTransition("patient is already waiting for this visit type") from exc
            if "idempotency_key" in str(exc.orig):
                raise IdempotencyConflict("this idempotency key is already used") from exc
            raise
        except DBAPIError as exc:
            self._raise_for_dbapi(exc)
            raise
        if row is None:
            raise UnknownReference("visit type not found")
        return WaitlistEntry(row.id, patient_id, visit_type_id, priority_code)

    async def rank_waitlist(
        self, conn: AsyncConnection, clinic_id: uuid.UUID, visit_type_id: uuid.UUID
    ) -> tuple[WaitlistCandidate, ...]:
        """Waiting patients for a visit type: strict priority rank first (1 = served first), then who joined
        first, then id as a final deterministic tie-break. A deactivated level still ranks: a patient is never
        silently dropped from the queue because a level was retired."""
        rows = await conn.execute(
            text(
                """select w.id, w.patient_id, w.priority_code, pl.rank, w.joined_at
                   from sched.waitlist w
                   join sched.priority_levels pl on pl.clinic_id = w.clinic_id and pl.code = w.priority_code
                   where w.clinic_id = :c and w.visit_type_id = :v and w.status = 'waiting'
                   order by """ + _WAITLIST_ORDER
            ),
            {"c": clinic_id, "v": visit_type_id},
        )
        return tuple(WaitlistCandidate(r.id, r.patient_id, r.priority_code, r.rank, r.joined_at) for r in rows)

    # ---- reads (staff console) ---------------------------------------------------------------------
    #
    # Read-only, no side effect. `clinic_ids` narrows the result to clinics the caller is allowed to read for this
    # purpose; row-level security still decides underneath, so this can only ever remove rows, never add them. Each
    # returns up to `limit` rows; callers ask for limit + 1 to learn whether there are more.

    async def list_slots(
        self,
        conn: AsyncConnection,
        clinic_ids: list[uuid.UUID],
        *,
        provider_id: uuid.UUID | None = None,
        location_id: uuid.UUID | None = None,
        starts_after: datetime | None = None,
        limit: int,
    ):
        """Open and held slots, soonest first. Status is the EFFECTIVE one (A): a slot marked 'held' whose hold has
        expired is reported 'open', so a late sweep never shows a bookable slot as taken."""
        sql = (
            "select s.id, s.clinic_id, s.provider_id, s.location_id, s.visit_type_id, s.starts_at, s.ends_at, "
            "case when s.status = 'held' and h.id is null then 'open' else s.status end as status, "
            "h.expires_at as hold_expires_at "
            "from sched.slots s "
            "left join lateral (select id, expires_at from sched.holds "
            "where slot_id = s.id and status = 'active' and expires_at > clock_timestamp() limit 1) h on true "
            "where s.clinic_id = any(:clinics) and s.status in ('open', 'held')"
        )
        params: dict = {"clinics": clinic_ids, "n": limit}
        for column, name, value in (("s.provider_id", "p", provider_id), ("s.location_id", "l", location_id)):
            if value is not None:
                sql += f" and {column} = :{name}"
                params[name] = value
        if starts_after is not None:
            sql += " and s.starts_at >= :after"
            params["after"] = starts_after
        return (await conn.execute(text(sql + " order by s.starts_at, s.id limit :n"), params)).all()

    async def list_bookings(self, conn: AsyncConnection, clinic_ids: list[uuid.UUID], *, limit: int):
        """Confirmed bookings, soonest appointment first. Identifiers only: no patient text is read or decrypted."""
        return (
            await conn.execute(
                text(
                    "select b.id, b.clinic_id, b.slot_id, b.patient_id, b.created_at, s.provider_id, s.location_id, "
                    "s.visit_type_id, s.starts_at, s.ends_at "
                    "from sched.bookings b join sched.slots s on s.id = b.slot_id and s.clinic_id = b.clinic_id "
                    "where b.clinic_id = any(:clinics) and b.status = 'confirmed' "
                    "order by s.starts_at, b.id limit :n"
                ),
                {"clinics": clinic_ids, "n": limit},
            )
        ).all()

    async def list_waitlist(
        self, conn: AsyncConnection, clinic_ids: list[uuid.UUID], *, visit_type_id: uuid.UUID | None = None, limit: int
    ):
        """Waiting patients: clinical priority rank first (1 first), then who joined first, then id. The same order
        `rank_waitlist` uses, so the queue a person reads is the queue a freed slot is offered through."""
        sql = (
            "select w.id, w.clinic_id, w.patient_id, w.visit_type_id, w.priority_code, pl.rank, w.joined_at "
            "from sched.waitlist w "
            "join sched.priority_levels pl on pl.clinic_id = w.clinic_id and pl.code = w.priority_code "
            "where w.clinic_id = any(:clinics) and w.status = 'waiting'"
        )
        params: dict = {"clinics": clinic_ids, "n": limit}
        if visit_type_id is not None:
            sql += " and w.visit_type_id = :v"
            params["v"] = visit_type_id
        return (await conn.execute(text(sql + " order by " + _WAITLIST_ORDER + " limit :n"), params)).all()

    # ---- sweep -------------------------------------------------------------------------------------

    async def release_expired_holds(self, conn: AsyncConnection) -> int:
        """The explicit sweep (A): expire every active hold past its time and reopen slots left 'held' with no
        live hold. Safe to run at any time, any number of times; returns how many holds it expired."""
        expired = await conn.execute(
            text(
                "update sched.holds set status = 'expired' where status = 'active' and expires_at <= clock_timestamp() "
                "returning slot_id"
            )
        )
        slot_ids = list({r.slot_id for r in expired})
        if slot_ids:
            await conn.execute(
                text(
                    """update sched.slots s set status = 'open'
                       where s.id = any(:ids) and s.status = 'held'
                         and not exists (select 1 from sched.holds h where h.slot_id = s.id and h.status = 'active')"""
                ),
                {"ids": slot_ids},
            )
        return len(slot_ids)

    # ---- error mapping -----------------------------------------------------------------------------

    @staticmethod
    def _raise_for_integrity(exc: IntegrityError, status: str) -> None:
        detail = str(exc.orig)
        if "holds_one_active_per_slot" in detail or "bookings_one_confirmed_per_slot" in detail:
            raise SlotUnavailable("slot was taken by a concurrent request", status) from exc
        if "idempotency_key" in detail:
            raise IdempotencyConflict("this idempotency key is already used by another request") from exc
        if "foreign key" in detail.lower():
            raise UnknownReference("unknown patient for this clinic") from exc

    @staticmethod
    def _raise_for_dbapi(exc: DBAPIError) -> None:
        state = _sqlstate(exc)
        if state == "42501":
            raise NotPermitted("your role may not make this change") from exc
        if state == "23514":
            raise SchedulingError(f"rejected by a database check: {exc.orig}") from exc
