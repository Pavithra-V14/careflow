"""The real implementations of the Scheduling graph's ports (careflow/scheduling_graph.py), built from the modules that already exist. They add NO new database access of their own: ranking, holding, booking and
releasing go through SlotManager, the offer through offers.send_offer, the screening through messages.screen_stored_message, the suggestion through reply_interpretation.interpret_reply. Every one runs on the
REQUEST's connection (the signed-in front-desk person's own session, row-level security in force); the actor id is read from the verified claims already set on that connection, never from an argument.

The decision engine behind the interpreter is whatever DecisionService it is given: in this package a FAKE (no real engine is approved). The review-task writer here is STATE-ONLY: a run that needs a person ends
in a visible status (`booking_needs_human`, `needs_human`, `held_unassessable`) that `GET /backfills/{slot}` shows and a deterministic task id; it writes no `agents.review_tasks` row, because a front-desk session
may not insert one of that kind and giving it that right would be a new policy (docs/design/pending-owner-ok-2026-11-04.md). A durable task for an unattended run is a recorded follow-up.
"""

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow import offers, reply_interpretation
from careflow.crypto import DemographicVault
from careflow.decision import DecisionService
from careflow.messages import screen_stored_message
from careflow.rules import RedFlagEngine
from careflow.scheduling import SlotManager
from careflow.scheduling_agent import SchedulingSettings


async def signed_in_account(conn: AsyncConnection) -> uuid.UUID:
    """The caller's account id from the verified claims set on this transaction (the same value auth.uid() returns). No claims is a hard error: nothing runs anonymously."""
    value = (await conn.execute(text("select nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub'"))).scalar()
    if not value:
        raise PermissionError("no signed-in account")
    return uuid.UUID(str(value))


class SlotRanker:
    def __init__(self, slots: SlotManager | None = None) -> None:
        self._slots = slots or SlotManager()

    async def rank(self, conn: AsyncConnection, clinic_id: uuid.UUID, visit_type_id: uuid.UUID) -> list[dict[str, Any]]:
        ranked = await self._slots.rank_waitlist(conn, clinic_id, visit_type_id)
        return [{"waitlist_id": c.waitlist_id, "patient_id": c.patient_id, "rank": c.rank} for c in ranked]


class OfferSenderPort:
    def __init__(self, vault: DemographicVault, settings: SchedulingSettings, key_version: int, slots: SlotManager | None = None) -> None:
        self._vault, self._settings, self._key_version, self._slots = vault, settings, key_version, slots

    async def send(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, slot_id: uuid.UUID, visit_type_id: uuid.UUID, waitlist_id: uuid.UUID, idempotency_key: str
    ) -> dict[str, str]:
        sent = await offers.send_offer(
            conn, self._vault, self._settings, key_version=self._key_version, clinic_id=clinic_id, origin=origin, visit_type_id=visit_type_id, slot_id=slot_id, waitlist_id=waitlist_id,
            actor_id=await signed_in_account(conn), idempotency_key=idempotency_key, slots=self._slots,
        )
        return {"hold_id": str(sent.hold_id), "message_id": str(sent.message_id)}


class ReplyScreenerPort:
    def __init__(self, vault: DemographicVault, engine: RedFlagEngine, profile: str | None = None) -> None:
        self._vault, self._engine, self._profile = vault, engine, profile

    async def screen(self, conn: AsyncConnection, *, message_id: uuid.UUID) -> str:
        done = await screen_stored_message(conn, self._vault, self._engine, message_id, idempotency_key=f"sched-screen:{message_id}", profile=self._profile)
        return "clear" if done.outcome == "clear" else "red_flag_escalated" if done.outcome == "red_flag" else "held_unassessable"


class ReplyInterpreterPort:
    """Wraps reply_interpretation.interpret_reply (which screens FIRST again, idempotently, before it reads the text for the model). Its suggestion words map to the graph's three answers; anything else
    (other time, a question, no suggestion, an engine failure, a spent ceiling) is `unclear`, i.e. a person."""

    def __init__(self, vault: DemographicVault, engine: RedFlagEngine, service: DecisionService) -> None:
        self._vault, self._engine, self._service = vault, engine, service

    async def interpret(self, conn: AsyncConnection, *, message_id: uuid.UUID, clinic_id: uuid.UUID) -> dict[str, Any]:
        result = await reply_interpretation.interpret_reply(
            conn, self._vault, self._engine, self._service, message_id, actor_id=await signed_in_account(conn), idempotency_key=f"sched-interpret:{message_id}"
        )
        answer = result.suggestion if result.suggestion in {"accept", "decline"} else "unclear"
        return {"answer": answer, "reason": result.reason or ("none" if answer != "unclear" else "no_suggestion")}


class BookerPort:
    def __init__(self, slots: SlotManager | None = None) -> None:
        self._slots = slots or SlotManager()

    async def book(self, conn: AsyncConnection, *, slot_id: uuid.UUID, patient_id: uuid.UUID, idempotency_key: str) -> None:
        await self._slots.book_appointment(conn, slot_id, patient_id, idempotency_key)


class HoldReleaserPort:
    def __init__(self, slots: SlotManager | None = None) -> None:
        self._slots = slots or SlotManager()

    async def release(self, conn: AsyncConnection, *, hold_id: uuid.UUID) -> None:
        await self._slots.release_hold(conn, hold_id)


class DurableTasks:
    """Raises the review task through agents.raise_backfill_task (migration 0038), the ONLY way front desk can: it checks the caller, the clinic and the slot, fixes the kind and the provenance, and writes
    the audit row itself. The conflict between a replay and the first write is the function's, so a replay returns the same task."""

    async def raise_task(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, slot_id: uuid.UUID, status: str, reasons: list[str], priority: str, assigned_role: str, idempotency_key: str
    ) -> uuid.UUID:
        value = (
            await conn.execute(
                text("select agents.raise_backfill_task(:c, :s, :st, :p, :r, :k)"),
                {"c": clinic_id, "s": slot_id, "st": status, "p": priority, "r": assigned_role, "k": idempotency_key},
            )
        ).scalar()
        if value is None:
            raise PermissionError("the review task was not raised")
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class StateOnlyTasks:
    """No row is written: the run's visible status and this deterministic id are the signal (see the module docstring)."""

    async def raise_task(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, slot_id: uuid.UUID, status: str, reasons: list[str], priority: str, assigned_role: str, idempotency_key: str
    ) -> uuid.UUID:
        return uuid.uuid5(uuid.NAMESPACE_URL, f"careflow-sched-task:{clinic_id}:{idempotency_key}")
