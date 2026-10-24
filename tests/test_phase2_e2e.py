"""PHASE 2 END TO END, offline (BUILD-SPEC.md Appendix D, Phase 2; docs/design/p2-10-end-to-end.md).

A referral letter goes through the REAL Front Door graph, the REAL referral/grounding code, the REAL card retriever (BM25 + RRF + version-checked join +
abstention) and the REAL triage writers' shapes, to a human decision. The stand-ins are at the edges only: the model-facing calls (the extractor and the
embedder: scripted), the vector store (in-memory), the database connection (a recording fake), and the checkpointer (InMemorySaver). Cards are SYNTHETIC
non-clinical stand-ins; every threshold is a test input.

Safety properties of the phase (NOT the whole of Phase 2 acceptance: the contract test, extraction, the gold-set gate and a real database run are missing), asserted TOGETHER on one run: rules before any model call; no unverified fact reaches identity; nothing approved without a human;
crash and resume repeat no effect; abstention goes to a human; no patient text in the checkpoint; the structural checker passes on this very graph.
The live (real database) variant is NOT here: under the present policies no single role can run the clean path (docs/design/p2-10-end-to-end.md)."""

import uuid
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from careflow.adapters.fake import FakeVectorStore
from careflow.front_door import (
    MODEL_PORTS,
    NEUTRAL_PORTS,
    NODE_SPECS,
    SIDE_EFFECT_PORTS,
    FrontDoorDeps,
    build_front_door,
    initial_state,
    run_config,
)
from careflow.graph_structure import violations
from careflow.ports import AdapterResult, EmbeddingResult, Usage, VectorPoint
from careflow.retrieval import (
    AbstentionPolicy,
    Bm25Index,
    CardFields,
    CardRetriever,
    retrieval_text,
)
from tests.test_front_door import Holds, Proposer, Recorder, Texts
from tests.test_referral import (
    CLINIC,
    DOC,
    GOOD,
    LETTER,
    Conn,
    Engine,
    Extractor,
    Ingestor,
)

pytestmark = pytest.mark.asyncio
SCOPE = {"domain": "demo"}
REVIEWER = str(uuid.uuid4())
CARDS = {
    "intake": CardFields("intake", 1, "Patient please see", "A letter asking for a patient to be seen", ("referral letter",), ("please see",), ("X1",)),
    "billing": CardFields("billing", 1, "Billing", "Invoices", ("invoice",), ("bill",), ("B1",)),
}


class Embedder:
    """Scripted: every query embeds to the first axis, so the dense side agrees with the 'intake' card."""

    def __init__(self):
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return AdapterResult(EmbeddingResult([[1.0, 0.0]], 2), Usage(1, 0))


class Truth:
    def __init__(self, versions):
        self.versions = versions

    async def current_versions(self, ids):
        return {c: v for c, v in self.versions.items() if c in ids}


class LoggedRetriever(CardRetriever):
    def __init__(self, log, **kw):
        super().__init__(**kw)
        self.log = log

    async def retrieve(self, query, *, scope):
        self.log.append("MODEL:retrieve")
        return await super().retrieve(query, scope=scope)


async def build(*, policy, versions=None, outcome="clear", answer=GOOD):
    log: list[str] = []
    conn = Conn(log=log)

    @asynccontextmanager
    async def session():
        yield conn

    store = FakeVectorStore()
    await store.upsert([VectorPoint("intake", 1, (1.0, 0.0), dict(SCOPE)), VectorPoint("billing", 1, (0.0, 1.0), dict(SCOPE))])
    bm25 = Bm25Index({k: retrieval_text(c) for k, c in CARDS.items()}, {k: dict(SCOPE) for k in CARDS}, {k: c.version for k, c in CARDS.items()})
    retriever = LoggedRetriever(log, embedder=Embedder(), store=store, truth=Truth(versions or {"intake": 1, "billing": 1}), bm25=bm25, policy=policy)
    deps = FrontDoorDeps(
        session=session, engine=Engine(log, outcome), texts=Texts(log), extractor=Extractor(log, answer), ingestor=Ingestor(log), retriever=retriever,  # type: ignore[arg-type]
        proposer=Proposer(log, "proposal"), recorder=Recorder(log, "decision"), holds=Holds(log, "hold"), retrieval_scope=dict(SCOPE), profile="demo",
    )
    saver = InMemorySaver()
    return build_front_door(deps, saver, environ={}), deps, saver, log


CONFIG, START = run_config(DOC), initial_state(DOC, CLINIC, "test")
CALIBRATED = AbstentionPolicy(min_dense_similarity=0.5, min_bm25_ratio=0.05)  # TEST INPUTS, not recommendations


def first_model_index(log):
    return next(i for i, e in enumerate(log) if e.startswith("MODEL"))


async def test_an_offline_referral_goes_from_letter_to_a_human_decision_with_the_safety_properties_holding_together():
    graph, deps, saver, log = await build(policy=CALIBRATED)
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)

    # 1. rules before any model call, on this path
    assert "rules" in log and log.index("rules") < first_model_index(log)
    # 2. identity came from VERIFIED facts only, and the proposal came from real retrieval
    assert len(deps.ingestor.calls) == 1 and deps.ingestor.calls[0]["dob"] == "1980-03-05"
    assert state.values["candidates"][0] == {"card_id": "intake", "version": 1} and state.values["abstained"] is False  # only the TOP one is gated by the policy
    # 3. paused for a human: nothing is approved, nothing is recorded
    assert state.next == ("review",) and deps.recorder.attempts == [] and len(deps.proposer.performed) == 1
    # 4. no patient text in the checkpoint
    blob = repr(state.values) + repr(saver.storage)
    assert not any(needle in blob for needle in ("Asha", "SENTINEL-LETTER", "1980", "98765"))
    # 5. the structural gate passes on THIS graph
    assert violations(graph.node_functions, NODE_SPECS, SIDE_EFFECT_PORTS, MODEL_PORTS, NEUTRAL_PORTS) == []

    final = await graph.ainvoke(Command(resume={"decision": "approve", "reviewer_id": REVIEWER}), CONFIG)
    assert final["status"] == "reviewed" and list(deps.recorder.performed) == [f"review:{DOC}"]


async def test_with_no_calibrated_policy_retrieval_abstains_and_the_proposal_goes_to_a_human_saying_so():
    graph, deps, _, _ = await build(policy=None)
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.values["abstained"] is True and state.values["candidates"] == [] and state.values["abstain_reason"] == "no_calibrated_policy"
    assert state.next == ("review",) and deps.recorder.attempts == []
    assert deps.retriever.embedder.calls == 0  # without a policy the query is not even embedded


async def test_a_card_whose_index_is_stale_in_postgres_is_never_proposed():
    graph, *_ = await build(policy=CALIBRATED, versions={"intake": 2, "billing": 1})  # Postgres moved 'intake' on; the index still holds version 1
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert all(c["card_id"] != "intake" for c in state.values["candidates"])


async def test_a_red_flag_stops_everything_before_any_model_and_proposes_nothing():
    graph, deps, _, log = await build(policy=CALIBRATED, outcome="red_flag")
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.values["status"] == "red_flag_escalated" and state.next == ()
    assert not any(e.startswith("MODEL") for e in log) and deps.proposer.attempts == [] and deps.ingestor.calls == []


async def test_a_crash_in_the_proposal_step_resumes_without_repeating_identity_or_the_proposal():
    graph, deps, *_ = await build(policy=CALIBRATED)
    deps.proposer.crash_after_effect = 1
    with pytest.raises(RuntimeError):
        await graph.ainvoke(START, CONFIG)
    await graph.ainvoke(None, CONFIG)
    assert len(deps.ingestor.calls) == 1 and len(deps.proposer.performed) == 1 and (await graph.aget_state(CONFIG)).next == ("review",)


async def test_a_letter_whose_name_does_not_verify_never_reaches_identity_or_a_proposal():
    from careflow.referral import ExtractedReferral

    bad = ExtractedReferral(name="Asha Rau", name_quote="Asha Rau", dob="1980-03-05", dob_quote="5 March 1980")
    graph, deps, *_ = await build(policy=CALIBRATED, answer=bad)
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.values["status"] == "needs_front_desk" and deps.ingestor.calls == [] and deps.proposer.attempts == [] and LETTER
