"""The Scheduling agent's mechanism (careflow/scheduling_agent.py, careflow/mcp_kit.py), offline: a fake SlotManager and a fake connection, the SDK's in-memory transport. No database, no
network, no model. Proves: nothing real runs without the clinician-owned settings, nothing is written, the caller's session is required, and the kit's rules (async-only tools, withheld
log detail, no socket)."""

import inspect
import logging
import socket
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from careflow.mcp_kit import (
    FIXED_LOG_MESSAGE,
    Envelope,
    NoSession,
    NotAsyncTool,
    WithholdSdkDetail,
    async_tool,
    audited,
    current_session,
    invoke,
    session_context,
)
from careflow.scheduling_agent import (
    SETTING_NAMES,
    SchedulingNotConfigured,
    SchedulingSettings,
    build_scheduling_server,
)

pytestmark = pytest.mark.asyncio
ACCOUNT, CLINIC, VISIT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
SENTINEL = "SENTINEL-PATIENT-TEXT-90210"
NOW = datetime(2030, 1, 1, tzinfo=UTC)
ASK = {"clinic_id": str(CLINIC), "visit_type_id": str(VISIT)}


def configured(**over):
    # test inputs only: every number and word here is a stand-in, not a recommendation
    values = {"offer_expiry_seconds": 11, "hold_lifetime_seconds": 7, "backfill_candidates": 2, "accept_confidence_threshold": 0.5, "offer_message_template": "TEMPLATE-FROM-A-CLINICIAN"}
    return SchedulingSettings(_env_file=None, **{**values, **over})  # type: ignore[call-arg]


class FakeConn:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append(str(statement))
        raise AssertionError("the read-only tools reach the database only through SlotManager")


class FakeSlots:
    def __init__(self, ranked=(), slots=()):
        self.ranked, self.slots, self.calls = ranked, slots, []

    async def rank_waitlist(self, conn, clinic_id, visit_type_id):
        self.calls.append(("rank", clinic_id, visit_type_id))
        return self.ranked

    async def list_slots(self, conn, clinic_ids, **kw):
        self.calls.append(("slots", tuple(clinic_ids), kw["limit"]))
        return self.slots


def cand(rank):
    return SimpleNamespace(waitlist_id=uuid.uuid4(), patient_id=uuid.uuid4(), priority_code=f"p{rank}", rank=rank, joined_at=NOW)


def slot():
    return SimpleNamespace(id=uuid.uuid4(), provider_id=uuid.uuid4(), location_id=uuid.uuid4(), visit_type_id=VISIT, starts_at=NOW, status="open")


async def call(server, tool, args, conn=None):
    return await invoke(server, tool, args, account_id=ACCOUNT, conn=conn or FakeConn(), origin="test")  # type: ignore[arg-type]


# ---- the clinician-owned settings ---------------------------------------------------------------------------------------------------------------


async def test_nothing_is_configured_by_default_and_each_setting_is_a_clinician_placeholder():
    s = SchedulingSettings(_env_file=None)  # type: ignore[call-arg]
    assert s.missing() == list(SETTING_NAMES) and all(getattr(s, n) is None for n in SETTING_NAMES)
    with pytest.raises(SchedulingNotConfigured) as raised:
        s.require()
    assert set(raised.value.names) == set(SETTING_NAMES)
    source = inspect.getsource(SchedulingSettings)
    assert source.count("CLINICAL-PLACEHOLDER: needs clinician sign-off") >= len(SETTING_NAMES)


async def test_technical_bounds_reject_nonsense_but_supply_no_value():
    for bad in (
        {"offer_expiry_seconds": 0}, {"hold_lifetime_seconds": -1}, {"backfill_candidates": 0}, {"backfill_candidates": 10_000}, {"accept_confidence_threshold": 0},
        {"accept_confidence_threshold": 1.5}, {"offer_message_template": ""},
    ):
        with pytest.raises(ValueError):
            configured(**bad)


async def test_propose_offer_is_unavailable_and_names_the_missing_settings_until_they_exist():
    slots = FakeSlots(ranked=(cand(1),))
    only_one = SchedulingSettings(_env_file=None, offer_expiry_seconds=5)  # type: ignore[call-arg]
    out = await call(build_scheduling_server(only_one, slots), "propose_offer", ASK)
    assert out.status == "unavailable" and out.reason is not None and out.reason.startswith("not_configured:")
    assert "backfill_candidates" in out.reason and "offer_expiry_seconds" not in out.reason
    assert slots.calls == []  # not even a read happens without the settings


# ---- the tools ------------------------------------------------------------------------------------------------------------------------------


async def test_propose_offer_returns_the_top_ranked_candidates_and_a_draft_and_writes_nothing():
    ranked = (cand(1), cand(2), cand(3))
    conn = FakeConn()
    out = await call(build_scheduling_server(configured(), FakeSlots(ranked=ranked)), "propose_offer", ASK, conn)
    assert out.status == "ok" and isinstance(out.data, dict)
    assert [c["rank"] for c in out.data["candidates"]] == [1, 2]  # backfill_candidates = 2: the first two, in rank order
    draft = out.data["draft"]
    assert draft["candidate_patient_id"] == str(ranked[0].patient_id) and draft["template"] == "TEMPLATE-FROM-A-CLINICIAN" and draft["rule"] == "rank_then_joined_at"
    assert conn.statements == []  # nothing here executes SQL itself, and the fake SlotManager never touched the connection


async def test_an_empty_ranking_is_empty_not_a_default_and_slots_are_bounded():
    slots = FakeSlots()
    server = build_scheduling_server(configured(), slots)
    assert (await call(server, "rank_waitlist", ASK)).model_dump() == {"status": "empty", "reason": "no_ranked_candidates", "data": []}
    assert (await call(server, "propose_offer", ASK)).status == "empty"
    assert (await call(server, "find_slots", {"clinic_id": str(CLINIC), "limit": 10_000})).status == "empty"
    assert ("slots", (CLINIC,), 50) in slots.calls  # the limit is clamped to the technical bound


async def test_find_slots_and_rank_waitlist_return_ids_and_closed_words_only():
    server = build_scheduling_server(configured(), FakeSlots(ranked=(cand(1),), slots=(slot(),)))
    found = await call(server, "find_slots", {"clinic_id": str(CLINIC)})
    ranked = await call(server, "rank_waitlist", ASK)
    assert found.status == "ok" and set(found.data[0]) == {"slot_id", "provider_id", "location_id", "visit_type_id", "starts_at", "status"}  # type: ignore[index]
    assert ranked.status == "ok" and set(ranked.data[0]) == {"waitlist_id", "patient_id", "priority_code", "rank", "joined_at"}  # type: ignore[index]


async def test_a_bad_argument_is_rejected_by_the_schema_before_the_tool_runs():
    slots = FakeSlots()
    out = await call(build_scheduling_server(configured(), slots), "rank_waitlist", {"clinic_id": "not-a-uuid", "visit_type_id": str(VISIT)})
    assert out.status == "unavailable" and out.reason == "tool_error" and slots.calls == []


async def test_without_a_session_a_tool_fails_closed_and_the_session_is_the_callers_own():
    from mcp.client import Client

    server = build_scheduling_server(configured(), FakeSlots(ranked=(cand(1),)))
    async with Client(server) as client:  # no session_context: no signed-in person
        result = await client.call_tool("rank_waitlist", ASK)
    assert result.is_error is True
    with pytest.raises(NoSession):
        current_session()
    other = uuid.uuid4()
    async with session_context(other, FakeConn(), "test"):  # type: ignore[arg-type]
        assert current_session().account_id == other
    with pytest.raises(NoSession):
        current_session()  # restored on exit


# ---- the kit's rules ----------------------------------------------------------------------------------------------------------------------------


async def test_a_sync_tool_cannot_be_registered_and_the_scheduling_server_has_only_async_read_tools():
    from mcp.client import Client
    from mcp.server.mcpserver import MCPServer

    def sync_tool() -> str:
        return "x"

    with pytest.raises(NotAsyncTool):
        async_tool(MCPServer("t"))(sync_tool)
    async with Client(build_scheduling_server(configured(), FakeSlots())) as client:
        names = sorted(t.name for t in (await client.list_tools()).tools)
    assert names == ["find_slots", "propose_offer", "rank_waitlist"]  # no write tool exists yet


async def test_a_tool_exception_text_reaches_neither_the_client_nor_a_log_record_through_the_filter(caplog):
    from mcp.client import Client
    from mcp.server.mcpserver import MCPServer

    server = MCPServer("t")

    @async_tool(server)
    async def failing() -> Envelope:
        raise ValueError(f"patient said {SENTINEL}")

    caplog.set_level(logging.DEBUG)
    flt = WithholdSdkDetail()
    caplog.handler.addFilter(flt)
    try:
        async with Client(server) as client:
            result = await client.call_tool("failing", {})
    finally:
        caplog.handler.removeFilter(flt)
    assert result.is_error and SENTINEL not in str(result.content)
    rendered = [caplog.handler.format(r) for r in caplog.records]
    assert any(FIXED_LOG_MESSAGE in text for text in rendered) and not any(SENTINEL in text for text in rendered)


async def test_calling_a_server_in_process_opens_no_socket(monkeypatch):
    opened = []
    original = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a, **k: (opened.append(a), original(self, *a, **k))[1])
    monkeypatch.setattr(socket.socket, "bind", lambda self, *a, **k: opened.append(a))
    await call(build_scheduling_server(configured(), FakeSlots(ranked=(cand(1),))), "rank_waitlist", ASK)
    assert opened == []


async def test_the_audit_wrapper_records_who_which_tool_and_the_outcome_never_arguments_or_results(monkeypatch):
    written = []

    async def fake_write_audit(conn, **kw):
        written.append(kw)
        return True

    monkeypatch.setattr("careflow.mcp_kit.write_audit", fake_write_audit)

    @audited("read_something", "nurse", lambda s: CLINIC)
    async def tool(secret_argument: str) -> Envelope:
        return Envelope.ok({"echo": "closed"})

    async with session_context(ACCOUNT, FakeConn(), "test"):  # type: ignore[arg-type]
        result = await tool(SENTINEL)
    assert result.status == "ok"
    (row,) = written
    assert (row["actor_id"], row["actor_role"], row["action"], row["clinic_id"]) == (ACCOUNT, "nurse", "clinical.tool_call", CLINIC)
    assert row["details"] == {"tool": "read_something", "outcome": "ok"} and SENTINEL not in str(row)

    written.clear()

    @audited("read_something", "nurse", lambda s: CLINIC)
    async def failing() -> Envelope:
        raise RuntimeError("boom")

    async with session_context(ACCOUNT, FakeConn(), "test"):  # type: ignore[arg-type]
        with pytest.raises(RuntimeError):
            await failing()
    assert written[0]["details"]["outcome"] == "refused"  # an error is still recorded
