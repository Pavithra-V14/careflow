"""Pre-launch blocker 8: the Front Door graph through the REAL transaction-mode pooler with REAL commits (BUILD-SPEC.md 18.1, 7.2, 7.4).

Every other live graph test runs inside one never-committed outer transaction on a direct connection. This one is the production shape: the app's own engine (`make_app_engine`, the
restricted `careflow_app` login through the pooler, the same pool settings as the lifespan), a job enqueued in a request-shaped transaction as front desk, the queue worker's own `run_tick`
claiming it, `front_door_handler` with the PRODUCTION `worker_session` (one transaction per use, claims set, committed on a clean exit), the Postgres checkpointer, the hold task, and the
acknowledgement, all COMMITTED. The assertions read the committed rows back over a separate owner connection.

The clinic has no approved red-flag rule, so the screen records "unassessable" and the graph ends in a hold for a nurse (unknown is not clear): the path that commits checkpoints, a review
task and the queue acknowledgement without needing a committed approved rule (which other suites refuse to run beside). Data is synthetic, tagged origin='test', random ids; tables here
are append-only, so the rows stay (nothing reads them).

WHAT IS MEASURED AND PRINTED (read the test output with -s, or the CI log): the peak number of `careflow_app` backends seen from the owner connection during the run, and the pool's
checked-out count afterwards. Differences from a direct connection that the test pins: the pooler uses no server-side prepared statements (statement_cache_size=0 with unique names), claims
are transaction-local (a later transaction on a recycled backend carries no identity), and nothing is left checked out.
"""

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text

from careflow.db import make_app_engine, set_request_claims
from careflow.dead_letters import SqlDeadLetterTaskWriter
from careflow.front_door_runner import (
    TASK_TYPE,
    FrontDoorComponents,
    front_door_handler,
)
from careflow.queue import PostgresQueue
from careflow.rules import RedFlagEngine
from careflow.tick import run_tick
from careflow.triage import ApprovalMode
from careflow.triage_store import (
    SqlHoldTaskWriter,
    SqlReviewRecorder,
    SqlTriageProposer,
)

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
APP_URL = os.environ.get("DATABASE_URL_APP") or dotenv_values(ROOT / ".env").get("DATABASE_URL_APP")
pytestmark = [
    pytest.mark.asyncio(loop_scope="module"),
    pytest.mark.skipif(not (MIGRATOR and APP_URL), reason="DATABASE_URL_MIGRATOR and DATABASE_URL_APP are required"),
]
LETTER = "Referral. Patient: Asha Rao, DOB 5 March 1980, Mobile 98765 43210. Please see this patient."


class Texts:
    async def text_for(self, document_id):
        return LETTER


class MustNotBeCalled:
    """The held path never reaches the extractor, the matcher or the retriever; if it does, the test fails loudly."""

    def __getattr__(self, name):
        async def boom(*args, **kwargs):
            raise AssertionError(f"{name} was called on a path that must end in a hold")

        return boom


def components():
    return FrontDoorComponents(
        engine=RedFlagEngine(), texts=Texts(), extractor=MustNotBeCalled(), ingestor=MustNotBeCalled(), retriever=MustNotBeCalled(),  # type: ignore[arg-type]
        proposer=SqlTriageProposer(mode=ApprovalMode.ASSISTED, assigned_role="nurse", task_priority="standard"),  # test inputs, not clinical values
        recorder=SqlReviewRecorder(), holds=SqlHoldTaskWriter(priority="standard"), dead_letters=SqlDeadLetterTaskWriter(priority="standard"),
        retrieval_scope={"domain": "pooler-test"}, profile="demo",
    )


class World:
    pass


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def world():
    w = World()
    w.clinic, w.fd, w.worker = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    w.owner = await asyncpg.connect((MIGRATOR or "").replace("postgresql+asyncpg://", "postgresql://", 1), timeout=30, statement_cache_size=0)
    await w.owner.execute("insert into iam.clinics (id, name, origin) values ($1, 'Pooler test', 'test')", w.clinic)
    await w.owner.executemany(
        "insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", [(w.fd,), (w.worker,)]
    )
    await w.owner.executemany(
        "insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')",
        [(w.fd, w.clinic, "front_desk"), (w.worker, w.clinic, "queue_worker")],
    )
    w.engine = make_app_engine(APP_URL or "", pool_size=5, max_overflow=5, pool_pre_ping=True)  # the lifespan's settings
    try:
        yield w
    finally:
        await w.engine.dispose()
        await w.owner.close()


async def new_document(w) -> uuid.UUID:
    doc = uuid.uuid4()
    await w.owner.execute(
        "insert into docs.documents (id, clinic_id, origin, kind, storage_path, content_type, size_bytes, sha256, registered_by, idempotency_key) "
        "values ($1, $2, 'test', 'referral', $3, 'application/pdf', 1000, $4, $5, $6)",
        doc, w.clinic, f"clinic/{w.clinic}/doc/{doc}", "a" * 64, w.fd, f"d-{doc}",
    )
    return doc


async def enqueue_as_front_desk(w, doc):
    """The request-shaped transaction of POST /run: claims set, the job enqueued, committed."""
    async with w.engine.connect() as conn, conn.begin():
        await set_request_claims(conn, {"sub": str(w.fd), "role": "authenticated"})
        return await PostgresQueue().enqueue(conn, w.clinic, "test", TASK_TYPE, {"document_id": str(doc)}, f"front-door-run:{doc}:pooler")


class Sampler:
    """Counts `careflow_app` backends seen by the owner connection every 0.25 s; the peak is reported."""

    def __init__(self, owner):
        self.owner, self.peak, self._task = owner, 0, None

    async def _run(self):
        while True:
            n = await self.owner.fetchval("select count(*) from pg_stat_activity where usename like 'careflow_app%'")
            self.peak = max(self.peak, n)
            await asyncio.sleep(0.25)

    async def __aenter__(self):
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *exc):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


async def test_a_referral_runs_through_the_real_pooler_and_every_step_is_really_committed(world, capsys):
    w = world
    doc = await new_document(w)
    queued = await enqueue_as_front_desk(w, doc)
    assert (await w.owner.fetchval("select state from ops.queue where id = $1", queued.job_id)) == "unclaimed"  # committed by the request-shaped transaction

    handlers = {TASK_TYPE: front_door_handler(components())}  # the PRODUCTION worker_session (the default)
    async with Sampler(w.owner) as sampler:
        result = await run_tick(w.engine, PostgresQueue(), handlers, worker_account_id=w.worker, worker_id="pooler-test", tick_seconds=40.0)
    assert result.completed == 1 and result.dead_lettered == 0 and result.left_for_retry == 0 and result.job_ids == [queued.job_id]

    # everything below is read over a SEPARATE connection, so it is only true if the transactions really committed
    assert (await w.owner.fetchval("select state from ops.queue where id = $1", queued.job_id)) == "completed"
    thread = f"front_door:{doc}"
    checkpoints = await w.owner.fetchval("select count(*) from agents.graph_checkpoints where clinic_id = $1 and thread_id = $2", w.clinic, thread)
    assert checkpoints >= 2, "the graph checkpointed through the pooler"
    tasks = await w.owner.fetch("select kind, assigned_role, provenance from agents.review_tasks where clinic_id = $1 and source_message_id = $2", w.clinic, f"document:{doc}")
    assert len(tasks) == 1 and tasks[0]["kind"] == "referral_hold" and tasks[0]["assigned_role"] == "nurse"
    assert "held_unassessable" in str(tasks[0]["provenance"])  # no approved rule: unknown is not clear
    assert (await w.owner.fetchval("select count(*) from mpi.patients where clinic_id = $1", w.clinic)) == 0  # nothing was created from an unassessed letter

    # a second tick is a no-op: nothing to claim, no second task, no new checkpoint
    again = await run_tick(w.engine, PostgresQueue(), handlers, worker_account_id=w.worker, worker_id="pooler-test", tick_seconds=40.0)
    assert again.processed == 0
    assert (await w.owner.fetchval("select count(*) from agents.review_tasks where clinic_id = $1 and source_message_id = $2", w.clinic, f"document:{doc}")) == 1
    assert (await w.owner.fetchval("select count(*) from agents.graph_checkpoints where clinic_id = $1 and thread_id = $2", w.clinic, thread)) == checkpoints

    # the pooler behaviours worth pinning
    assert w.engine.pool.checkedout() == 0, "a connection was left checked out"  # type: ignore[attr-defined]
    assert sampler.peak >= 1  # the sampler saw this run's backends. No upper bound is asserted: the other two shards use the same login at the same time, so the number is REPORTED, not pinned
    with capsys.disabled():
        print(f"\nPOOLER-GRAPH peak_careflow_app_backends={sampler.peak} checkpoints={checkpoints} pool_checkedout_after=0")


async def test_claims_are_transaction_local_on_the_pooler_a_recycled_backend_carries_no_identity(world):
    w = world
    for _ in range(12):  # many short transactions over a small pool: backends are reused
        async with w.engine.connect() as conn, conn.begin():
            await set_request_claims(conn, {"sub": str(w.worker), "role": "authenticated"})
            assert (await conn.execute(text("select current_setting('request.jwt.claims', true) like :p"), {"p": f"%{w.worker}%"})).scalar_one() is True
        async with w.engine.connect() as conn, conn.begin():
            leftover = (await conn.execute(text("select coalesce(current_setting('request.jwt.claims', true), '')"))).scalar_one()
            assert leftover == "", "an earlier transaction's claims leaked onto a recycled pooled backend"


async def test_the_enqueue_and_the_tick_never_use_server_side_prepared_statements_beyond_the_pooler_safe_unique_names(world):
    async with world.engine.connect() as conn, conn.begin():
        names = [r[0] for r in (await conn.execute(text("select name from pg_prepared_statements"))).all()]
    assert not [n for n in names if n.startswith("__asyncpg_stmt_")], names  # sequential names collide across pooled backends
