"""Phase 2 end to end, THROUGH THE ROUTES, against the real database, inside ONE NEVER-COMMITTED transaction (BUILD-SPEC Appendix D, Phase 2: upload and extraction, the Front Door graph, human review).

What is real: the HTTP routes with real signed tokens and the real auth dependency, the upload intake (begin, the browser's PUT, register with the server's own re-verification), the extraction pipeline
reading the stored PDF (pypdf), the red-flag screen, the Front Door graph with the Postgres checkpointer, the queue and the worker handler, the triage tables, the review-task routes, row-level
security under `set local role careflow_app`. What is a stand-in (as everywhere in Phase 2, nothing hosted is approved): the storage (an in-memory fake behind `UploadPort`), the extractor, the
retriever. No model is called.

The clean path needs an approved red-flag rule so a letter is "assessed"; the rule here is a SYNTHETIC token (`zzalpha`), inserted inside the rolled-back transaction; nothing is a clinical term.
Like tests/test_api_referrals_live.py, this suite refuses to run beside a COMMITTED approved origin='test' rule. Nothing is committed; everything is rolled back at the end.
"""

import asyncio
import base64
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from careflow.adapters.fake import FakeUpload
from careflow.adapters.pdf import PypdfExtractor
from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.budget import RunBudget
from careflow.crypto import DemographicVault
from careflow.db import set_request_claims
from careflow.dead_letters import SqlDeadLetterTaskWriter
from careflow.documents import DocumentIntake
from careflow.extraction import ExtractionPipeline, PipelineTextSource
from careflow.front_door_runner import FrontDoorComponents, front_door_handler
from careflow.mpi import PatientIngestionPipeline
from careflow.queue import PostgresQueue
from careflow.rules import RedFlagEngine
from careflow.tick import JobContext, TickClock
from careflow.triage import ApprovalMode
from careflow.triage_store import (
    SqlHoldTaskWriter,
    SqlReviewRecorder,
    SqlTriageProposer,
)
from tests.bounds import bounded
from tests.db_helpers import ensure_app_role_sa
from tests.pdf_factory import pdf
from tests.test_api_referrals_live import GOOD, LETTER, RED_LETTER, Extractor, Retriever
from tests.test_api_role_matrix import PROJECT, bearer, fake_jwks_client, mint

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
BEGIN, REGISTER, QUEUE = "/v1/staff/documents/uploads", "/v1/staff/documents/register", "/v1/staff/referrals"
RUN, REVIEW = QUEUE + "/{}/run", QUEUE + "/{}/review"


def _u() -> uuid.UUID:
    return uuid.uuid4()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        n = (await conn.execute(text(
            "select count(*) from kb.red_flag_rules where origin = 'test' and status = 'approved' and (cardinality(phrases) > 0 or cardinality(patterns) > 0)"
        ))).scalar_one()
        if n:
            pytest.fail(f"This suite assumes the database holds NO committed approved origin='test' red-flag rule with criteria (it inserts its own inside a rolled-back transaction); found {n}. Nothing was deleted.", pytrace=False)
        await ensure_app_role_sa(conn)
        vault = DemographicVault.from_env({f"CAREFLOW_{k}_SECRET_V1": base64.b64encode(os.urandom(32)).decode() for k in ("ENC", "HMAC")})
        a, b = _u(), _u()
        who = [("nurse_a", a, "nurse"), ("doctor_a", a, "doctor"), ("fd_a", a, "front_desk"), ("worker_a", a, "queue_worker"), ("fd_b", b, "front_desk"), ("nurse_b", b, "nurse"), ("worker_b", b, "queue_worker")]
        ids = {k: _u() for k, _, _ in who}
        await raw(
            conn,
            f"insert into iam.clinics (id, name, origin) values ('{a}', 'E2E A', 'test'), ('{b}', 'E2E B', 'test'); "
            "insert into iam.accounts (id, account_type, status, origin) values " + ", ".join(f"('{ids[k]}', 'staff', 'active', 'test')" for k, _, _ in who) + "; "
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, c, r in who) + ";",
        )
        rid = _u()  # a clinic-A-only synthetic rule: clinic B has NO usable rule, so a letter there is unassessable and ends in a hold
        await conn.execute(
            text(
                "insert into kb.red_flag_rules (id, clinic_id, rule_code, version_id, status, phrases, patterns, governance_metadata, authored_by, approved_by_1, approved_by_2, approved_at, origin, idempotency_key) "
                "values (:i, :a, 'e2e_rule', 1, 'approved', array['zzalpha'], '{}', cast('{}' as jsonb), :d, :d, :n, now(), 'test', :k)"
            ),
            {"i": rid, "a": a, "d": ids["doctor_a"], "n": ids["nurse_a"], "k": f"r-{rid}"},
        )
        lock = asyncio.Lock()
        held = {"task": None, "depth": 0}  # the task that holds `lock` now: ONE connection serves everything, and the worker handler's own loader opens a session while the handler's session is open

        @asynccontextmanager
        async def exclusive():
            """Take `lock`, or, if THIS task already holds it, nest inside (asyncio.Lock is not reentrant: the first run in CI hung here for 55 minutes). A wait for the lock is bounded."""
            task = asyncio.current_task()
            if held["task"] is task:
                held["depth"] += 1
                try:
                    yield True
                finally:
                    held["depth"] -= 1
                return
            await asyncio.wait_for(lock.acquire(), 60)
            held["task"], held["depth"] = task, 0
            try:
                yield False
            finally:
                held["task"] = None
                lock.release()

        def worker_session_for(engine_, account):
            @asynccontextmanager
            async def session():
                async with exclusive() as nested:
                    if nested:  # already inside a session of this task (same connection, role and claims): reuse it, and do not reset what the outer one set
                        yield conn
                        return
                    async with conn.begin_nested():
                        await conn.exec_driver_sql("set local role careflow_app")
                        await set_request_claims(conn, {"sub": str(account), "role": "authenticated"})
                        yield conn
                        await conn.exec_driver_sql("reset role")
                        await conn.execute(text("select set_config('request.jwt.claims', '', true)"))

            return session

        uploads = FakeUpload()
        intake = DocumentIntake(uploads, profile="demo")

        async def loader(document_id):  # the worker reads the registered bytes through the intake (re-hash), as the real loader will
            async with worker_session_for(None, ids["worker_a"])() as c:
                doc = await intake.read_verified(c, document_id)
            return doc.content, doc.content_type, doc.clinic_id

        extractor = Extractor()
        components = FrontDoorComponents(
            engine=RedFlagEngine(), texts=PipelineTextSource(ExtractionPipeline(PypdfExtractor()), loader), extractor=extractor,
            ingestor=PatientIngestionPipeline(vault, key_version=1, registrar="function"), retriever=Retriever(),  # type: ignore[arg-type]
            proposer=SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role="nurse", task_priority="standard"),  # test inputs, not clinical values
            recorder=SqlReviewRecorder(), holds=SqlHoldTaskWriter(priority="standard"), dead_letters=SqlDeadLetterTaskWriter(priority="standard"), retrieval_scope={"domain": "stand-in"}, profile="demo",
        )
        app = create_app(
            Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=fake_jwks_client(), engine=object(), vault=vault,  # type: ignore[arg-type]
            front_door=components, front_door_session_for=worker_session_for, document_intake=intake,
        )

        async def shared_db(claims=Depends(verified_claims)):
            async with exclusive(), conn.begin_nested():
                await conn.exec_driver_sql("set local role careflow_app")
                await set_request_claims(conn, claims)
                yield conn
                await conn.exec_driver_sql("reset role")
                await conn.execute(text("select set_config('request.jwt.claims', '', true)"))

        app.dependency_overrides[db_conn] = shared_db
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield type("World", (), {
                    "conn": conn, "client": client, "ids": ids, "a": a, "b": b, "uploads": uploads, "extractor": extractor,
                    "handler": front_door_handler(components, session_for=worker_session_for), "worker_session_for": worker_session_for,
                })
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def w(world):
    sp = await world.conn.begin_nested()
    world.extractor.answers.clear()
    world.extractor.calls.clear()
    try:
        yield world
    finally:
        await sp.rollback()


def headers(w, who, key="new"):
    return {**bearer(mint(w.ids[who])), **({} if key is None else {"X-Idempotency-Key": f"k-{_u()}" if key == "new" else key})}


async def rows(w, sql, **params):
    return (await w.conn.execute(text(sql), params)).all()


async def upload_letter(w, who, clinic, letter):
    """The whole browser journey: a ticket from the route, the PUT to the server-chosen path, then registration under the same key."""
    key = f"up-{_u()}"
    begin = await w.client.post(BEGIN, headers=headers(w, who, key), json={"clinic_id": str(clinic), "kind": "referral", "content_type": "application/pdf"})
    assert begin.status_code == 200, begin.text
    document = uuid.UUID(begin.json()["document_id"])
    w.uploads.client_put(f"clinic/{clinic}/doc/{document}", pdf([(letter, False)]), "application/pdf")
    registered = await w.client.post(REGISTER, headers=headers(w, who, key), json={"clinic_id": str(clinic), "kind": "referral"})
    assert registered.status_code == 200 and registered.json()["document_id"] == str(document), registered.text
    return document


async def run_worker(w, worker):
    queue, session = PostgresQueue(), w.worker_session_for(object(), w.ids[worker])
    async with session() as conn:
        job = await queue.claim(conn, "e2e-worker", 120.0)
    assert job is not None, "no job was queued"
    await bounded(w.handler(JobContext(job=job, budget=RunBudget(TickClock()), engine=object(), worker_account_id=w.ids[worker])), "the front door worker handler", 120.0)  # type: ignore[arg-type]
    async with session() as conn:
        await queue.acknowledge(conn, job.id, worker_id="e2e-worker")


async def referral(w, who, document):
    r = await w.client.get(f"{QUEUE}/{document}", headers=headers(w, who, None))
    assert r.status_code == 200, r.text
    return r.json()


async def test_from_the_browsers_upload_to_a_human_decision_through_the_routes(w):
    document = await upload_letter(w, "fd_a", w.a, LETTER)
    w.extractor.answers[document] = GOOD
    queued = await referral(w, "fd_a", document)
    assert queued["run_state"] is None and queued["open_holds"] == 0 and queued["proposal_visible"] is False  # registered, nothing has run, and front desk cannot see proposals
    started = await w.client.post(RUN.format(document), headers=headers(w, "fd_a"))
    assert started.status_code == 200
    assert (await referral(w, "fd_a", document))["run_state"] == "unclaimed"
    await run_worker(w, "worker_a")
    assert w.extractor.calls == [document]  # the extractor saw the letter that was read from the stored PDF
    done = await referral(w, "nurse_a", document)
    assert done["run_state"] == "completed" and done["proposal_visible"] is True and done["decided"] is False
    assert done["proposal"]["candidates"] == [{"card_id": "stand-in-card", "version": 1}] and done["proposal"]["abstained"] is False
    review = await w.client.post(REVIEW.format(document), headers=headers(w, "nurse_a"), json={"decision": "approve"})
    assert review.status_code == 200 and review.json()["decision"] == "approve" and review.json()["replayed"] is False
    final = await referral(w, "nurse_a", document)
    assert final["decided"] is True
    task = await w.client.get("/v1/staff/review-tasks?status=resolved&kind=triage_proposal", headers=headers(w, "nurse_a", None))
    assert task.status_code == 200 and done["proposal"]["review_task_id"] in {i["task_id"] for i in task.json()["items"]}  # the human decision closed its task


async def test_a_letter_with_a_red_flag_raises_the_immediate_nurse_task_and_no_extractor_is_called(w):
    document = await upload_letter(w, "fd_a", w.a, RED_LETTER)
    w.extractor.answers[document] = GOOD
    assert (await w.client.post(RUN.format(document), headers=headers(w, "fd_a"))).status_code == 200
    await run_worker(w, "worker_a")
    assert w.extractor.calls == []  # rules first, on every path: the escalation ended the run before any model-shaped step
    tasks = (await w.client.get("/v1/staff/review-tasks?kind=red_flag", headers=headers(w, "nurse_a", None))).json()["items"]
    assert any(t["priority"] == "immediate" and t["assigned_role"] == "nurse" for t in tasks)


async def test_a_letter_in_a_clinic_with_no_usable_rule_is_held_and_a_nurse_can_release_the_hold_through_the_routes(w):
    document = await upload_letter(w, "fd_b", w.b, LETTER)
    assert (await w.client.post(RUN.format(document), headers=headers(w, "fd_b"))).status_code == 200
    await run_worker(w, "worker_b")
    assert w.extractor.calls == []  # unassessable is not clear: nothing proceeded
    held = await referral(w, "nurse_b", document)
    assert held["run_state"] == "completed" and held["open_holds"] == 1 and held["holds"][0]["status"] == "open"  # the run did not end silently: a person is asked
    hold = held["holds"][0]["task_id"]
    assert (await w.client.post(f"/v1/staff/review-tasks/{hold}/resolve", headers=headers(w, "nurse_b"))).status_code == 200  # released by a route, not by a database update
    assert (await referral(w, "nurse_b", document))["open_holds"] == 0
