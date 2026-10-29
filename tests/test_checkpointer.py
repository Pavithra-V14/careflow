"""Our asyncpg LangGraph checkpointer (careflow/checkpointer.py; migration 0021), offline over the in-memory store.

Four things are proved:
  1. CONFORMANCE: the same graph, run against LangGraph's own InMemorySaver and against ours, gives the same states, the same history and the same interrupt.
  2. THE CONTRACT, operation by operation: put / put_writes / get_tuple / list semantics, replay is a no-op, special writes are replaced, delete is refused, scope is required.
  3. DURABILITY: the Front Door graph is killed mid-run (the graph, saver and everything in the process thrown away), a NEW graph and saver over the same store resumes, and no side
     effect is repeated; every model call after the restart is still preceded by the rules.
  4. The SQL store's statements carry the clinic, the origin and an idempotency key. (The tables, RLS and the real database are tests/test_rls_checkpoints_live.py.)
"""

import json
import uuid
from contextlib import asynccontextmanager
from typing import TypedDict

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from careflow.checkpointer import (
    CheckpointerError,
    HistoryIsAppendOnly,
    InMemoryCheckpointStore,
    PostgresCheckpointSaver,
    Scope,
    SqlCheckpointStore,
)
from careflow.front_door import (
    FrontDoorDeps,
    build_front_door,
    initial_state,
    run_config,
)
from tests.needles import assert_not_leaked
from tests.test_front_door import Holds, Proposer, Recorder, Retriever, Texts
from tests.test_referral import CLINIC, DOC, GOOD, Conn, Engine, Extractor, Ingestor

pytestmark = pytest.mark.asyncio
THREAD = "t-1"
REVIEWER = str(uuid.uuid4())


def cfg(thread=THREAD, **over) -> RunnableConfig:
    return {"configurable": {"thread_id": thread, "checkpoint_ns": "", "clinic_id": str(CLINIC), "origin": "test", **over}}


def scoped(out, clinic=None):
    """A config returned by aput carries no clinic; reads need one."""
    return {"configurable": {**out["configurable"], "clinic_id": str(clinic or CLINIC), "origin": "test"}}


def saver():
    store = InMemoryCheckpointStore()
    return PostgresCheckpointSaver(store), store


# ---- 1. conformance with InMemorySaver ---------------------------------------------------------------------------------------------------------


class S(TypedDict, total=False):
    a: int
    answer: str


def small_graph(checkpointer):
    def one(state: S) -> S:
        return {"a": 1}

    def ask(state: S) -> S:
        return {"answer": interrupt({"question": "go on?"})}

    def two(state: S) -> S:
        return {"a": state["a"] + 10}

    builder = StateGraph(S)
    builder.add_node("one", one)
    builder.add_node("ask", ask)
    builder.add_node("two", two)
    builder.add_edge(START, "one")
    builder.add_edge("one", "ask")
    builder.add_edge("ask", "two")
    builder.add_edge("two", END)
    return builder.compile(checkpointer=checkpointer)


def normalise(snapshot):
    return (
        dict(snapshot.values), snapshot.next, snapshot.metadata.get("step"), snapshot.metadata.get("source"), snapshot.parent_config is not None,
        [(t.name, [i.value for i in t.interrupts]) for t in snapshot.tasks],
    )


async def run_small(checkpointer, config):
    graph = small_graph(checkpointer)
    await graph.ainvoke({"a": 0}, config)
    paused = normalise(await graph.aget_state(config))
    await graph.ainvoke(Command(resume="yes"), config)
    final = normalise(await graph.aget_state(config))
    history = [normalise(s) async for s in graph.aget_state_history(config)]
    return paused, final, history


async def test_the_same_graph_gives_the_same_states_history_and_interrupt_as_langgraphs_own_saver():
    ours, _ = saver()
    mine = await run_small(ours, cfg(durability="sync"))
    theirs = await run_small(InMemorySaver(), {"configurable": {"thread_id": THREAD}})
    assert mine == theirs
    paused, final, history = mine
    assert paused[1] == ("ask",) and paused[5][0][1] == [{"question": "go on?"}] and final[0] == {"a": 11, "answer": "yes"} and final[1] == ()
    assert len(history) >= 4  # the lineage is kept: input, one, ask (paused), two ... every step has a checkpoint


async def test_a_second_thread_does_not_see_the_first_and_each_thread_resumes_independently():
    ours, _ = saver()
    graph = small_graph(ours)
    for thread in ("t-a", "t-b"):
        await graph.ainvoke({"a": 0}, cfg(thread))
    await graph.ainvoke(Command(resume="only a"), cfg("t-a"))
    assert (await graph.aget_state(cfg("t-a"))).values["answer"] == "only a" and (await graph.aget_state(cfg("t-b"))).next == ("ask",)


# ---- 2. the contract, operation by operation ------------------------------------------------------------------------------------------------


def checkpoint(values=None, versions=None):
    c = empty_checkpoint()
    c["channel_values"] = values or {}
    c["channel_versions"] = versions or {k: "00000000000000000000000000000001.0000000000000001" for k in (values or {})}
    return c


async def test_put_and_get_round_trip_channel_values_empty_channels_and_the_parent_chain():
    s, _ = saver()
    first = checkpoint({"x": [1, 2], "y": {"k": "v"}})
    cfg1 = await s.aput(cfg(), first, {"step": 0, "source": "input"}, first["channel_versions"])
    second = checkpoint({"x": [3]}, {"x": "v2", "z": "v1"})
    cfg2 = await s.aput({**cfg1, "configurable": {**cfg1["configurable"], "clinic_id": str(CLINIC), "origin": "test"}}, second, {"step": 1, "source": "loop"}, second["channel_versions"])
    got = await s.aget_tuple(scoped(cfg2))
    assert got is not None and got.checkpoint["channel_values"] == {"x": [3]}  # z has a version but no value: "empty", not returned
    assert got.parent_config["configurable"]["checkpoint_id"] == first["id"] and got.metadata["step"] == 1
    latest = await s.aget_tuple(cfg())  # no checkpoint_id: the latest
    assert latest.config["configurable"]["checkpoint_id"] == second["id"]
    assert (await s.aget_tuple(cfg("nobody"))) is None


async def test_a_replayed_put_changes_nothing_and_every_key_written_is_non_empty_and_prefixed():
    s, store = saver()
    c = checkpoint({"x": 1})
    await s.aput(cfg(), c, {"step": 0}, c["channel_versions"])
    before = (dict(store.checkpoints), dict(store.blobs))
    await s.aput(cfg(), c, {"step": 0}, c["channel_versions"])
    assert (dict(store.checkpoints), dict(store.blobs)) == before
    assert store.keys and all(k.startswith(("cp:", "blob:", "w:")) and len(k) > 4 for k in store.keys)


async def test_regular_writes_are_stored_once_special_writes_are_replaced_and_pending_writes_come_back_in_order():
    s, _ = saver()
    c = checkpoint({"x": 1})
    config = await s.aput(cfg(), c, {"step": 0}, c["channel_versions"])
    wc = {"configurable": {**config["configurable"], "clinic_id": str(CLINIC), "origin": "test"}}
    await s.aput_writes(wc, [("a", 1), ("b", 2)], "task-1", "path-b")
    await s.aput_writes(wc, [("a", 999)], "task-1", "path-b")  # a replay of a regular write: the first value stays
    await s.aput_writes(wc, [("a", 5)], "task-0", "path-a")
    await s.aput_writes(wc, [("__interrupt__", "first")], "task-1", "path-b")
    await s.aput_writes(wc, [("__interrupt__", "second")], "task-1", "path-b")  # special: replaced
    got = (await s.aget_tuple(scoped(config))).pending_writes
    assert got == [("task-0", "a", 5), ("task-1", "__interrupt__", "second"), ("task-1", "a", 1), ("task-1", "b", 2)] or sorted(got) == sorted(
        [("task-0", "a", 5), ("task-1", "__interrupt__", "second"), ("task-1", "a", 1), ("task-1", "b", 2)]
    )
    # ordered by (task_path, task_id, idx): path-a before path-b; within path-b the special (negative idx) entry first
    assert [w[0] for w in got] == ["task-0", "task-1", "task-1", "task-1"] and got[1][1] == "__interrupt__"


async def test_list_is_newest_first_and_honours_before_limit_and_metadata_filter():
    s, _ = saver()
    config, ids = cfg(), []
    for step in range(4):
        c = checkpoint({"x": step})
        out = await s.aput(config, c, {"step": step, "source": "loop" if step else "input"}, c["channel_versions"])
        config, ids = {"configurable": {**out["configurable"], "clinic_id": str(CLINIC), "origin": "test"}}, [*ids, c["id"]]
    listed = [t async for t in s.alist(cfg())]
    assert [t.config["configurable"]["checkpoint_id"] for t in listed] == sorted(ids, reverse=True)
    assert len([t async for t in s.alist(cfg(), limit=2)]) == 2
    before = {"configurable": {"thread_id": THREAD, "checkpoint_ns": "", "checkpoint_id": sorted(ids)[2]}}
    assert [t.config["configurable"]["checkpoint_id"] async for t in s.alist(cfg(), before=before)] == sorted(ids, reverse=True)[2:]
    assert len([t async for t in s.alist(cfg(), filter={"source": "input"})]) == 1
    assert [t async for t in s.alist(cfg("nobody"))] == []
    with pytest.raises(CheckpointerError):
        [t async for t in s.alist(None)]  # a list is always for one clinic: listing across clinics is refused


async def test_deleting_a_thread_is_refused_because_the_history_is_append_only():
    s, store = saver()
    c = checkpoint({"x": 1})
    await s.aput(cfg(), c, {"step": 0}, c["channel_versions"])
    with pytest.raises(HistoryIsAppendOnly):
        await s.adelete_thread(THREAD)
    assert len(store.checkpoints) == 1


async def test_a_write_without_a_clinic_or_with_an_unknown_origin_is_refused_and_nothing_is_stored():
    s, store = saver()
    c = checkpoint({"x": 1})
    for conf in ({"clinic_id": None}, {"origin": None}, {"origin": "prod"}, {"clinic_id": "not-a-uuid"}):
        config = cfg()
        config["configurable"].update(conf)
        with pytest.raises(CheckpointerError):
            await s.aput(config, c, {"step": 0}, c["channel_versions"])
        with pytest.raises(CheckpointerError):
            await s.aput_writes({"configurable": {**config["configurable"], "checkpoint_id": c["id"]}}, [("a", 1)], "t", "")
    assert not store.checkpoints and not store.blobs and not store.writes


async def test_a_failed_put_leaves_neither_a_checkpoint_nor_a_blob():
    s, store = saver()
    store.fail_next_put = 1
    c = checkpoint({"x": 1})
    with pytest.raises(RuntimeError):
        await s.aput(cfg(), c, {"step": 0}, c["channel_versions"])
    assert not store.checkpoints and not store.blobs and await s.aget_tuple(cfg()) is None


# ---- 3. durability: kill the process mid-run, restart over the same store ---------------------------------------------------------------------


class World:
    """The EXTERNAL things (the fakes that stand for the database, the model and the clinic's systems) outlive a restart; the graph, the saver and the process do not."""

    def __init__(self, **kw):
        self.log: list[str] = []
        self.store = InMemoryCheckpointStore()
        conn = Conn(log=self.log)

        @asynccontextmanager
        async def session():
            yield conn

        self.session = session
        self.engine, self.extractor, self.ingestor = Engine(self.log, kw.get("outcome", "clear")), Extractor(self.log, GOOD), Ingestor(self.log)
        self.proposer, self.recorder, self.holds, self.retriever = Proposer(self.log, "proposal"), Recorder(self.log, "decision"), Holds(self.log, "hold"), Retriever(self.log)

    def boot(self):
        """A fresh process: new deps object, new saver, new compiled graph, the same store and the same external fakes."""
        deps = FrontDoorDeps(
            session=self.session, engine=self.engine, texts=Texts(self.log), extractor=self.extractor, ingestor=self.ingestor, retriever=self.retriever,  # type: ignore[arg-type]
            proposer=self.proposer, recorder=self.recorder, holds=self.holds, retrieval_scope={"domain": "demo"}, profile="demo",
        )
        return build_front_door(deps, PostgresCheckpointSaver(self.store), environ={})


CONFIG, START_STATE = run_config(DOC, CLINIC, "test"), initial_state(DOC, CLINIC, "test")


async def test_the_run_config_asks_for_sync_durability_and_scopes_every_row():
    conf = CONFIG["configurable"]
    assert conf["__pregel_durability"] == "sync" and conf["clinic_id"] == str(CLINIC) and conf["origin"] == "test" and conf["thread_id"] == f"front_door:{DOC}"


async def test_a_crash_before_the_proposal_then_a_restart_resumes_from_the_stored_checkpoint_and_repeats_no_side_effect():
    w = World()
    w.proposer.crash_before_effect = 1
    first = w.boot()
    with pytest.raises(RuntimeError):
        await first.ainvoke(START_STATE, CONFIG)
    del first  # the process is gone
    second = w.boot()  # a new graph, a new saver, the same store
    stored = await second.aget_state(CONFIG)
    assert stored.next == ("propose_triage",) and stored.values["status"] == "matched"  # every step before the crash was already durable
    await second.ainvoke(None, CONFIG)
    assert (await second.aget_state(CONFIG)).next == ("review",)
    assert len(w.ingestor.calls) == 1 and w.log.count("MODEL:extract") == 1 and len(w.proposer.performed) == 1


async def test_a_crash_after_the_effect_before_the_checkpoint_repeats_the_node_not_the_effect_even_after_a_restart():
    w = World()
    w.proposer.crash_after_effect = 1
    with pytest.raises(RuntimeError):
        await w.boot().ainvoke(START_STATE, CONFIG)
    resumed = w.boot()
    await resumed.ainvoke(None, CONFIG)
    assert len(w.proposer.attempts) == 2 and len(w.proposer.performed) == 1 and set(w.proposer.attempts) == {f"triage:{DOC}"}


async def test_a_restart_inside_the_extractor_screens_again_before_calling_the_model_again():
    w = World()
    w.extractor.raises = RuntimeError("the process died mid-call")
    with pytest.raises(RuntimeError):
        await w.boot().ainvoke(START_STATE, CONFIG)
    w.extractor.raises = None
    await w.boot().ainvoke(None, CONFIG)
    seen_rules = False
    for event in w.log:
        seen_rules = seen_rules or event == "rules"
        assert not event.startswith("MODEL") or seen_rules
    assert w.log.count("MODEL:extract") == 2 and w.log.count("rules") >= 3 and len(w.ingestor.calls) == 1


async def test_a_paused_review_survives_a_restart_and_the_decision_is_recorded_exactly_once():
    w = World()
    await w.boot().ainvoke(START_STATE, CONFIG)
    restarted = w.boot()
    assert (await restarted.aget_state(CONFIG)).next == ("review",) and w.recorder.attempts == []
    await restarted.ainvoke(Command(resume={"decision": "approve", "reviewer_id": REVIEWER}), CONFIG)
    await w.boot().ainvoke(None, CONFIG)  # another restart and a poke: a finished thread does nothing
    assert list(w.recorder.performed) == [f"review:{DOC}"] and len(w.ingestor.calls) == 1


async def test_a_database_failure_while_saving_a_checkpoint_stops_the_run_and_the_resume_repeats_no_effect():
    w = World()
    first = w.boot()
    await first.ainvoke(START_STATE, CONFIG)  # runs to the pause
    w.store.fail_next_put = 1
    with pytest.raises(RuntimeError):
        await first.ainvoke(Command(resume={"decision": "approve", "reviewer_id": REVIEWER}), CONFIG)
    await w.boot().ainvoke(Command(resume={"decision": "approve", "reviewer_id": REVIEWER}), CONFIG)
    assert len(w.recorder.performed) == 1 and len(w.proposer.performed) == 1 and len(w.ingestor.calls) == 1


async def test_nothing_in_the_stored_checkpoints_blobs_or_writes_holds_patient_text():
    w = World()
    await w.boot().ainvoke(START_STATE, CONFIG)
    everything = b"".join(
        [v[2][1] + v[3][1] for v in w.store.checkpoints.values()] + [v[1][1] for v in w.store.blobs.values()] + [v[2][1] for v in w.store.writes.values()]
    )
    assert_not_leaked(everything, label="the stored checkpoints, blobs and writes")
    assert everything  # the probe is not vacuous: something was stored


# ---- 4. the SQL store's statements -------------------------------------------------------------------------------------------------------------


class Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class RecordingConn:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return Result()


def sql_store():
    conn = RecordingConn()

    @asynccontextmanager
    async def session():
        yield conn

    return SqlCheckpointStore(session), conn


async def test_every_insert_carries_the_clinic_the_origin_and_an_idempotency_key_and_a_put_is_one_transaction():
    store, conn = sql_store()
    scope = Scope(CLINIC, "test")
    await store.put_checkpoint(scope, "t", "", "cp-1", None, ("msgpack", b"c"), ("json", b"m"), [("x", "v1", ("msgpack", b"b")), ("y", "v1", ("empty", b""))])
    await store.put_writes(scope, "t", "", "cp-1", "task", "path", [(0, "a", ("msgpack", b"w")), (-3, "__interrupt__", ("msgpack", b"i"))])
    inserts = [c for c in conn.calls if c[0].lstrip().lower().startswith("insert")]
    assert len(inserts) == 5  # two blobs, one checkpoint, two writes
    for sql, params in inserts:
        assert params["c"] == CLINIC and params["o"] == "test" and params["k"] and str(params["k"]).split(":")[0] in ("cp", "blob", "w")
        assert "on conflict" in sql
    regular, special = [c for c in inserts if "graph_checkpoint_writes" in c[0]]
    assert "do nothing" in regular[0] and "do update set" in special[0] and special[1]["idx"] == -3


async def test_the_statements_never_delete_update_a_checkpoint_or_truncate():
    store, conn = sql_store()
    await store.put_checkpoint(Scope(CLINIC, "test"), "t", "", "cp-1", None, ("a", b"c"), ("a", b"m"), [])
    await store.get_checkpoint(CLINIC, "t", "", None)
    await store.list_checkpoints(CLINIC, "t", "", None, "cp-9")
    await store.get_blobs(CLINIC, "t", "", {"x": "v1"})
    await store.get_writes(CLINIC, "t", "", "cp-1")
    text_ = " ".join(c[0].lower() for c in conn.calls)
    assert "delete " not in text_ and "truncate" not in text_ and "update agents.graph_checkpoints" not in text_ and "update agents.graph_checkpoint_blobs" not in text_
    assert json.dumps([c[1] for c in conn.calls], default=str)  # parameters are plain values


# ---- every read is scoped to the clinic in the config (reviewer H1) -----------------------------------------------------------------------------


OTHER = uuid.uuid4()


def cfg_for(clinic, thread=THREAD, **over):
    config = cfg(thread, **over)
    config["configurable"]["clinic_id"] = str(clinic)
    return config


async def test_two_clinics_with_the_same_thread_id_never_see_each_others_checkpoints_blobs_or_writes():
    s, _ = saver()
    a, b = checkpoint({"x": "clinic A value"}), checkpoint({"x": "clinic B value"})
    out_a = await s.aput(cfg_for(CLINIC), a, {"step": 0}, a["channel_versions"])
    out_b = await s.aput(cfg_for(OTHER), b, {"step": 0}, b["channel_versions"])
    await s.aput_writes({"configurable": {**cfg_for(CLINIC)["configurable"], "checkpoint_id": a["id"]}}, [("w", "A")], "task", "")
    await s.aput_writes({"configurable": {**cfg_for(OTHER)["configurable"], "checkpoint_id": b["id"]}}, [("w", "B")], "task", "")
    got_a, got_b = await s.aget_tuple(cfg_for(CLINIC)), await s.aget_tuple(cfg_for(OTHER))
    assert got_a.checkpoint["channel_values"] == {"x": "clinic A value"} and got_b.checkpoint["channel_values"] == {"x": "clinic B value"}
    assert got_a.pending_writes == [("task", "w", "A")] and got_b.pending_writes == [("task", "w", "B")]
    assert [t.checkpoint["channel_values"] async for t in s.alist(cfg_for(CLINIC))] == [{"x": "clinic A value"}]
    # a later-sorting checkpoint written in clinic B under the same thread must not displace clinic A's latest
    later = checkpoint({"x": "poison"})
    later["id"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    await s.aput(cfg_for(OTHER), later, {"step": 9}, later["channel_versions"])
    assert (await s.aget_tuple(cfg_for(CLINIC))).config["configurable"]["checkpoint_id"] == a["id"]
    assert out_a["configurable"]["checkpoint_id"] != out_b["configurable"]["checkpoint_id"]


async def test_a_read_without_a_clinic_is_refused_not_answered_from_whatever_exists():
    s, _ = saver()
    c = checkpoint({"x": 1})
    await s.aput(cfg(), c, {"step": 0}, c["channel_versions"])
    for conf in ({"clinic_id": None}, {"clinic_id": "not-a-uuid"}):
        config = cfg()
        config["configurable"].update(conf)
        with pytest.raises(CheckpointerError):
            await s.aget_tuple(config)
        with pytest.raises(CheckpointerError):
            [t async for t in s.alist(config)]
    bare = {"configurable": {"thread_id": THREAD, "checkpoint_ns": ""}}
    with pytest.raises(CheckpointerError):
        await s.aget_tuple(bare)


async def test_the_idempotency_keys_cannot_collide_across_a_thread_id_that_contains_the_delimiter():
    from careflow.checkpointer import idempotency_key

    assert idempotency_key("w", "c", "a:b", "", "cp", "t", 0) != idempotency_key("w", "c", "a", "b:", "cp", "t", 0)
    assert idempotency_key("cp", "c", "t", "", "id") == idempotency_key("cp", "c", "t", "", "id")  # deterministic: a replay finds the same key
    assert idempotency_key("cp", "x").startswith("cp:") and idempotency_key("blob", "x").startswith("blob:") and idempotency_key("w", "x").startswith("w:")


async def test_a_special_write_put_twice_is_replaced_a_regular_one_stays_through_the_store_directly():
    s, _ = saver()
    c = checkpoint({"x": 1})
    config = await s.aput(cfg(), c, {"step": 0}, c["channel_versions"])
    wc = {"configurable": {**config["configurable"], "clinic_id": str(CLINIC), "origin": "test"}}
    for value in ("one", "two"):
        await s.aput_writes(wc, [("__interrupt__", value), ("a", value)], "task", "")
    pending = {(w[1]): w[2] for w in (await s.aget_tuple(wc)).pending_writes}
    assert pending == {"__interrupt__": "two", "a": "one"}


async def test_the_durability_key_in_the_run_config_is_really_honoured_sync_persists_every_step_exit_does_not():
    """Guards the private LangGraph constant we import: if an upgrade stopped honouring it, the 'sync' run below would persist like the 'exit' run."""
    counts = {}
    for mode in ("sync", "exit"):
        w = World()
        w.proposer.crash_before_effect = 1
        config = run_config(DOC, CLINIC, "test")
        config["configurable"]["__pregel_durability"] = mode
        with pytest.raises(RuntimeError):
            await w.boot().ainvoke(START_STATE, config)
        counts[mode] = len(w.store.checkpoints)
    assert counts["sync"] > counts["exit"], counts  # every completed step was saved before the crash, versus only at exit
