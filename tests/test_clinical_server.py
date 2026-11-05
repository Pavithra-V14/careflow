"""The `clinical` server (careflow/clinical_server.py), offline: a scripted connection, the SDK's in-memory transport. Proves it is physically read-only (by reading its source), every call is audited with the role
actually held (and a caller with none is refused), a workflow's scope filters the fields, an unknown workflow is unavailable, and the load-bearing default."""

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from careflow import clinical_server
from careflow.clinical_server import (
    DECORATIVE,
    SCOPES,
    build_clinical_server,
    missing_data_policy,
)
from careflow.mcp_kit import Envelope, invoke

pytestmark = pytest.mark.asyncio
ACCOUNT, CLINIC, PATIENT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
NOW = datetime(2030, 1, 1, tzinfo=UTC)
ASK = {"clinic_id": str(CLINIC), "patient_id": str(PATIENT)}


class Rows:
    def __init__(self, rows=(), first=None):
        self._rows, self._first = list(rows), first

    def all(self):
        return self._rows

    def first(self):
        return self._first


class Conn:
    def __init__(self, role="nurse", tasks=(), bookings=(), waiting=()):
        self.role, self.tasks, self.bookings, self.waiting, self.statements = role, tasks, bookings, waiting, []

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "unnest(iam.effective_roles" in sql:
            return Rows(first=(self.role,) if self.role else None)
        if "from agents.review_tasks" in sql:
            return Rows(self.tasks)
        if "from sched.bookings" in sql:
            return Rows(self.bookings)
        if "from sched.waitlist" in sql:
            return Rows(self.waiting)
        raise AssertionError(sql)


@pytest.fixture
def audits(monkeypatch):
    rows = []

    async def write_audit(conn, **kw):
        rows.append(kw)
        return True

    monkeypatch.setattr("careflow.mcp_kit.write_audit", write_audit)
    return rows


TASK = SimpleNamespace(id=uuid.uuid4(), kind="referral_hold", priority="standard", status="open", created_at=NOW)
BOOKING = SimpleNamespace(id=uuid.uuid4(), slot_id=uuid.uuid4(), status="confirmed")
ENTRY = SimpleNamespace(id=uuid.uuid4(), visit_type_id=uuid.uuid4(), priority_code="p1", joined_at=NOW)


async def call(tool, conn, **over):
    return await invoke(build_clinical_server(), tool, {**ASK, "workflow": "scheduling_reply", **over}, account_id=ACCOUNT, conn=conn, origin="test")


async def test_the_server_is_read_only_by_its_source_it_issues_only_selects_and_imports_no_write_path():
    source = Path(clinical_server.__file__).read_text(encoding="utf-8")
    sql = re.findall(r'text\(\s*"([^"]+)"', source)
    assert sql and all(s.lstrip().lower().startswith("select") for s in sql)
    assert not re.search(r"\b(insert|update|delete|truncate|alter|create|drop|grant)\s", " ".join(sql), re.IGNORECASE)
    imports = " ".join(re.findall(r"^(?:from|import) .*$", source, re.MULTILINE))
    assert "scheduling import" not in imports and "SlotManager" not in source and "write_audit" not in source  # the audit goes through mcp_kit.audited, nothing else writes


async def test_the_three_tools_return_ids_kinds_and_closed_words_filtered_by_the_workflow_scope(audits):
    conn = Conn(tasks=[TASK], bookings=[BOOKING], waiting=[ENTRY])
    tasks, bookings, waiting = await call("open_tasks", conn), await call("bookings", conn), await call("waitlist", conn)
    assert tasks.status == "ok" and set(tasks.data[0]) == SCOPES["scheduling_reply"]["open_tasks"] == {"kind", "priority", "status"}  # no task id, no time: outside this workflow's scope
    assert set(bookings.data[0]) == {"booking_id", "slot_id", "status"} and set(waiting.data[0]) == {"waitlist_id", "visit_type_id", "priority_code"}
    wide = await call("open_tasks", conn, workflow="triage_review")
    assert set(wide.data[0]) == {"task_id", "kind", "priority", "status", "created_at"}


async def test_an_empty_result_is_an_explicit_empty_status_with_a_reason_never_a_bare_list(audits):
    for tool, reason in (("open_tasks", "no_open_tasks"), ("bookings", "no_bookings"), ("waitlist", "no_waitlist_entries")):
        r = await call(tool, Conn())
        assert (r.status, r.reason, r.data) == ("empty", reason, [])


async def test_an_unknown_workflow_is_unavailable_and_nothing_is_read(audits):
    conn = Conn(tasks=[TASK])
    r = await call("open_tasks", conn, workflow="free_for_all")
    assert (r.status, r.reason) == ("unavailable", "unknown_workflow") and not any("agents.review_tasks" in s for s in conn.statements)


async def test_every_call_writes_one_audit_row_naming_the_role_actually_held_never_arguments_or_results(audits):
    await call("open_tasks", Conn(role="doctor", tasks=[TASK]))
    await call("bookings", Conn(role="front_desk"))
    assert [(a["actor_role"], a["details"], a["clinic_id"], a["action"]) for a in audits] == [
        ("doctor", {"tool": "open_tasks", "outcome": "ok"}, CLINIC, "clinical.tool_call"),
        ("front_desk", {"tool": "bookings", "outcome": "empty"}, CLINIC, "clinical.tool_call"),
    ]
    assert str(PATIENT) not in str(audits)


async def test_a_caller_who_holds_no_clinical_role_in_that_clinic_gets_nothing_and_the_call_fails(audits):
    r = await call("open_tasks", Conn(role=None, tasks=[TASK]))
    assert isinstance(r, Envelope) and r.status == "unavailable" and r.reason == "tool_error" and audits == []


async def test_load_bearing_is_the_default_and_decorative_is_declared_per_workflow_and_tool():
    assert missing_data_policy("scheduling_reply", "open_tasks") == "refuse_and_escalate"  # a task read gates the step
    assert missing_data_policy("scheduling_reply", "bookings") == "proceed_and_note_gap" and ("scheduling_reply", "bookings") in DECORATIVE
    assert missing_data_policy("triage_review", "bookings") == "refuse_and_escalate"  # the same tool is load-bearing for another workflow
    assert missing_data_policy("anything_new", "anything_new") == "refuse_and_escalate"  # unknown is not decorative
