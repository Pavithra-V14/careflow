"""The role matrix AND the live end-to-end run for POST /v1/staff/referrals/{document_id}/run and /review, against the real database (BUILD-SPEC.md §7.2, §12.1, §18.4, §21; CLAUDE.md rule 8).

THE DEDICATED HALF of a split matrix (the committed half is in tests/test_api_role_matrix.py: every identity against random document ids, nothing to clean up).
WHY A SEPARATE HARNESS. Same reason as tests/test_api_screen_message_live.py: the rows these routes and the worker write (docs.documents, ops.queue, agents.graph_checkpoints,
agents.triage_proposals, agents.review_decisions, audit-style review tasks) are append-only, so tests/test_api_role_matrix.py's commit-and-delete fixture could never clean them up.
Here the app is driven over HTTP (httpx ASGI transport) with real signed tokens and the real auth dependency; its database dependency is ONE shared connection inside a
never-committed outer transaction, each request in a savepoint with `set local role careflow_app` and the verified claims set, so row-level security is genuinely in the path, and
everything is rolled back. tests/test_api_role_matrix.py registers both routes (SEPARATE_HARNESS), so `test_every_route_is_in_the_matrix` fails if a route or this file goes missing.
Documented exception to rule 8: docs/design/role-matrix-exceptions.md.

THE RUN IS REAL up to the external engines. The text source, the extractor and the retriever are STAND-INS (scripted); everything else is the production code: the routes, the queue, the
worker handler (claimed and run here exactly as the tick would), the Front Door graph, the red-flag engine and its rules table, mpi ingestion with a real vault, the triage writers, the hold
writer, our asyncpg checkpointer on its real tables, and the real RLS policies of migrations 0017 to 0021. Not covered here (see the screen harness for the same list): the real
pooler engine and commit-before-response (nothing commits), and a process restart across a commit.

# CLINICAL-PLACEHOLDER: needs clinician sign-off
The only red-flag rule is a SYNTHETIC token (`zzalpha`); the task priority and the reviewing role are test inputs. No clinical value is asserted.
It must run in CI with ZERO skips: its only skip condition is a missing DATABASE_URL_MIGRATOR, which CI provides.
"""

import asyncio
import base64
import os
import time
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

from careflow.app import Settings, create_app, db_conn, verified_claims
from careflow.budget import RunBudget, TickClock
from careflow.crypto import DemographicVault
from careflow.db import set_request_claims
from careflow.dead_letters import SqlDeadLetterTaskWriter
from careflow.front_door_runner import FrontDoorComponents, front_door_handler
from careflow.mpi import PatientIngestionPipeline
from careflow.queue import PostgresQueue
from careflow.referral import ExtractedReferral
from careflow.retrieval import Candidate, Retrieval
from careflow.rules import RedFlagEngine
from careflow.tick import JobContext
from careflow.triage import ApprovalMode
from careflow.triage_store import (
    SqlHoldTaskWriter,
    SqlReviewRecorder,
    SqlTriageProposer,
)
from tests.db_helpers import ensure_app_role_sa
from tests.test_api_role_matrix import ATTACKER, PROJECT, bearer, fake_jwks_client, mint

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
RUN, REVIEW = "/v1/staff/referrals/{}/run", "/v1/staff/referrals/{}/review"
LETTER = "Referral. Patient: Asha Rao, DOB 5 March 1980, Mobile 98765 43210. Please see this patient."
RED_LETTER = LETTER + " Note zzalpha."
GOOD = ExtractedReferral(
    name="Asha Rao", name_quote="Patient: Asha Rao", dob="1980-03-05", dob_quote="DOB 5 March 1980", phone="9876543210", phone_quote="Mobile 98765 43210"
)
NO_DOB = ExtractedReferral(name="Asha Rao", name_quote="Patient: Asha Rao")

pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]

# who may start a run (front desk, nurse and doctor of the clinic), and who may review (nurse and doctor). 404 = the document is invisible to them.
START = {
    "nurse_a": 200, "doctor_a": 200, "fd_a": 200, "multi": 200, "worker_a": 403,
    "admin_a": 404, "auditor_a": 404, "owner_a": 404, "nurse_b": 404, "fd_b": 404, "patient": 404, "guardian": 404, "invited": 404, "suspended": 404,
}
REVIEWS = {
    "nurse_a": 200, "doctor_a": 200, "multi": 200, "fd_a": 403, "worker_a": 403,
    "admin_a": 404, "auditor_a": 404, "owner_a": 404, "nurse_b": 404, "fd_b": 404, "patient": 404, "guardian": 404, "invited": 404, "suspended": 404,
}
# Read by tests/test_api_role_matrix.py::test_split_routes_have_both_halves_and_the_dedicated_half_covers_every_role_on_every_axis_value: every required role is tried on
# BOTH routes by the one driving test, and the tests named below must exist.
GRID_AXIS = ("run", "review")
GRID_DRIVER = "test_who_can_start_a_run_and_who_can_review"
GRID_REQUIRED_TESTS = (
    "test_authentication_comes_first_for_every_kind_of_bad_credential", "test_the_idempotency_key_and_the_path_are_validated",
    "test_a_replay_with_the_same_key_returns_the_original_and_a_new_key_is_a_no_op_not_a_second_task", "test_the_whole_run_from_a_registered_letter_to_a_human_decision",
    "test_a_red_flag_letter_raises_the_immediate_nurse_task_and_no_model_is_called", "test_a_review_task_another_reviewer_already_acknowledged_refuses_the_decision_and_the_run_stays_paused",
)
COVERED_GRID = {
    ("POST", "/v1/staff/referrals/{document_id}/run"): {who: GRID_AXIS for who in START},
    ("POST", "/v1/staff/referrals/{document_id}/review"): {who: GRID_AXIS for who in START},
}


def _u() -> uuid.UUID:
    return uuid.uuid4()


async def raw(conn, sql):
    handle = await conn.get_raw_connection()
    await handle.driver_connection.execute(sql)


class Texts:
    def __init__(self):
        self.letters: dict[uuid.UUID, str] = {}

    async def text_for(self, document_id):
        return self.letters[document_id]


class Extractor:
    def __init__(self):
        self.answers: dict[uuid.UUID, ExtractedReferral] = {}
        self.calls: list[uuid.UUID] = []
        self.fail_next = 0

    async def extract(self, letter_text, clearance, *, clinic_id):
        self.calls.append(clearance.document_id)
        if self.fail_next:
            self.fail_next -= 1
            raise RuntimeError("the process died inside the extractor")
        return self.answers[clearance.document_id]


class Retriever:
    async def retrieve(self, query, *, scope):
        return Retrieval(False, None, (Candidate("stand-in-card", 1, 0.03, 0.9, 0.5),))


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
        who = [("nurse_a", a, "nurse"), ("doctor_a", a, "doctor"), ("fd_a", a, "front_desk"), ("worker_a", a, "queue_worker"), ("admin_a", a, "admin"),
               ("auditor_a", a, "auditor"), ("owner_a", a, "business_owner"), ("nurse_b", b, "nurse"), ("fd_b", b, "front_desk"), ("worker_b", b, "queue_worker"),
               ("suspended", a, "nurse"), ("invited", a, "nurse"), ("multi", a, "nurse"), ("multi", b, "doctor")]
        ids = {k: _u() for k, _, _ in who}
        ids["patient"], ids["guardian"], ids["no_account"] = _u(), _u(), _u()
        statuses = {"suspended": "suspended", "invited": "invited"}
        await raw(
            conn,
            f"insert into iam.clinics (id, name, origin) values ('{a}', 'Referral A', 'test'), ('{b}', 'Referral B', 'test'); "
            "insert into iam.accounts (id, account_type, status, origin) values "
            + ", ".join(f"('{ids[k]}', 'staff', '{statuses.get(k, 'active')}', 'test')" for k in dict.fromkeys(k for k, _, _ in who))
            + f", ('{ids['patient']}', 'patient', 'active', 'test'), ('{ids['guardian']}', 'guardian', 'active', 'test'); "
            "insert into iam.staff_roles (account_id, clinic_id, role, origin) values " + ", ".join(f"('{ids[k]}', '{c}', '{r}', 'test')" for k, c, r in who) + ";",
        )
        rid = _u()  # a clinic-A-only red-flag rule: clinic B has NO usable rule, so a letter there is unassessable
        await conn.execute(
            text(
                "insert into kb.red_flag_rules (id, clinic_id, rule_code, version_id, status, phrases, patterns, governance_metadata, authored_by, "
                "approved_by_1, approved_by_2, approved_at, origin, idempotency_key) values (:i, :a, 'referral_route_rule', 1, 'approved', "
                "array['zzalpha'], '{}', cast('{}' as jsonb), :d, :d, :n, now(), 'test', :k)"
            ),
            {"i": rid, "a": a, "d": ids["doctor_a"], "n": ids["nurse_a"], "k": f"r-{rid}"},
        )
        texts, extractor = Texts(), Extractor()
        components = FrontDoorComponents(
            engine=RedFlagEngine(), texts=texts, extractor=extractor, ingestor=PatientIngestionPipeline(vault, key_version=1), retriever=Retriever(),  # type: ignore[arg-type]
            proposer=SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role="nurse", task_priority="standard"),  # test inputs
            recorder=SqlReviewRecorder(), holds=SqlHoldTaskWriter(priority="standard"), dead_letters=SqlDeadLetterTaskWriter(priority="standard"), retrieval_scope={"domain": "stand-in"}, profile="demo",
        )
        lock = asyncio.Lock()

        def worker_session_for(engine, account):
            @asynccontextmanager
            async def session():
                async with lock, conn.begin_nested():
                    await conn.exec_driver_sql("set local role careflow_app")
                    await set_request_claims(conn, {"sub": str(account), "role": "authenticated"})
                    yield conn
                    await conn.exec_driver_sql("reset role")
                    await conn.execute(text("select set_config('request.jwt.claims', '', true)"))

            return session

        app = create_app(
            Settings(supabase_url=PROJECT, database_url_app="postgresql://unused"), jwks=fake_jwks_client(), engine=object(),  # type: ignore[arg-type]
            vault=vault, front_door=components, front_door_session_for=worker_session_for,
        )

        async def shared_db(claims=Depends(verified_claims)):
            async with lock, conn.begin_nested():
                await conn.exec_driver_sql("set local role careflow_app")
                await set_request_claims(conn, claims)
                yield conn
                await conn.exec_driver_sql("reset role")
                await conn.execute(text("select set_config('request.jwt.claims', '', true)"))

        app.dependency_overrides[db_conn] = shared_db
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield type("World", (), {"conn": conn, "client": client, "ids": ids, "a": a, "b": b, "texts": texts, "extractor": extractor,
                                         "components": components, "handler": front_door_handler(components, session_for=worker_session_for),
                                         "worker_session_for": worker_session_for})
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(loop_scope="module")
async def w(world):
    sp = await world.conn.begin_nested()
    world.extractor.answers.clear()
    world.extractor.calls.clear()
    world.extractor.fail_next = 0
    try:
        yield world
    finally:
        await sp.rollback()


async def rows(w, sql, **params):
    return (await w.conn.execute(text(sql), params)).all()


async def new_doc(w, clinic, registered_by, letter=LETTER, answer=GOOD) -> uuid.UUID:
    doc = _u()
    await w.conn.execute(
        text(
            "insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
            "values (:d, :c, 'test', 'referral', :p, 'application/pdf', 1000, :h, :u, :k)"
        ),
        {"d": doc, "c": clinic, "p": f"clinic/{clinic}/doc/{doc}", "h": "a" * 64, "u": registered_by, "k": f"d-{doc}"},
    )
    w.texts.letters[doc] = letter
    w.extractor.answers[doc] = answer
    return doc


async def post(w, who, path, *, key="new", json=None, token=None):
    headers = {} if key is None else {"X-Idempotency-Key": f"k-{_u()}" if key == "new" else key}
    headers.update(bearer(token) if token else bearer(mint(w.ids[who])) if who else {})
    return await w.client.post(path, headers=headers, json=json)


async def run_worker(w, *, expect_job=True):
    """Claim the next job as the worker and run the real handler on it, exactly as the tick would; acknowledge on success."""
    queue, session = PostgresQueue(), w.worker_session_for(object(), w.ids["worker_a"])
    async with session() as conn:
        job = await queue.claim(conn, "test-worker", 120.0)
    if job is None:
        assert not expect_job, "no job was queued"
        return None
    ctx = JobContext(job=job, budget=RunBudget(TickClock()), engine=object(), worker_account_id=w.ids["worker_a"])  # type: ignore[arg-type]
    await w.handler(ctx)
    async with session() as conn:
        await queue.acknowledge(conn, job.id, worker_id="test-worker")
    return job


async def start_and_run(w, doc, who="fd_a"):
    r = await post(w, who, RUN.format(doc))
    assert r.status_code == 200, r.text
    await run_worker(w)
    return r


async def paused(w, clinic=None, by=None) -> uuid.UUID:
    doc = await new_doc(w, clinic or w.a, by or w.ids["fd_a"])
    await start_and_run(w, doc)
    return doc


async def decisions(w, doc):
    return await rows(w, "select d.decision, d.decided_by from agents.review_decisions d join agents.triage_proposals p on p.id = d.proposal_id where p.document_id = :d", d=doc)


# ---- the matrix --------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", list(START))
async def test_who_can_start_a_run_and_who_can_review(w, who):
    """The driving test of the dedicated half: each of the 14 identities tries /run and then /review on real documents (the 403s and 200s the committed half cannot reach)."""
    doc = await paused(w)
    other = await new_doc(w, w.a, w.ids["fd_a"])
    run = await post(w, who, RUN.format(other))
    assert run.status_code == START[who], (who, run.text)
    if START[who] == 200:
        assert set(run.json()) == {"job_id", "replayed"}
    else:
        assert run.json() in ({"detail": "not found"}, {"detail": "your role may not start a referral run"}), (who, run.json())
    review = await post(w, who, REVIEW.format(doc), json={"decision": "approve"})
    assert review.status_code == REVIEWS[who], (who, review.text)
    if REVIEWS[who] == 200:
        assert review.json() == {"document_id": str(doc), "decision": "approve", "replayed": False}
        ((decision, by),) = await decisions(w, doc)
        assert decision == "approve" and by == w.ids[who]  # the reviewer is the signed-in account
    else:
        assert await decisions(w, doc) == [], who  # a refused reviewer decides nothing: the run is still paused


async def test_a_valid_user_with_no_account_and_an_unknown_document_get_the_same_404(w):
    doc = await paused(w)
    for path, body in ((RUN, None), (REVIEW, {"decision": "approve"})):
        known_but_invisible = await post(w, "admin_a", path.format(doc), json=body)
        unknown = await post(w, "nurse_a", path.format(_u()), json=body)
        no_account = await post(w, None, path.format(doc), json=body, token=mint(w.ids["no_account"]))
        assert {(r.status_code, r.text) for r in (known_but_invisible, unknown, no_account)} == {(404, '{"detail":"not found"}')}


async def test_a_document_that_is_not_a_referral_is_not_found(w):
    doc = _u()
    await w.conn.execute(
        text(
            "insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
            "values (:d, :c, 'test', 'report', :p, 'application/pdf', 1000, :h, :u, :k)"
        ),
        {"d": doc, "c": w.a, "p": f"clinic/{w.a}/doc/{doc}", "h": "a" * 64, "u": w.ids["fd_a"], "k": f"d-{doc}"},
    )
    assert (await post(w, "nurse_a", RUN.format(doc))).status_code == 404


async def test_authentication_comes_first_for_every_kind_of_bad_credential(w):
    sub, target = w.ids["nurse_a"], _u()
    for label, headers in (
        ("no header", {}), ("garbage", bearer("not.a.jwt")), ("expired", bearer(mint(sub, exp=int(time.time()) - 3600))),
        ("other key under the right kid", bearer(mint(sub, key=ATTACKER))), ("anonymous role", bearer(mint(sub, role="anon"))),
    ):
        for path, body in ((RUN, None), (REVIEW, {"decision": "approve"})):
            for extra in ({"X-Idempotency-Key": "k"}, {}):  # even without the key: 401, not 422
                r = await w.client.post(path.format(target), headers={**headers, **extra}, json=body)
                assert r.status_code == 401, (label, path, extra)


async def test_the_idempotency_key_and_the_path_are_validated(w):
    """(This also validates the review body: the reviewer is never accepted from it.)"""
    doc = await paused(w)
    for key in (None, "", "   "):
        assert (await post(w, "nurse_a", RUN.format(doc), key=key)).status_code == 422, key
        assert (await post(w, "nurse_a", REVIEW.format(doc), key=key, json={"decision": "approve"})).status_code == 422, key
    for path in (RUN, REVIEW):
        r = await w.client.post(path.format("nope"), headers={**bearer(mint(w.ids["nurse_a"])), "X-Idempotency-Key": "k"}, json={"decision": "approve"})
        assert r.status_code == 422
    for bad in ({}, {"decision": "approve-all"}, {"decision": "approve", "reviewer_id": str(w.ids["doctor_a"])}):  # the reviewer is never taken from the body
        assert (await post(w, "nurse_a", REVIEW.format(doc), json=bad)).status_code == 422, bad
    assert await decisions(w, doc) == []


# ---- behaviour: the whole run -----------------------------------------------------------------------------------------------------------


async def test_a_replay_with_the_same_key_returns_the_original_and_a_new_key_is_a_no_op_not_a_second_task(w):
    """Same key: the same job. New key: a second job, but the RUN is idempotent: one patient, one proposal, one review task."""
    doc = await new_doc(w, w.a, w.ids["fd_a"])
    first = await post(w, "fd_a", RUN.format(doc), key="same")
    again = await post(w, "fd_a", RUN.format(doc), key="same")
    other = await post(w, "fd_a", RUN.format(doc), key="different")
    assert first.json()["job_id"] == again.json()["job_id"] != other.json()["job_id"]
    assert (first.json()["replayed"], again.json()["replayed"], other.json()["replayed"]) == (False, True, False)
    assert len(await rows(w, "select id from ops.queue where task_type = 'front_door_run' and payload->>'document_id' = :d", d=str(doc))) == 2
    await run_worker(w)
    await run_worker(w)  # the second job finds the thread paused and does nothing
    assert len(await rows(w, "select id from agents.triage_proposals where document_id = :d", d=doc)) == 1
    assert len(await rows(w, "select id from agents.review_tasks where source_message_id = :s and kind = 'triage_proposal'", s=f"document:{doc}")) == 1
    assert len(await rows(w, "select id from mpi.patients where clinic_id = :c", c=w.a)) == 1 and w.extractor.calls == [doc]


async def test_the_whole_run_from_a_registered_letter_to_a_human_decision(w):
    doc = await new_doc(w, w.a, w.ids["fd_a"])
    started = await post(w, "fd_a", RUN.format(doc))
    assert started.status_code == 200
    job = await run_worker(w)
    assert job is not None and job.payload == {"document_id": str(doc)} and job.clinic_id == w.a
    # identity: one patient from VERIFIED facts, created by the worker, with tokens and no review pair
    assert len(await rows(w, "select id from mpi.patients where clinic_id = :c", c=w.a)) == 1
    assert len(await rows(w, "select id from mpi.identity_tokens where clinic_id = :c", c=w.a)) == 2
    # the proposal and its open nurse task; nothing decided, nothing approved by waiting
    ((proposal_id, task_id, abstained, candidates),) = await rows(w, "select id, review_task_id, abstained, candidates from agents.triage_proposals where document_id = :d", d=doc)
    assert abstained is False and candidates == [{"card_id": "stand-in-card", "version": 1}]
    ((status, assigned, kind),) = await rows(w, "select status, assigned_role, kind from agents.review_tasks where id = :t", t=task_id)
    assert (status, assigned, kind) == ("open", "nurse", "triage_proposal") and await decisions(w, doc) == []
    # the paused run is in the checkpoint tables, written by the worker under row-level security
    assert (await rows(w, "select count(*) from agents.graph_checkpoints where thread_id = :t and clinic_id = :c", t=f"front_door:{doc}", c=w.a))[0][0] >= 4
    # the reviewer decides
    r = await post(w, "nurse_a", REVIEW.format(doc), json={"decision": "approve"})
    assert r.status_code == 200 and r.json()["replayed"] is False
    ((decision, by),) = await decisions(w, doc)
    assert (decision, by) == ("approve", w.ids["nurse_a"])
    ((status, ack),) = await rows(w, "select status, acknowledged_by from agents.review_tasks where id = :t", t=task_id)
    assert (status, ack) == ("resolved", w.ids["nurse_a"])
    # a repeat is a replay, a different decision is refused, and another reviewer cannot overwrite it
    assert (await post(w, "nurse_a", REVIEW.format(doc), json={"decision": "approve"})).json()["replayed"] is True
    assert (await post(w, "nurse_a", REVIEW.format(doc), json={"decision": "reject"})).status_code == 409
    assert (await post(w, "doctor_a", REVIEW.format(doc), json={"decision": "approve"})).status_code == 409
    assert len(await decisions(w, doc)) == 1 and proposal_id


async def test_no_patient_text_reaches_a_response_a_job_a_task_or_a_checkpoint(w):
    doc = await new_doc(w, w.a, w.ids["fd_a"])
    run = await post(w, "fd_a", RUN.format(doc))
    await run_worker(w)
    review = await post(w, "nurse_a", REVIEW.format(doc), json={"decision": "approve"})
    for response in (run, review):
        assert not any(s in response.text for s in ("Asha", "Rao", "1980", "98765", "9876543210"))
    stored = " ".join(str(r[0]) for r in await rows(w, "select payload::text from ops.queue union all select provenance::text from agents.review_tasks"))
    assert not any(s in stored for s in ("Asha", "Rao", "1980", "98765"))
    control = (await rows(w, "select count(*) from agents.graph_checkpoints where position(convert_to('front_door', 'UTF8') in metadata) > 0"))[0][0]
    assert control > 0  # the probe below CAN find a string that is there
    for table, column in (("agents.graph_checkpoints", "checkpoint"), ("agents.graph_checkpoints", "metadata"), ("agents.graph_checkpoint_blobs", "blob"), ("agents.graph_checkpoint_writes", "write_blob")):
        for needle in ("Asha", "Rao", "1980", "98765"):
            n = (await rows(w, f"select count(*) from {table} where position(convert_to(:s, 'UTF8') in {column}) > 0", s=needle))[0][0]
            assert n == 0, (table, column, needle)


async def test_a_red_flag_letter_raises_the_immediate_nurse_task_and_no_model_is_called(w):
    doc = await new_doc(w, w.a, w.ids["fd_a"], letter=RED_LETTER)
    await post(w, "fd_a", RUN.format(doc))
    await run_worker(w)
    assert w.extractor.calls == []  # the rules ran first and the extractor was never asked
    ((kind, priority, role),) = await rows(w, "select kind, priority, assigned_role from agents.review_tasks where source_message_id = :s", s=f"document:{doc}")
    assert (kind, priority, role) == ("red_flag", "immediate", "nurse")
    assert await rows(w, "select id from mpi.patients where clinic_id = :c", c=w.a) == [] and await rows(w, "select id from agents.triage_proposals where document_id = :d", d=doc) == []
    assert (await post(w, "nurse_a", REVIEW.format(doc), json={"decision": "approve"})).status_code == 409  # a finished run is not waiting for a decision


async def test_a_letter_in_a_clinic_with_no_usable_rules_is_held_for_a_nurse_never_treated_as_clear(w):
    doc = await new_doc(w, w.b, w.ids["fd_b"])
    assert (await post(w, "fd_b", RUN.format(doc))).status_code == 200
    await run_worker(w)
    assert w.extractor.calls == []
    ((kind, role, provenance),) = await rows(w, "select kind, assigned_role, provenance from agents.review_tasks where source_message_id = :s", s=f"document:{doc}")
    assert (kind, role) == ("referral_hold", "nurse") and provenance["status"] == "held_unassessable"


async def test_a_letter_with_no_date_of_birth_is_held_for_front_desk_who_can_see_and_resolve_it(w):
    doc = await new_doc(w, w.a, w.ids["fd_a"], answer=NO_DOB)
    await post(w, "fd_a", RUN.format(doc))
    await run_worker(w)
    assert await rows(w, "select id from mpi.patients where clinic_id = :c", c=w.a) == []  # never created from an unverified fact
    ((task, role, provenance),) = await rows(w, "select id, assigned_role, provenance from agents.review_tasks where source_message_id = :s", s=f"document:{doc}")
    assert role == "front_desk" and provenance["status"] == "needs_front_desk" and "dob_missing" in provenance["reasons"]
    async with w.worker_session_for(object(), w.ids["fd_a"])() as conn:  # the same session helper, as front desk
        seen = (await conn.execute(text("select id from agents.review_tasks where id = :t"), {"t": task})).all()
        resolved = (await conn.execute(text("update agents.review_tasks set status = 'resolved', acknowledged_by = :u, acknowledged_at = now(), resolved_at = now() where id = :t"),
                                       {"u": w.ids["fd_a"], "t": task})).rowcount
    assert len(seen) == 1 and resolved == 1


async def test_a_crash_inside_the_extractor_is_retried_and_the_run_completes_without_repeating_an_effect(w):
    doc = await new_doc(w, w.a, w.ids["fd_a"])
    await post(w, "fd_a", RUN.format(doc))
    w.extractor.fail_next = 1
    with pytest.raises(RuntimeError):
        await run_worker(w)  # the job is claimed and NOT acknowledged: the queue's visibility timeout would hand it out again
    assert await rows(w, "select id from mpi.patients where clinic_id = :c", c=w.a) == []
    await w.conn.execute(text("update ops.queue set visible_after = now() - interval '1 second' where payload->>'document_id' = :d"), {"d": str(doc)})
    await run_worker(w)  # the retry resumes the thread from its checkpoint
    assert w.extractor.calls == [doc, doc] and len(await rows(w, "select id from mpi.patients where clinic_id = :c", c=w.a)) == 1
    assert len(await rows(w, "select id from agents.triage_proposals where document_id = :d", d=doc)) == 1
    assert (await post(w, "nurse_a", REVIEW.format(doc), json={"decision": "edit"})).status_code == 200


async def test_a_worker_job_for_a_document_of_another_clinic_is_refused_before_anything_runs(w):
    doc = await new_doc(w, w.b, w.ids["fd_b"])  # a clinic-B document ...
    await w.conn.execute(
        text("insert into ops.queue (clinic_id, origin, task_type, payload, idempotency_key) values (:c, 'test', 'front_door_run', cast(:p as jsonb), :k)"),
        {"c": w.a, "p": f'{{"document_id": "{doc}"}}', "k": f"mis-{_u()}"},  # ... named by a clinic-A job
    )
    with pytest.raises(Exception, match="not in the job's clinic"):
        await run_worker(w)
    assert w.extractor.calls == [] and await rows(w, "select id from agents.graph_checkpoints where thread_id = :t", t=f"front_door:{doc}") == []


async def test_a_review_task_another_reviewer_already_acknowledged_refuses_the_decision_and_the_run_stays_paused(w):
    doc = await paused(w)
    ((task,),) = await rows(w, "select review_task_id from agents.triage_proposals where document_id = :d", d=doc)
    async with w.worker_session_for(object(), w.ids["doctor_a"])() as conn:  # the doctor picks the task up first
        n = (await conn.execute(text("update agents.review_tasks set status = 'acknowledged', acknowledged_by = :u, acknowledged_at = now() where id = :t"),
                                {"u": w.ids["doctor_a"], "t": task})).rowcount
    assert n == 1
    refused = await post(w, "nurse_a", REVIEW.format(doc), json={"decision": "approve"})
    assert (refused.status_code, refused.json()) == (403, {"detail": "you cannot record this decision"})
    assert await decisions(w, doc) == []  # the request rolled back: no decision on an unresolved task
    still_open = await post(w, "doctor_a", REVIEW.format(doc), json={"decision": "approve"})
    assert still_open.status_code == 200 and len(await decisions(w, doc)) == 1  # the run was still paused: the acknowledging doctor can decide
