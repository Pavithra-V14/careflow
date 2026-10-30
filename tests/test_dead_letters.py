"""A dead-lettered front_door_run job is never silent (careflow/dead_letters.py; owner decision 2026-10-24). Offline: fakes only. The real queue dead-lettering after five failures
and the one task it leaves is tests/test_dead_letters_live.py."""

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from careflow.dead_letters import (
    DeadLetterError,
    SqlDeadLetterTaskWriter,
    raise_dead_letter_tasks,
)
from careflow.tick import run_tick

pytestmark = pytest.mark.asyncio
CLINIC, JOB, DOC, ACCOUNT = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class Conn:
    def __init__(self, *, rows=(), existing=False, error=None):
        self.rows, self.existing, self.error, self.calls = list(rows), existing, error, []

    def begin_nested(self):
        @asynccontextmanager
        async def cm():
            yield self

        return cm()

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if self.error and "insert into agents.review_tasks" in sql:
            raise self.error
        if "insert into agents.review_tasks" in sql:
            return Result([] if self.existing else [SimpleNamespace(id=uuid.uuid4())])
        return Result(self.rows)


def dead_job(job_id=JOB, payload=None, attempts=5):
    return SimpleNamespace(id=job_id, clinic_id=CLINIC, origin="test", task_type="front_door_run", payload={"document_id": str(DOC)} if payload is None else payload, attempt_count=attempts)


async def test_the_writer_makes_one_nurse_task_with_ids_and_numbers_only_and_the_priority_is_an_input():
    conn, writer = Conn(), SqlDeadLetterTaskWriter(priority="standard")  # a test input
    assert await writer.raise_task(conn, job_id=JOB, clinic_id=CLINIC, origin="test", task_type="front_door_run", payload={"document_id": str(DOC)}, attempts=5) is True
    sql, p = conn.calls[0]
    assert "'nurse'" in sql and "'standard'" not in sql and p["k"] == "referral_dead_letter" and p["pr"] == "standard"
    assert p["ik"] == f"dead-letter:{JOB}" and p["s"] == f"document:{DOC}"
    assert p["p"] == f'{{"job_id": "{JOB}", "task_type": "front_door_run", "attempts": 5}}'  # no error text, no letter text


async def test_a_replay_creates_nothing_and_a_payload_without_a_document_falls_back_to_the_job_id():
    writer = SqlDeadLetterTaskWriter(priority="immediate")
    assert await writer.raise_task(Conn(existing=True), job_id=JOB, clinic_id=CLINIC, origin="test", task_type="t", payload={}, attempts=1) is False
    conn = Conn()
    await writer.raise_task(conn, job_id=JOB, clinic_id=CLINIC, origin="test", task_type="t", payload={"document_id": "not-a-uuid"}, attempts=1)
    assert conn.calls[0][1]["s"] == f"job:{JOB}"


async def test_the_priority_has_no_default_and_must_be_one_the_table_allows_and_a_refusal_is_a_clean_error():
    with pytest.raises(TypeError):
        SqlDeadLetterTaskWriter()  # type: ignore[call-arg]
    for bad in ("urgent", "", None):
        with pytest.raises(ValueError):
            SqlDeadLetterTaskWriter(priority=bad)  # type: ignore[arg-type]

    class Orig(Exception):
        sqlstate = "42501"

    with pytest.raises(DeadLetterError):
        await SqlDeadLetterTaskWriter(priority="standard").raise_task(
            Conn(error=DBAPIError("insert", {}, Orig("x"))), job_id=JOB, clinic_id=CLINIC, origin="test", task_type="t", payload={}, attempts=1
        )


async def test_the_sweep_asks_only_for_dead_lettered_jobs_of_the_listed_types_that_have_no_task_and_counts_what_it_creates():
    seen = []

    class Writer:
        async def raise_task(self, conn, **kw):
            seen.append(kw["job_id"])
            return kw["job_id"] != JOB  # the first job already has a task

    conn = Conn(rows=[dead_job(), dead_job(job_id=uuid.uuid4())])
    created = await raise_dead_letter_tasks(conn, Writer())  # type: ignore[arg-type]
    assert created == 1 and len(seen) == 2
    sql, params = conn.calls[0]
    assert "state = 'dead_lettered'" in sql and "not exists" in sql and "dead-letter:" in sql and params["t"] == ["front_door_run"]


# ---- the tick runs the sweeps ---------------------------------------------------------------------------------------------------------------


class FakeEngine:
    def connect(self):
        @asynccontextmanager
        async def cm():
            class C:
                async def execute(self, *a, **k):
                    return None

                def begin(self):
                    @asynccontextmanager
                    async def b():
                        yield self

                    return b()

            yield C()

        return cm()


class EmptyQueue:
    async def claim(self, *a, **k):
        return None


async def test_the_tick_runs_every_sweep_after_the_jobs_and_a_failing_sweep_does_not_fail_the_tick(caplog):
    calls = []

    async def good(conn):
        calls.append("good")
        return 2

    async def bad(conn):
        calls.append("bad")
        raise RuntimeError("SENTINEL-SWEEP-ERROR")

    result = await run_tick(FakeEngine(), EmptyQueue(), {}, worker_account_id=ACCOUNT, worker_id="w", sweeps=[bad, good])  # type: ignore[arg-type]
    assert calls == ["bad", "good"] and result.swept == 2
    assert "SENTINEL" not in caplog.text and "RuntimeError" in caplog.text  # the class only
