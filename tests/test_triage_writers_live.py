"""The REAL SQL writers (careflow/triage_store.py) against the real tables, as the restricted `careflow_app` role with row-level security in the path (migrations 0017, 0018, 0020).

The offline tests prove the Python side with a recording fake; this proves the statements actually fit the schema: the review-task insert (kind, priority, provenance jsonb,
source_message_id, ON CONFLICT on the global key), the proposal insert with its composite foreign keys, the decision insert with decided_by = auth.uid(), and the task resolve.
One never-committed outer transaction; each test in its own savepoint. Migrations 0017/0018/0020 are executed inside it if not applied yet.
"""

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.db import set_request_claims
from careflow.triage import ApprovalMode
from careflow.triage_store import (
    DecisionConflict,
    NotPermitted,
    SqlHoldTaskWriter,
    SqlReviewRecorder,
    SqlTriageProposer,
    TriageStoreError,
    proposal_id_for,
)
from tests.db_helpers import ensure_app_role_sa

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
SQL = {n: (ROOT / "db" / "migrations" / f).read_text(encoding="utf-8") for n, f in (
    ("0017", "0017_docs_documents.sql"), ("0018", "0018_agents_triage_proposals_and_decisions.sql"), ("0020", "0020_referral_hold_review_tasks.sql"))}

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]


def _u() -> uuid.UUID:
    return uuid.uuid4()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        await ensure_app_role_sa(conn)
        if (await conn.execute(text("select to_regclass('docs.documents') is null"))).scalar_one():
            await raw(conn, SQL["0017"])
        if (await conn.execute(text("select to_regclass('agents.triage_proposals') is null"))).scalar_one():
            await raw(conn, SQL["0018"])
        if (await conn.execute(text("select count(*) = 0 from pg_policies where tablename = 'review_tasks' and qual like '%referral_hold%'"))).scalar_one():
            await raw(conn, SQL["0020"])
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def db(_conn):
    sp = await _conn.begin_nested()
    try:
        yield _conn
    finally:
        await sp.rollback()


class World:
    pass


async def world(conn) -> World:
    w = World()
    w.a, w.patient, w.doc = _u(), _u(), _u()
    roster = [("worker", "queue_worker"), ("nurse", "nurse"), ("nurse2", "nurse"), ("fd", "front_desk")]
    w.people = {k: _u() for k, _ in roster}
    await raw(
        conn,
        f"insert into iam.clinics (id, name, origin) values ('{w.a}', 'Writers A', 'test');\n"
        "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{w.people[k]}', 'staff', 'active', 'test')" for k, _ in roster)
        + ";\ninsert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{w.people[k]}', '{w.a}', '{r}', 'test')" for k, r in roster)
        + f";\ninsert into mpi.patients (id, clinic_id, name_ct, name_key_version, dob_ct, dob_key_version, origin, idempotency_key) "
        f"values ('{w.patient}', '{w.a}', '\\x01'::bytea, 1, '\\x01'::bytea, 1, 'test', 'pat-{w.patient}');\n"
        f"insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
        f"values ('{w.doc}', '{w.a}', 'test', 'referral', 'clinic/{w.a}/doc/{w.doc}', 'application/pdf', 1000, '{'a' * 64}', '{w.people['fd']}', 'd-{w.doc}');",
    )
    return w


async def as_user(conn, uid):
    await conn.exec_driver_sql("set local role careflow_app")
    await set_request_claims(conn, {"sub": str(uid), "role": "authenticated"})


async def one(conn, sql, **params):
    return (await conn.execute(text(sql), params)).first()


async def test_the_hold_writer_runs_against_the_real_table_as_the_worker_and_is_idempotent(db):
    w = await world(db)
    writer = SqlHoldTaskWriter(priority="standard")  # a test input
    async with db.begin_nested():
        await as_user(db, w.people["worker"])
        first = await writer.raise_hold(db, document_id=w.doc, clinic_id=w.a, origin="test", assigned_role="front_desk", status="needs_front_desk",
                                        reasons=["dob_missing", "not a known reason"], idempotency_key=f"hold:{w.doc}:needs_front_desk")
        again = await writer.raise_hold(db, document_id=w.doc, clinic_id=w.a, origin="test", assigned_role="front_desk", status="needs_front_desk",
                                        reasons=["dob_missing"], idempotency_key=f"hold:{w.doc}:needs_front_desk")
        await db.exec_driver_sql("reset role")
    assert first == again
    row = await one(db, "select kind, priority, assigned_role, source_message_id, provenance from agents.review_tasks where id = :i", i=first)
    assert (row.kind, row.priority, row.assigned_role, row.source_message_id) == ("referral_hold", "standard", "front_desk", f"document:{w.doc}")
    assert row.provenance == {"status": "needs_front_desk", "reasons": ["dob_missing"]}  # fixed words only


async def test_front_desk_cannot_raise_a_hold_the_writer_reports_not_permitted(db):
    w = await world(db)
    with pytest.raises(NotPermitted):
        async with db.begin_nested():
            await as_user(db, w.people["fd"])
            await SqlHoldTaskWriter(priority="standard").raise_hold(
                db, document_id=w.doc, clinic_id=w.a, origin="test", assigned_role="nurse", status="held_unassessable", reasons=[], idempotency_key="hold:x"
            )


async def test_proposal_then_decision_then_task_resolved_with_replays_and_conflicts(db):
    w = await world(db)
    proposer = SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role="nurse", task_priority="standard")  # test inputs
    key = f"triage:{w.doc}"
    async with db.begin_nested():
        await as_user(db, w.people["worker"])
        proposal = await proposer.propose(db, document_id=w.doc, clinic_id=w.a, origin="test", patient_id=w.patient,
                                          candidates=[{"card_id": "c1", "version": 2, "title": "must not be stored"}], abstained=False, idempotency_key=key)
        replay = await proposer.propose(db, document_id=w.doc, clinic_id=w.a, origin="test", patient_id=w.patient,
                                        candidates=[{"card_id": "c1", "version": 2}], abstained=False, idempotency_key=key)
        await db.exec_driver_sql("reset role")
    assert proposal == replay and proposal.proposal_id == proposal_id_for(w.a, key)
    stored = await one(db, "select candidates, abstained, mode, review_task_id from agents.triage_proposals where id = :i", i=proposal.proposal_id)
    assert stored.candidates == [{"card_id": "c1", "version": 2}] and stored.abstained is False and stored.mode == "assisted" and stored.review_task_id == proposal.task_id

    recorder = SqlReviewRecorder()
    async with db.begin_nested():
        await as_user(db, w.people["nurse"])
        await recorder.record(db, proposal_id=proposal.proposal_id, clinic_id=w.a, decision="approve", reviewer_id=w.people["nurse"], idempotency_key=f"review:{w.doc}")
        await recorder.record(db, proposal_id=proposal.proposal_id, clinic_id=w.a, decision="approve", reviewer_id=w.people["nurse"], idempotency_key=f"review:{w.doc}")  # replay
        await db.exec_driver_sql("reset role")
    assert (await one(db, "select count(*) as n from agents.review_decisions where proposal_id = :p", p=proposal.proposal_id)).n == 1
    task = await one(db, "select status, acknowledged_by from agents.review_tasks where id = :i", i=proposal.task_id)
    assert task.status == "resolved" and task.acknowledged_by == w.people["nurse"]

    with pytest.raises(DecisionConflict):  # a second decision on the same proposal, under another key
        async with db.begin_nested():
            await as_user(db, w.people["nurse2"])
            await recorder.record(db, proposal_id=proposal.proposal_id, clinic_id=w.a, decision="reject", reviewer_id=w.people["nurse2"], idempotency_key="other")


async def test_a_decision_naming_somebody_else_and_a_non_clinical_role_are_refused(db):
    w = await world(db)
    proposer = SqlTriageProposer(mode=ApprovalMode.SHADOW, assigned_role="doctor", task_priority="immediate")
    async with db.begin_nested():
        await as_user(db, w.people["worker"])
        proposal = await proposer.propose(db, document_id=w.doc, clinic_id=w.a, origin="test", patient_id=w.patient, candidates=[], abstained=True, idempotency_key="triage:y")
        await db.exec_driver_sql("reset role")
    for who, claimed in (("nurse", "nurse2"), ("fd", "fd"), ("worker", "worker")):
        with pytest.raises((NotPermitted, TriageStoreError)):  # front desk cannot even see the proposal: one answer for missing and invisible
            async with db.begin_nested():
                await as_user(db, w.people[who])
                await SqlReviewRecorder().record(db, proposal_id=proposal.proposal_id, clinic_id=w.a, decision="approve", reviewer_id=w.people[claimed], idempotency_key=f"k-{who}")
    assert (await one(db, "select count(*) as n from agents.review_decisions")).n == 0
