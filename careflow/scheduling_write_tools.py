"""The WRITE tools of the in-process `scheduling` MCP server (BUILD-SPEC.md 8; docs/design/phase3-scheduling-agent.md): hold a slot, book, cancel, join the waitlist. Every one runs through the five-step
pipeline of careflow/write_pipeline.py in the spec's order (schema, authority on THIS row, autonomy policy, budget, idempotency key) and answers with an explicit ToolResult status, never a bare empty value.

* AUTHORITY is the caller's own front-desk grant on the clinic the ROW belongs to (looked up from the slot, booking or visit type), through iam.has_clinic_role: the same helper, so the active-role header
  narrows it. The tool never trusts a clinic id in its arguments.
* POLICY: all four are the `booking` action class of the approval matrix. In `shadow` mode they return `needs_human` with the validated proposal and execute NOTHING; in `assisted` and `autonomous` they run.
* The tools call the existing SlotManager, which already does the row locking, the exclusion rules and the idempotent replay; nothing here duplicates it. They write no message and contact no patient.
* The result `data` holds ids, numbers and closed words only. A refusal from the slot manager becomes a closed reason (`slot_unavailable`, `not_found`, `conflict`, `request_rejected`), never its text.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import Field
from sqlalchemy import text

from careflow.autonomy import Policy
from careflow.budget import RunBudget
from careflow.mcp_kit import async_tool, current_session
from careflow.scheduling import (
    BookingNotFound,
    HoldNotFound,
    IdempotencyConflict,
    InvalidTransition,
    SchedulingError,
    SlotManager,
    SlotNotFound,
    SlotUnavailable,
    UnknownReference,
)
from careflow.write_pipeline import StrictArgs, ToolResult, guarded_write

ACTION = "booking"


class HoldArgs(StrictArgs):
    slot_id: uuid.UUID
    duration_minutes: int = Field(gt=0, le=24 * 60)
    patient_id: uuid.UUID | None = None


class BookArgs(StrictArgs):
    slot_id: uuid.UUID
    patient_id: uuid.UUID


class CancelArgs(StrictArgs):
    booking_id: uuid.UUID


class WaitlistArgs(StrictArgs):
    patient_id: uuid.UUID
    visit_type_id: uuid.UUID
    priority_code: str = Field(min_length=1, max_length=40)


async def _front_desk_of(sql: str, row_id: uuid.UUID) -> bool:
    """Does the signed-in caller hold front desk at the clinic that owns this row? (An invisible or missing row is False: one answer for both.)"""
    session = current_session()
    result = await session.conn.execute(text(sql), {"i": row_id})
    return bool(result.scalar())


_SLOT_CLINIC = "select iam.has_clinic_role(clinic_id, array['front_desk']) from sched.slots where id = :i"
_BOOKING_CLINIC = "select iam.has_clinic_role(clinic_id, array['front_desk']) from sched.bookings where id = :i"
_VISIT_TYPE_CLINIC = "select iam.has_clinic_role(clinic_id, array['front_desk']) from sched.visit_types where id = :i"


def _reason(exc: SchedulingError) -> ToolResult:
    if isinstance(exc, SlotUnavailable | InvalidTransition):
        return ToolResult(status="unavailable", reason="slot_unavailable")
    if isinstance(exc, SlotNotFound | BookingNotFound | HoldNotFound | UnknownReference):
        return ToolResult(status="unavailable", reason="not_found")
    if isinstance(exc, IdempotencyConflict):
        return ToolResult(status="unavailable", reason="conflict")
    return ToolResult(status="unavailable", reason="request_rejected")


def register_write_tools(server: Any, manager: SlotManager, policy: Policy, budget: Callable[[], RunBudget | None] | None = None) -> None:
    """Add the four write tools. `budget` returns the current run's RunBudget (or None for no ceiling)."""

    def run_budget() -> RunBudget | None:
        return budget() if budget is not None else None

    async def go(schema: type[StrictArgs], raw: dict[str, Any], authorize: Callable[[Any], Awaitable[bool]], key: str, execute: Callable[[Any, str], Awaitable[ToolResult]]) -> ToolResult:
        async def guarded_execute(args: Any, idempotency_key: str) -> ToolResult:
            try:
                return await execute(args, idempotency_key)
            except SchedulingError as exc:
                return _reason(exc)

        return await guarded_write(schema=schema, raw_args=raw, authorize=authorize, action_class=ACTION, policy=policy, budget=run_budget(), idempotency_key=key, execute=guarded_execute)

    @async_tool(server)
    async def hold_slot(slot_id: str, duration_minutes: Any, idempotency_key: str, patient_id: str | None = None) -> ToolResult:
        """WRITE. Hold an open slot for a while (optionally for one patient). Runs the five checks of spec 8; in shadow mode returns the proposal for a person."""
        session = current_session()

        async def execute(args: HoldArgs, key: str) -> ToolResult:
            held = await manager.hold_slot(session.conn, args.slot_id, args.duration_minutes, idempotency_key=key, patient_id=args.patient_id)
            return ToolResult(status="ok", reason="executed", data={"hold_id": str(held.hold_id), "slot_id": str(held.slot_id), "expires_at": held.expires_at.isoformat(), "replayed": held.replayed})

        return await go(HoldArgs, {"slot_id": slot_id, "duration_minutes": duration_minutes, "patient_id": patient_id}, lambda a: _front_desk_of(_SLOT_CLINIC, a.slot_id), idempotency_key, execute)

    @async_tool(server)
    async def book_appointment(slot_id: str, patient_id: str, idempotency_key: str) -> ToolResult:
        """WRITE. Book a slot (an open one, or one held for this patient) for a patient. Same five checks."""
        session = current_session()

        async def execute(args: BookArgs, key: str) -> ToolResult:
            booked = await manager.book_appointment(session.conn, args.slot_id, args.patient_id, key)
            return ToolResult(status="ok", reason="executed", data={"booking_id": str(booked.booking_id), "slot_id": str(booked.slot_id), "status": booked.status, "replayed": booked.replayed})

        return await go(BookArgs, {"slot_id": slot_id, "patient_id": patient_id}, lambda a: _front_desk_of(_SLOT_CLINIC, a.slot_id), idempotency_key, execute)

    @async_tool(server)
    async def cancel_appointment(booking_id: str, idempotency_key: str) -> ToolResult:
        """WRITE. Cancel a booking; the freed slot's waitlist candidates are returned (best first) for a person, none is held or contacted. Same five checks."""
        session = current_session()

        async def execute(args: CancelArgs, key: str) -> ToolResult:
            cancelled = await manager.cancel_appointment(session.conn, args.booking_id, idempotency_key=key)
            candidates = [{"waitlist_id": str(c.waitlist_id), "patient_id": str(c.patient_id), "priority_code": c.priority_code, "rank": c.rank} for c in cancelled.candidates]
            return ToolResult(
                status="ok" if candidates else "empty", reason="executed" if candidates else "nothing_to_do",
                data={"booking_id": str(cancelled.booking_id), "slot_id": str(cancelled.slot_id), "candidates": candidates, "replayed": cancelled.replayed},
            )

        return await go(CancelArgs, {"booking_id": booking_id}, lambda a: _front_desk_of(_BOOKING_CLINIC, a.booking_id), idempotency_key, execute)

    @async_tool(server)
    async def join_waitlist(patient_id: str, visit_type_id: str, priority_code: str, idempotency_key: str) -> ToolResult:
        """WRITE. Put a patient on the waitlist for a visit type at one of the clinic's own priority levels. Same five checks."""
        session = current_session()

        async def execute(args: WaitlistArgs, key: str) -> ToolResult:
            entry = await manager.join_waitlist(session.conn, args.patient_id, args.visit_type_id, args.priority_code, idempotency_key=key)
            return ToolResult(status="ok", reason="executed", data={"waitlist_id": str(entry.waitlist_id), "priority_code": entry.priority_code, "replayed": entry.replayed})

        return await go(
            WaitlistArgs, {"patient_id": patient_id, "visit_type_id": visit_type_id, "priority_code": priority_code}, lambda a: _front_desk_of(_VISIT_TYPE_CLINIC, a.visit_type_id), idempotency_key, execute
        )
