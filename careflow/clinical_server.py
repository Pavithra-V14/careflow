"""The `clinical` MCP server: READ-ONLY, FIELD-FILTERED and AUDITED (BUILD-SPEC.md 8; docs/design/phase3-scheduling-agent.md section 4). In-process only, mounted beside the `scheduling` server; the SDK's in-memory
transport is the only way in. A read tool must be PHYSICALLY INCAPABLE of writing, so this is a separate server in a separate module that issues only SELECTs through the caller's own session (row-level
security still decides what the caller may see) and imports no write path; `tests/test_clinical_server.py` pins that by reading the source.

* FIELD FILTERING: every call declares the WORKFLOW it serves, and the workflow's scope (a table in this module, DATA not branches) names the exact fields each tool may return for it. A field outside the
  scope is never selected, never returned. An unknown workflow is `unavailable`, never a default scope.
* LOAD-BEARING OR DECORATIVE per tool per workflow (spec 8, 16.2): `missing_data_policy(workflow, tool)` says whether an empty or unavailable result means "refuse and escalate to a human" (load-bearing: the
  default for any pair not listed, because unknown is not decorative) or "proceed and note the gap" (decorative). The calling step must branch on the status and this policy.
* EVERY CALL IS AUDITED (who, role, tool, outcome; never arguments or results) in the caller's transaction through `mcp_kit.audited`; if the audit row cannot be written the call fails.
* The results hold ids, kinds, closed words and times only: no name, date of birth, phone, message body or free text exists in any field below.
"""

import contextvars
import uuid
from typing import Any, Final, Literal

from mcp.server.mcpserver import MCPServer
from sqlalchemy import text

from careflow.mcp_kit import Envelope, Session, async_tool, audited, current_session

MAX_LIMIT: Final = 50
READ_ROLES: Final = ("front_desk", "nurse", "doctor")  # who may use the clinical read tools at all (the same readers as the message and patient routes)
_CLINIC: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar("careflow_clinical_clinic", default=None)

# workflow -> tool -> the fields that tool may return for that workflow. Engineering's mapping of what each step needs; it holds no clinical content.
SCOPES: Final[dict[str, dict[str, frozenset[str]]]] = {
    "scheduling_reply": {
        "open_tasks": frozenset({"kind", "priority", "status"}),
        "bookings": frozenset({"booking_id", "slot_id", "status"}),
        "waitlist": frozenset({"waitlist_id", "visit_type_id", "priority_code"}),
    },
    "triage_review": {
        "open_tasks": frozenset({"task_id", "kind", "priority", "status", "created_at"}),
        "bookings": frozenset({"booking_id", "status"}),
        "waitlist": frozenset({"waitlist_id", "priority_code", "joined_at"}),
    },
}
# (workflow, tool) pairs that are DECORATIVE: a missing result does not stop the step. Everything else is load-bearing.
DECORATIVE: Final = frozenset({("scheduling_reply", "bookings"), ("scheduling_reply", "waitlist")})

Workflow = Literal["scheduling_reply", "triage_review"]
MissingDataPolicy = Literal["refuse_and_escalate", "proceed_and_note_gap"]


def missing_data_policy(workflow: str, tool: str) -> MissingDataPolicy:
    return "proceed_and_note_gap" if (workflow, tool) in DECORATIVE else "refuse_and_escalate"


async def _acting_role(session: Session) -> str:
    """The clinical role the caller actually holds in the clinic this call named (nurse, doctor or front desk), for the audit row. None held: the call fails."""
    row = (
        await session.conn.execute(
            text("select r from unnest(iam.effective_roles(:c)) as r where r = any(cast(:roles as text[])) order by r limit 1"), {"c": _clinic_of(session), "roles": list(READ_ROLES)}
        )
    ).first()
    if row is None:
        raise PermissionError("no clinical role in this clinic")
    return row[0]


def _clinic_of(_session: Session) -> uuid.UUID:
    clinic = _CLINIC.get()
    if clinic is None:
        raise LookupError("no clinic was named")  # the audit row needs one: a call that never named a clinic fails closed
    return clinic


def _scope(workflow: str, tool: str) -> frozenset[str] | None:
    return SCOPES.get(workflow, {}).get(tool)


def _filtered(rows: list[dict[str, Any]], fields: frozenset[str]) -> list[dict[str, Any]]:
    return [{k: v for k, v in row.items() if k in fields} for row in rows]


def build_clinical_server() -> MCPServer:
    """The in-process `clinical` server. Every tool is `async def`, reads only through the caller's session, and returns an Envelope."""
    server = MCPServer("clinical")

    @async_tool(server)
    @audited("open_tasks", _acting_role, _clinic_of)
    async def open_tasks(clinic_id: uuid.UUID, patient_id: uuid.UUID, workflow: str, limit: int = 20) -> Envelope:
        """READ-ONLY. The patient's open review tasks (kind, priority, status), for the named workflow's scope."""
        _CLINIC.set(clinic_id)
        fields = _scope(workflow, "open_tasks")
        if fields is None:
            return Envelope.unavailable("unknown_workflow")
        rows = (
            await current_session().conn.execute(
                text(
                    "select id, kind, priority, status, created_at from agents.review_tasks where clinic_id = :c and provenance ->> 'patient_id' = :p and status <> 'resolved' "
                    "order by created_at desc, id limit :n"
                ),
                {"c": clinic_id, "p": str(patient_id), "n": max(1, min(limit, MAX_LIMIT))},
            )
        ).all()
        data = [{"task_id": str(r.id), "kind": r.kind, "priority": r.priority, "status": r.status, "created_at": r.created_at.isoformat()} for r in rows]
        return Envelope.ok(_filtered(data, fields)) if data else Envelope.empty("no_open_tasks")

    @async_tool(server)
    @audited("bookings", _acting_role, _clinic_of)
    async def bookings(clinic_id: uuid.UUID, patient_id: uuid.UUID, workflow: str, limit: int = 20) -> Envelope:
        """READ-ONLY. The patient's bookings (ids and status), for the named workflow's scope."""
        _CLINIC.set(clinic_id)
        fields = _scope(workflow, "bookings")
        if fields is None:
            return Envelope.unavailable("unknown_workflow")
        rows = (
            await current_session().conn.execute(
                text("select id, slot_id, status from sched.bookings where clinic_id = :c and patient_id = :p order by created_at desc, id limit :n"),
                {"c": clinic_id, "p": patient_id, "n": max(1, min(limit, MAX_LIMIT))},
            )
        ).all()
        data = [{"booking_id": str(r.id), "slot_id": str(r.slot_id), "status": r.status} for r in rows]
        return Envelope.ok(_filtered(data, fields)) if data else Envelope.empty("no_bookings")

    @async_tool(server)
    @audited("waitlist", _acting_role, _clinic_of)
    async def waitlist(clinic_id: uuid.UUID, patient_id: uuid.UUID, workflow: str, limit: int = 20) -> Envelope:
        """READ-ONLY. The patient's waitlist entries (visit type, priority level), for the named workflow's scope."""
        _CLINIC.set(clinic_id)
        fields = _scope(workflow, "waitlist")
        if fields is None:
            return Envelope.unavailable("unknown_workflow")
        rows = (
            await current_session().conn.execute(
                text("select id, visit_type_id, priority_code, joined_at from sched.waitlist where clinic_id = :c and patient_id = :p and status = 'waiting' order by joined_at, id limit :n"),
                {"c": clinic_id, "p": patient_id, "n": max(1, min(limit, MAX_LIMIT))},
            )
        ).all()
        data = [{"waitlist_id": str(r.id), "visit_type_id": str(r.visit_type_id), "priority_code": r.priority_code, "joined_at": r.joined_at.isoformat()} for r in rows]
        return Envelope.ok(_filtered(data, fields)) if data else Envelope.empty("no_waitlist_entries")

    return server
