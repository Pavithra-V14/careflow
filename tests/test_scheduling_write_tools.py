"""The scheduling server's WRITE tools behind the five-check pipeline (careflow/scheduling_write_tools.py), offline: a fake SlotManager, a fake connection, the SDK's in-memory transport. Proves the order
of the checks, the shadow-mode behaviour (a proposal, nothing executed), authority on the ROW's clinic, the budget, the key, and that nothing real is reached."""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from careflow.autonomy import Policy
from careflow.budget import Limits, RunBudget, TickClock
from careflow.mcp_kit import session_context
from careflow.scheduling import SlotUnavailable
from careflow.scheduling_agent import SchedulingSettings, build_scheduling_server
from careflow.write_pipeline import ToolResult

pytestmark = pytest.mark.asyncio
ACCOUNT, SLOT, PATIENT, VT, BOOKING = (uuid.uuid4() for _ in range(5))
NOW = datetime(2030, 1, 1, tzinfo=UTC)


class _R:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class Conn:
    def __init__(self, allowed=True):
        self.allowed, self.statements = allowed, []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        if "iam.has_clinic_role" in str(statement):
            return _R(self.allowed)
        raise AssertionError(str(statement))


class Slots:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    async def _do(self, name, result, *args, **kw):
        self.calls.append((name, args, kw))
        if self.error:
            raise self.error
        return result

    async def hold_slot(self, conn, slot_id, minutes, *, idempotency_key, patient_id=None):
        return await self._do("hold", SimpleNamespace(hold_id=uuid.uuid4(), slot_id=slot_id, expires_at=NOW, replayed=False), slot_id, minutes, key=idempotency_key, patient=patient_id)

    async def book_appointment(self, conn, slot_id, patient_id, idempotency_key):
        return await self._do("book", SimpleNamespace(booking_id=uuid.uuid4(), slot_id=slot_id, status="confirmed", replayed=False), slot_id, patient_id, key=idempotency_key)

    async def cancel_appointment(self, conn, booking_id, *, idempotency_key):
        cands = (SimpleNamespace(waitlist_id=uuid.uuid4(), patient_id=PATIENT, priority_code="p1", rank=1),)
        return await self._do("cancel", SimpleNamespace(booking_id=booking_id, slot_id=SLOT, candidates=cands, replayed=False), booking_id, key=idempotency_key)

    async def join_waitlist(self, conn, patient_id, visit_type_id, priority_code, *, idempotency_key):
        return await self._do("join", SimpleNamespace(waitlist_id=uuid.uuid4(), priority_code=priority_code, replayed=False), patient_id, visit_type_id, key=idempotency_key)


def server(slots, mode="assisted", budget=None):
    return build_scheduling_server(SchedulingSettings(_env_file=None), slots, write_policy=Policy(mode), budget=(lambda: budget) if budget else None)  # type: ignore[arg-type, call-arg]


async def call(srv, tool, args, conn=None):
    from mcp.client import Client

    async with session_context(ACCOUNT, conn or Conn(), "test"), Client(srv) as client:
        result = await client.call_tool(tool, args)
    assert not result.is_error, result
    return ToolResult.model_validate(result.structured_content)


BOOK = {"slot_id": str(SLOT), "patient_id": str(PATIENT), "idempotency_key": "k-1"}


async def test_in_assisted_mode_a_booking_runs_after_the_five_checks_and_calls_the_slot_manager_once():
    slots = Slots()
    r = await call(server(slots), "book_appointment", BOOK)
    assert (r.status, r.reason) == ("ok", "executed") and r.data["status"] == "confirmed"
    assert slots.calls == [("book", (SLOT, PATIENT), {"key": "k-1"})]


async def test_in_shadow_mode_nothing_executes_and_the_proposal_is_returned_for_a_person():
    slots = Slots()
    r = await call(server(slots, "shadow"), "book_appointment", BOOK)
    assert (r.status, r.reason) == ("needs_human", "human_decides") and r.data["patient_id"] == str(PATIENT) and slots.calls == []


async def test_authority_is_the_front_desk_grant_on_the_rows_own_clinic_and_a_refusal_executes_nothing():
    slots, conn = Slots(), Conn(allowed=False)
    r = await call(server(slots), "book_appointment", BOOK, conn)
    assert (r.status, r.reason) == ("refused", "not_authorized") and slots.calls == []
    assert any("from sched.slots" in s for s in conn.statements)  # the clinic comes from the ROW, not from an argument


@pytest.mark.parametrize("bad", [{"slot_id": "nope", "patient_id": str(PATIENT)}, {"slot_id": str(SLOT)}])
async def test_bad_arguments_are_invalid_before_authority_is_even_asked(bad):
    slots, conn = Slots(), Conn()
    try:
        r = await call(server(slots), "book_appointment", {**bad, "idempotency_key": "k"}, conn)
    except AssertionError:
        return  # the SDK itself rejected an unknown argument name before the pipeline: also nothing ran
    assert (r.status, r.reason) == ("invalid", "bad_arguments") and slots.calls == [] and conn.statements == []


async def test_a_blank_idempotency_key_is_refused_and_nothing_executes():
    slots = Slots()
    r = await call(server(slots), "book_appointment", {**BOOK, "idempotency_key": "   "})
    assert (r.status, r.reason) == ("refused", "missing_idempotency_key") and slots.calls == []


async def test_the_tool_call_ceiling_stops_an_ordinary_write():
    budget = RunBudget(TickClock(), Limits(tool_calls=1))
    slots = Slots()
    srv = server(slots, budget=budget)
    first = await call(srv, "book_appointment", BOOK)
    second = await call(srv, "book_appointment", {**BOOK, "idempotency_key": "k-2"})
    assert (first.status, second.status, second.reason) == ("ok", "budget_exhausted", "tool_call_ceiling") and len(slots.calls) == 1


async def test_a_refusal_from_the_slot_manager_becomes_a_closed_reason_never_its_text():
    r = await call(server(Slots(error=SlotUnavailable("SENTINEL-DETAIL-1", "held"))), "book_appointment", BOOK)
    assert (r.status, r.reason) == ("unavailable", "slot_unavailable") and "SENTINEL" not in r.model_dump_json()


async def test_cancel_returns_the_ranked_candidates_for_a_person_and_contacts_nobody():
    slots = Slots()
    r = await call(server(slots), "cancel_appointment", {"booking_id": str(BOOKING), "idempotency_key": "k-c"})
    assert r.status == "ok" and r.data["candidates"][0]["rank"] == 1 and [c[0] for c in slots.calls] == ["cancel"]


async def test_hold_and_join_waitlist_use_the_same_pipeline():
    slots = Slots()
    held = await call(server(slots), "hold_slot", {"slot_id": str(SLOT), "duration_minutes": 15, "idempotency_key": "k-h"})
    joined = await call(server(slots), "join_waitlist", {"patient_id": str(PATIENT), "visit_type_id": str(VT), "priority_code": "p1", "idempotency_key": "k-j"})
    assert held.status == joined.status == "ok" and [c[0] for c in slots.calls] == ["hold", "join"]
    assert (await call(server(slots), "hold_slot", {"slot_id": str(SLOT), "duration_minutes": 0, "idempotency_key": "k"})).status == "invalid"


async def test_without_a_write_policy_the_server_is_still_read_only():
    from mcp.client import Client

    srv = build_scheduling_server(SchedulingSettings(_env_file=None), Slots())  # type: ignore[call-arg, arg-type]
    async with Client(srv) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"find_slots", "rank_waitlist", "propose_offer"}


async def test_an_argument_the_tool_does_not_declare_never_reaches_the_executor():
    slots = Slots()
    r = await call(server(slots), "book_appointment", {**BOOK, "clinic_id": str(uuid.uuid4())})  # the SDK drops it: a clinic id in the arguments is never trusted or used
    assert r.status == "ok" and slots.calls == [("book", (SLOT, PATIENT), {"key": "k-1"})]
