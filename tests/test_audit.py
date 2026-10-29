"""Audit rows for human decisions (careflow/audit.py). Offline: a fake connection. The live checks (the rows really commit with the decision, as the signed-in person, ids only) are in the
role-matrix module and tests/test_api_referrals_live.py."""

import inspect
import re
import uuid
from pathlib import Path

import pytest

from careflow import app as app_module
from careflow.audit import ACTIONS, AuditDetailRefused, AuditError, write_audit

pytestmark = pytest.mark.asyncio
CLINIC, ACTOR, RES = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class Conn:
    def __init__(self, rowcount=1):
        self.calls, self.rowcount = [], rowcount

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return Result(self.rowcount)


KW = {
    "clinic_id": CLINIC, "origin": "test", "actor_id": ACTOR, "actor_role": "front_desk", "action": "merge.propose", "resource_type": "merge", "resource_id": RES,
    "idempotency_key": "audit:k-1",
}


async def test_one_row_is_written_idempotently_as_the_actor_with_ids_and_closed_words_only():
    conn = Conn()
    assert await write_audit(conn, details={"master_patient_id": uuid.uuid4(), "decision": "approve", "replayed": False, "n": 2}, **KW) is True  # type: ignore[arg-type]
    ((sql, p),) = conn.calls
    assert "insert into audit.log" in sql and "on conflict (idempotency_key) do nothing" in sql and "returning" not in sql  # RETURNING would need SELECT, which only auditors hold
    assert p["a"] == ACTOR and p["c"] == CLINIC and p["k"] == "audit:k-1" and p["ri"] == str(RES) and p["r"] == "front_desk"


async def test_a_replay_writes_no_second_row():
    assert await write_audit(Conn(rowcount=0), details={}, **KW) is False  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["Asha Rao", "5 March 1980", "98765 43210", "a free sentence", "x" * 65, "UPPER", "", None, 1.5, ["a"], {"a": 1}])
async def test_free_text_and_anything_that_is_not_an_id_a_number_a_boolean_or_a_token_is_refused_before_a_query(bad):
    conn = Conn()
    with pytest.raises(AuditDetailRefused):
        await write_audit(conn, details={"note": bad}, **KW)  # type: ignore[arg-type]
    assert conn.calls == []


async def test_the_action_origin_key_and_tokens_are_closed():
    for override in ({"action": "patient.erase"}, {"origin": "production"}, {"idempotency_key": "  "}, {"resource_type": "A merge"}, {"actor_role": "Front Desk"}):
        conn = Conn()
        with pytest.raises(AuditError):
            await write_audit(conn, details={}, **{**KW, **override})  # type: ignore[arg-type]
        assert conn.calls == []
    with pytest.raises(AuditDetailRefused):
        await write_audit(Conn(), details={"Bad Key": "x"}, **KW)  # type: ignore[arg-type]


async def test_a_failing_insert_propagates_so_the_decision_does_not_commit():
    class Broken(Conn):
        async def execute(self, statement, params=None):
            raise RuntimeError("audit unavailable")

    with pytest.raises(RuntimeError):
        await write_audit(Broken(), details={}, **KW)  # type: ignore[arg-type]


# ---- the gate: a human-decision route cannot lose its audit call ----------------------------------------------------------------------------------------

ROUTES = {
    "review_referral": "referral.review_decision",
    "propose_merge": "merge.propose",
    "approve_merge": "merge.approve",
    "unmerge": "merge.unmerge",
    "confirm_patient_identity": "patient.confirm_identity",
}


@pytest.mark.parametrize(("function", "action"), sorted(ROUTES.items()))
async def test_each_human_decision_route_writes_its_audit_row_in_the_same_transaction(function, action):
    source = inspect.getsource(getattr(app_module, function))
    assert "await write_audit(" in source and f'action="{action}"' in source
    assert "idempotency_key=f\"audit:" in source  # keyed on the request's idempotency key, so a replay writes nothing more
    assert source.index("await write_audit(") > source.index("await ")  # after the decision itself, on the same connection `conn` (the request's transaction)
    assert re.search(r"write_audit\(\s*conn,", source)


async def test_every_action_has_a_route_or_is_listed_as_pending():
    pending = {"clinical.tool_call"}  # written by careflow/mcp_kit.py `audited` (no clinical server is mounted yet)
    assert set(ROUTES.values()) | pending == set(ACTIONS)


async def test_the_audit_module_adds_no_dependency_and_uses_only_the_existing_table():
    text = Path(app_module.__file__).with_name("audit.py").read_text(encoding="utf-8")
    assert "audit.log" in text and "create " not in text.lower().replace("create_", "") and "grant " not in text.lower()
