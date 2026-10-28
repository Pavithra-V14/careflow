"""The SQL writers (careflow/triage_store.py) against a recording fake connection. Offline. The real constraints and policies are exercised by
tests/test_rls_triage_live.py (CI only); this file proves the Python side: idempotency keys, ids only, clean errors."""

import json
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.exc import DBAPIError

from careflow.triage import ApprovalMode
from careflow.triage_store import (
    DecisionConflict,
    NotPermitted,
    SqlReviewRecorder,
    SqlTriageProposer,
    TriageStoreError,
    proposal_id_for,
)

pytestmark = pytest.mark.asyncio
CLINIC, DOC, PATIENT, TASK, REVIEWER = (uuid.uuid4() for _ in range(5))
KEY = f"triage:{DOC}"


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Result:
    def __init__(self, row=None):
        self._row = row

    def first(self):
        return self._row


def db_error(state):
    class Orig(Exception):
        sqlstate = state

    return DBAPIError("stmt", {}, Orig("x"))


class Conn:
    error_state = "XX000"

    def __init__(self, *, task_new=True, proposal_new=True, fail_on=None, decision_new=True, same_decision=True, proposal_visible=True):
        self.task_new, self.proposal_new, self.fail_on = task_new, proposal_new, fail_on
        self.decision_new, self.same_decision, self.proposal_visible = decision_new, same_decision, proposal_visible
        self.calls: list[tuple[str, dict]] = []

    def begin_nested(self):
        @asynccontextmanager
        async def cm():
            yield self

        return cm()

    async def execute(self, statement, params=None):
        sql, params = str(statement), params or {}
        self.calls.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise db_error(self.error_state)
        if "insert into agents.review_tasks" in sql:
            return Result(Row(id=TASK) if self.task_new else None)
        if "select id from agents.review_tasks" in sql:
            return Result(Row(id=TASK))
        if "insert into agents.triage_proposals" in sql:
            return Result(None)
        if "from agents.triage_proposals where clinic_id" in sql:
            return Result(Row(id=proposal_id_for(CLINIC, KEY), review_task_id=TASK))
        if "select origin, review_task_id" in sql:
            return Result(Row(origin="test", review_task_id=TASK) if self.proposal_visible else None)
        if "insert into agents.review_decisions" in sql:
            return Result(Row(id=uuid.uuid4()) if self.decision_new else None)
        if "from agents.review_decisions" in sql:
            return Result(Row() if self.same_decision else None)
        if "update agents.review_tasks" in sql:
            return Result(None)
        raise AssertionError(sql)


def proposer(role="nurse", priority="standard"):  # test inputs
    return SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role=role, task_priority=priority)


async def propose(conn, **over):
    args = {"document_id": DOC, "clinic_id": CLINIC, "origin": "test", "patient_id": PATIENT, "candidates": [{"card_id": "c1", "version": 2}], "abstained": False,
            "idempotency_key": KEY}
    args.update(over)
    return await proposer().propose(conn, **args)


async def test_a_proposal_writes_a_task_and_a_proposal_with_ids_only_and_returns_the_deterministic_id():
    conn = Conn()
    result = await propose(conn, candidates=[{"card_id": "c1", "version": 2, "title": "SENTINEL-CARD-TEXT", "note": "SENTINEL-PATIENT"}])
    assert result.proposal_id == proposal_id_for(CLINIC, KEY) and result.task_id == TASK
    task_sql, task_params = next(c for c in conn.calls if "insert into agents.review_tasks" in c[0])
    assert "'triage_proposal'" in task_sql and task_params["pr"] == "standard" and "'standard'" not in task_sql  # the priority is an input, never a literal
    assert task_params["k"] == f"triage-task:{DOC}" and task_params["r"] == "nurse" and task_params["s"] == f"document:{DOC}"
    _, prop_params = next(c for c in conn.calls if "insert into agents.triage_proposals" in c[0])
    assert json.loads(prop_params["cand"]) == [{"card_id": "c1", "version": 2}] and prop_params["k"] == KEY and prop_params["m"] == "assisted"
    assert "SENTINEL" not in json.dumps([c[1] for c in conn.calls], default=str)


async def test_there_is_no_priority_anywhere_in_what_is_written():
    conn = Conn()
    await propose(conn)
    for sql, _ in conn.calls:
        assert "triage_priority" not in sql and "clinical_priority" not in sql


async def test_a_replay_finds_the_existing_task_and_proposal_and_writes_nothing_twice():
    conn = Conn(task_new=False)
    result = await propose(conn)
    assert result.task_id == TASK
    assert sum("insert into agents.triage_proposals" in c[0] and "on conflict (clinic_id, idempotency_key) do nothing" in c[0] for c in conn.calls) == 1


async def test_the_role_that_reviews_has_no_default_and_must_be_clinical():
    with pytest.raises(TypeError):
        SqlTriageProposer(mode=ApprovalMode.ASSISTED, task_priority="standard")  # type: ignore[call-arg]
    for bad in ("front_desk", "admin", "", None):
        with pytest.raises(ValueError):
            SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role=bad, task_priority="standard")  # type: ignore[arg-type]


async def test_a_proposal_needs_a_patient_and_a_key():
    with pytest.raises(TriageStoreError):
        await propose(Conn(), patient_id=None)
    with pytest.raises(TriageStoreError):
        await propose(Conn(), idempotency_key="  ")


async def test_row_level_security_and_a_second_proposal_for_the_document_become_clean_errors():
    conn = Conn(fail_on="insert into agents.triage_proposals")
    conn.error_state = "42501"
    with pytest.raises(NotPermitted):
        await propose(conn)
    conn = Conn(fail_on="insert into agents.triage_proposals")
    conn.error_state = "23505"
    with pytest.raises(TriageStoreError):
        await propose(conn)


async def test_a_decision_is_recorded_once_for_the_reviewer_and_the_task_is_resolved():
    conn = Conn()
    await SqlReviewRecorder().record(conn, proposal_id=proposal_id_for(CLINIC, KEY), clinic_id=CLINIC, decision="approve", reviewer_id=REVIEWER, idempotency_key=f"review:{DOC}")
    _, ins = next(c for c in conn.calls if "insert into agents.review_decisions" in c[0])
    assert ins["u"] == REVIEWER and ins["d"] == "approve" and ins["k"] == f"review:{DOC}" and ins["o"] == "test"
    _, upd = next(c for c in conn.calls if "update agents.review_tasks" in c[0])
    assert upd["u"] == REVIEWER and upd["t"] == TASK


async def test_a_replayed_decision_is_a_no_op_and_a_different_one_under_the_same_key_is_a_conflict():
    pid = proposal_id_for(CLINIC, KEY)
    conn = Conn(decision_new=False)
    await SqlReviewRecorder().record(conn, proposal_id=pid, clinic_id=CLINIC, decision="approve", reviewer_id=REVIEWER, idempotency_key="k")
    assert not any("update agents.review_tasks" in c[0] for c in conn.calls)  # nothing resolved twice
    with pytest.raises(DecisionConflict):
        await SqlReviewRecorder().record(Conn(decision_new=False, same_decision=False), proposal_id=pid, clinic_id=CLINIC, decision="reject", reviewer_id=REVIEWER, idempotency_key="k")


async def test_unknown_decisions_invisible_proposals_and_database_refusals_are_clean_errors():
    pid = proposal_id_for(CLINIC, KEY)
    recorder = SqlReviewRecorder()
    with pytest.raises(TriageStoreError):
        await recorder.record(Conn(), proposal_id=pid, clinic_id=CLINIC, decision="approve-all", reviewer_id=REVIEWER, idempotency_key="k")
    with pytest.raises(TriageStoreError):
        await recorder.record(Conn(proposal_visible=False), proposal_id=pid, clinic_id=CLINIC, decision="approve", reviewer_id=REVIEWER, idempotency_key="k")
    for state, error in (("42501", NotPermitted), ("23505", DecisionConflict)):
        conn = Conn(fail_on="insert into agents.review_decisions")
        conn.error_state = state
        with pytest.raises(error):
            await recorder.record(conn, proposal_id=pid, clinic_id=CLINIC, decision="approve", reviewer_id=REVIEWER, idempotency_key="k")
        assert not any("update agents.review_tasks" in c[0] for c in conn.calls)


async def test_the_task_priority_has_no_default_and_must_be_one_the_table_allows():
    with pytest.raises(TypeError):
        SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role="nurse")  # type: ignore[call-arg]
    for bad in ("urgent", "", None):
        with pytest.raises(ValueError):
            SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role="nurse", task_priority=bad)  # type: ignore[arg-type]
    conn = Conn()
    await proposer(priority="immediate").propose(conn, document_id=DOC, clinic_id=CLINIC, origin="test", patient_id=PATIENT, candidates=[], abstained=True, idempotency_key=KEY)
    assert next(c for c in conn.calls if "insert into agents.review_tasks" in c[0])[1]["pr"] == "immediate"


async def test_resolving_a_task_another_reviewer_acknowledged_is_a_clean_refusal_and_the_caller_must_roll_back():
    conn = Conn(fail_on="update agents.review_tasks")
    conn.error_state = "42501"
    with pytest.raises(NotPermitted):
        await SqlReviewRecorder().record(
            conn, proposal_id=proposal_id_for(CLINIC, KEY), clinic_id=CLINIC, decision="approve", reviewer_id=REVIEWER, idempotency_key="k"
        )


# ---- hold tasks: no run ends silently (migration 0020) ----------------------------------------------------------------------------------------


async def test_a_hold_task_is_written_idempotently_with_a_fixed_word_provenance_and_the_priority_is_an_input():
    from careflow.triage_store import SqlHoldTaskWriter

    conn = Conn()
    writer = SqlHoldTaskWriter(priority="standard")  # a test input
    task = await writer.raise_hold(
        conn, document_id=DOC, clinic_id=CLINIC, origin="test", assigned_role="front_desk", status="needs_front_desk",
        reasons=["dob_missing", "SENTINEL name with spaces", "x" * 100, "Venkatesh", "98765", "name_not_a_known_reason"], idempotency_key=f"hold:{DOC}:needs_front_desk",
    )
    assert task == TASK
    sql, params = next(c for c in conn.calls if "insert into agents.review_tasks" in c[0])
    assert "'referral_hold'" in sql and "'standard'" not in sql and params["pr"] == "standard" and params["r"] == "front_desk"
    assert params["s"] == f"document:{DOC}" and params["k"] == f"hold:{DOC}:needs_front_desk"
    stored = json.loads(params["p"])
    assert stored == {"status": "needs_front_desk", "reasons": ["dob_missing"]}  # single-token names, numbers and invented words are dropped too: a closed vocabulary
    assert "SENTINEL" not in json.dumps([c[1] for c in conn.calls], default=str)


async def test_the_hold_priority_has_no_default_roles_are_limited_and_a_replay_finds_the_task():
    from careflow.triage_store import SqlHoldTaskWriter

    with pytest.raises(TypeError):
        SqlHoldTaskWriter()  # type: ignore[call-arg]
    for bad in ("urgent", "", None):
        with pytest.raises(ValueError):
            SqlHoldTaskWriter(priority=bad)  # type: ignore[arg-type]
    writer = SqlHoldTaskWriter(priority="immediate")
    for role in ("doctor", "admin", ""):
        with pytest.raises(TriageStoreError):
            await writer.raise_hold(Conn(), document_id=DOC, clinic_id=CLINIC, origin="test", assigned_role=role, status="s", reasons=[], idempotency_key="k")
    replay = Conn(task_new=False)
    assert await writer.raise_hold(replay, document_id=DOC, clinic_id=CLINIC, origin="test", assigned_role="nurse", status="held_unassessable", reasons=[], idempotency_key="k") == TASK
    refused = Conn(fail_on="insert into agents.review_tasks")
    refused.error_state = "42501"
    with pytest.raises(NotPermitted):
        await writer.raise_hold(refused, document_id=DOC, clinic_id=CLINIC, origin="test", assigned_role="nurse", status="s", reasons=[], idempotency_key="k")
