"""The Front Door job handler (careflow/front_door_runner.py), offline: a fake session and a fake graph. Whether it is safe to run twice, to resume after a crash and to leave a
paused or finished thread alone. The real run against the real tables is tests/test_api_referrals_live.py."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import careflow.front_door_runner as runner
from careflow.budget import RunBudget, TickClock
from careflow.front_door_runner import RunnerError, front_door_handler
from careflow.queue import Job
from careflow.tick import JobContext

pytestmark = pytest.mark.asyncio
CLINIC, DOC, WORKER = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class Conn:
    def __init__(self, clinic, origin="test"):
        self.clinic, self.origin = clinic, origin

    async def execute(self, statement, params=None):
        assert "from docs.documents" in str(statement)
        return SimpleNamespace(first=lambda: None if self.clinic is None else SimpleNamespace(clinic_id=self.clinic, origin=self.origin))


class Graph:
    def __init__(self, values=None, next_=()):
        self.state = SimpleNamespace(values=values or {}, next=next_)
        self.invoked = []

    async def aget_state(self, config):
        return self.state

    async def ainvoke(self, payload, config):
        self.invoked.append((payload, config))


def ctx(payload=None, clinic=CLINIC):
    job = Job(uuid.uuid4(), clinic, "test", "front_door_run", {"document_id": str(DOC)} if payload is None else payload, 1, "w", datetime.now(UTC), "k")
    return JobContext(job=job, budget=RunBudget(TickClock()), engine=object(), worker_account_id=WORKER)  # type: ignore[arg-type]


def handler(graph, doc_clinic=CLINIC, monkeypatch=None, doc_origin="test"):
    seen = {}

    def session_for(engine, account):
        seen["account"] = account

        @asynccontextmanager
        async def session():
            yield Conn(doc_clinic, doc_origin)

        return session

    monkeypatch.setattr(runner, "graph_for", lambda components, session: graph)
    return front_door_handler(object(), session_for=session_for), seen  # type: ignore[arg-type]


async def test_a_new_run_starts_the_graph_from_the_initial_state_as_the_worker(monkeypatch):
    graph = Graph()
    h, seen = handler(graph, monkeypatch=monkeypatch)
    await h(ctx())
    ((payload, config),) = graph.invoked
    assert payload == {"document_id": str(DOC), "clinic_id": str(CLINIC), "origin": "test", "status": "started", "reasons": []}
    assert config["configurable"]["thread_id"] == f"front_door:{DOC}" and config["configurable"]["clinic_id"] == str(CLINIC) and seen["account"] == WORKER
    assert config["recursion_limit"] == 40


async def test_a_crashed_run_is_continued_from_its_checkpoint_not_started_again(monkeypatch):
    graph = Graph({"status": "matched"}, ("propose_triage",))
    h, _ = handler(graph, monkeypatch=monkeypatch)
    await h(ctx())
    ((payload, _),) = graph.invoked
    assert payload is None  # resume from the last completed step


@pytest.mark.parametrize("state", [({"status": "triage_proposed"}, ("review",)), ({"status": "reviewed"}, ()), ({"status": "red_flag_escalated"}, ())])
async def test_a_thread_paused_for_a_person_or_already_finished_is_left_alone_and_the_job_completes(monkeypatch, state):
    graph = Graph(*state)
    h, _ = handler(graph, monkeypatch=monkeypatch)
    await h(ctx())
    assert graph.invoked == []


async def test_a_job_that_does_not_name_a_document_or_names_one_of_another_clinic_is_refused_before_anything_runs(monkeypatch):
    graph = Graph()
    h, _ = handler(graph, monkeypatch=monkeypatch)
    for payload in ({}, {"document_id": "not-a-uuid"}, {"document_id": 7}):
        with pytest.raises(RunnerError):
            await h(ctx(payload))
    h2, _ = handler(graph, doc_clinic=uuid.uuid4(), monkeypatch=monkeypatch)
    with pytest.raises(RunnerError):
        await h2(ctx())
    h3, _ = handler(graph, doc_clinic=None, monkeypatch=monkeypatch)  # a document the worker cannot see
    with pytest.raises(RunnerError):
        await h3(ctx())
    h4, _ = handler(graph, doc_origin="live", monkeypatch=monkeypatch)  # the job says origin 'test', the document is 'live'
    with pytest.raises(RunnerError):
        await h4(ctx())
    assert graph.invoked == []


async def test_the_production_worker_session_sets_the_workers_claims_first_and_commits_on_a_clean_exit():
    events = []

    class Conn:
        async def execute(self, statement, params=None):
            events.append(("execute", str(statement), params))

    class Begin:
        async def __aenter__(self):
            events.append(("begin",))

        async def __aexit__(self, *exc):
            events.append(("commit" if exc[0] is None else "rollback",))

    class Connect:
        async def __aenter__(self):
            conn = Conn()
            conn.begin = lambda: Begin()
            return conn

        async def __aexit__(self, *exc):
            events.append(("closed",))

    engine = SimpleNamespace(connect=lambda: Connect())
    async with runner.worker_session(engine, WORKER)() as conn:  # type: ignore[arg-type]
        assert conn is not None
    assert events[0] == ("begin",) and events[1][0] == "execute" and "set_config('request.jwt.claims'" in events[1][1] and str(WORKER) in events[1][2]["c"]
    assert ("commit",) in events
    events.clear()
    with pytest.raises(ValueError):
        async with runner.worker_session(engine, WORKER)():  # type: ignore[arg-type]
            raise ValueError("boom")
    assert ("rollback",) in events and ("commit",) not in events


async def test_the_error_text_never_carries_the_payload(monkeypatch):
    h, _ = handler(Graph(), monkeypatch=monkeypatch)
    with pytest.raises(RunnerError) as exc:
        await h(ctx({"document_id": "SENTINEL-NOT-A-UUID"}))
    assert "SENTINEL" not in str(exc.value)


async def test_the_request_session_serialises_its_savepoints_so_concurrent_uses_never_interleave():
    import asyncio

    events = []

    class Conn:
        def begin_nested(self):
            @asynccontextmanager
            async def cm():
                events.append("enter")
                try:
                    yield self
                finally:
                    events.append("exit")

            return cm()

    session = runner.request_session(Conn())  # type: ignore[arg-type]

    async def use():
        async with session():
            await asyncio.sleep(0)  # a task switch inside the savepoint, as LangGraph's tasks cause
            await asyncio.sleep(0)

    await asyncio.gather(use(), use(), use())
    assert events == ["enter", "exit"] * 3  # never enter, enter, exit, exit


async def test_the_graph_runs_inside_the_worker_context_so_extraction_is_allowed_there_and_nowhere_else(monkeypatch):
    from careflow.extraction import NotInWorker, require_worker

    class Probe(Graph):
        async def ainvoke(self, payload, config):
            require_worker()  # raises NotInWorker if the handler did not set the context
            await super().ainvoke(payload, config)

    graph = Probe()
    h, _ = handler(graph, monkeypatch=monkeypatch)
    await h(ctx())
    assert len(graph.invoked) == 1
    with pytest.raises(NotInWorker):  # and it is gone again afterwards
        require_worker()
