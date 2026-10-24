"""The Front Door graph's behaviour (careflow/front_door.py). Offline: LangGraph's InMemorySaver plays the checkpointer and every edge is a fake sharing ONE
event log, so the tests can prove ordering (rules before any model call, on every path including a resume after a crash) and effects (a crash and resume
repeats nothing, every write carries an idempotency key). No database, no model, no network. Synthetic names only."""

import contextlib
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal  # noqa: F401  (kept for fakes that may price calls)

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from careflow.front_door import (
    RECURSION_LIMIT,
    FrontDoorDeps,
    FrontDoorError,
    TriageProposal,
    build_front_door,
    initial_state,
    run_config,
)
from careflow.retrieval import Candidate, Retrieval
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
REVIEWER = str(uuid.uuid4())


class Texts:
    def __init__(self, log):
        self.log = log

    async def text_for(self, document_id):
        self.log.append("text")
        return LETTER


class Retriever:
    def __init__(self, log, abstain=False):
        self.log, self.abstain = log, abstain

    async def retrieve(self, query, *, scope):
        self.log.append("MODEL:retrieve")
        assert scope == {"domain": "demo"} and "SENTINEL-LETTER-91" in query
        if self.abstain:
            return Retrieval(True, "below_threshold")
        return Retrieval(False, None, (Candidate("hours", 1, 0.03, 0.9, 0.5),))


class Effects:
    """Records every attempt and performs an effect only for a key not seen before (what an idempotent writer does)."""

    def __init__(self, log, name):
        self.log, self.name, self.attempts, self.performed, self.crash_after_effect, self.crash_before_effect = log, name, [], {}, 0, 0

    def _do(self, key):
        assert isinstance(key, str) and key and str(DOC) in key, "every write carries an idempotency key derived from the document"
        self.attempts.append(key)
        self.log.append(f"effect:{self.name}")
        if self.crash_before_effect:
            self.crash_before_effect -= 1
            raise RuntimeError("crash before the effect")
        self.performed.setdefault(key, len(self.performed) + 1)
        if self.crash_after_effect:
            self.crash_after_effect -= 1
            raise RuntimeError("crash after the effect, before the checkpoint")


class Proposer(Effects):
    async def propose(self, conn, *, document_id, clinic_id, origin, patient_id, candidates, abstained, idempotency_key):
        self._do(idempotency_key)
        self.last = {"patient_id": patient_id, "candidates": candidates, "abstained": abstained}
        return TriageProposal(uuid.uuid5(uuid.NAMESPACE_URL, idempotency_key))


class Recorder(Effects):
    async def record(self, conn, *, proposal_id, clinic_id, decision, reviewer_id, idempotency_key):
        self._do(idempotency_key)
        self.decision = decision


def world(outcome="clear", *, answer=GOOD, abstain=False, engine_fail=None, extract_raises=None):
    log: list[str] = []
    conn = Conn(log=log)

    @asynccontextmanager
    async def session():
        yield conn

    engine = Engine(log, outcome, engine_fail)
    extractor = Extractor(log, answer, extract_raises)
    ingestor = Ingestor(log)
    proposer, recorder = Proposer(log, "proposal"), Recorder(log, "decision")
    deps = FrontDoorDeps(
        session=session, engine=engine, texts=Texts(log), extractor=extractor, ingestor=ingestor, retriever=Retriever(log, abstain),  # type: ignore[arg-type]
        proposer=proposer, recorder=recorder, retrieval_scope={"domain": "demo"}, profile="demo",
    )
    saver = InMemorySaver()
    graph = build_front_door(deps, saver, environ={})
    return graph, log, saver, extractor, ingestor, proposer, recorder


CONFIG = run_config(DOC)
START = initial_state(DOC, CLINIC, "test")


def model_events(log):
    return [e for e in log if e.startswith("MODEL")]


def assert_rules_before_every_model_call(log):
    seen_rules = False
    for event in log:
        if event == "rules":
            seen_rules = True
        if event.startswith("MODEL"):
            assert seen_rules, f"a model was called before the rules ran: {log}"


# ---- the happy path ------------------------------------------------------------------------------------------------------------------------


async def test_a_clear_referral_runs_to_the_review_pause_with_the_proposal_written_and_nothing_approved():
    graph, log, _, _, ingestor, proposer, recorder = world()
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == ("review",) and state.values["status"] == "triage_proposed"
    assert state.values["candidates"] == [{"card_id": "hours", "version": 1}] and state.values["abstained"] is False
    assert len(ingestor.calls) == 1 and len(proposer.performed) == 1 and recorder.performed == {}  # paused: no decision, no timeout approval
    assert_rules_before_every_model_call(log)


async def test_resuming_with_a_decision_records_it_once_and_finishes():
    graph, *_, recorder = world()
    await graph.ainvoke(START, CONFIG)
    final = await graph.ainvoke(Command(resume={"decision": "approve", "reviewer_id": REVIEWER}), CONFIG)
    assert final["status"] == "reviewed" and (await graph.aget_state(CONFIG)).next == ()
    assert list(recorder.performed) == [f"review:{DOC}"] and recorder.decision == "approve"


async def test_a_card_abstention_still_goes_to_a_human_as_a_proposal_that_says_so():
    graph, *_, proposer, _ = world(abstain=True)
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == ("review",) and proposer.last["abstained"] is True and proposer.last["candidates"] == []
    assert state.values["abstain_reason"] == "below_threshold"


# ---- nothing is approved by default ----------------------------------------------------------------------------------------------------------


async def test_poking_a_paused_run_without_a_decision_approves_nothing():
    graph, *_, recorder = world()
    await graph.ainvoke(START, CONFIG)
    for _ in range(3):  # a retry, a tick, a restart: none of them is a decision
        await graph.ainvoke(None, CONFIG)
        assert (await graph.aget_state(CONFIG)).next == ("review",)
    assert recorder.attempts == []


@pytest.mark.parametrize(
    "bad",
    [{"decision": "approve-all", "reviewer_id": REVIEWER}, {"decision": "approve"}, {"decision": "approve", "reviewer_id": "not-a-uuid"}, "approve"],
)
async def test_a_malformed_decision_is_refused_and_records_nothing(bad):
    graph, *_, recorder = world()
    await graph.ainvoke(START, CONFIG)
    with pytest.raises(FrontDoorError):
        await graph.ainvoke(Command(resume=bad), CONFIG)
    assert recorder.attempts == []


# ---- rule 2: the rules before any model call, on every path ---------------------------------------------------------------------------------------


async def test_a_red_flag_escalates_and_ends_the_run_with_no_model_call_and_no_proposal():
    graph, log, _, _, ingestor, proposer, _ = world("red_flag")
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == () and state.values["status"] == "red_flag_escalated"
    assert model_events(log) == [] and "escalate" in log and ingestor.calls == [] and proposer.attempts == []


async def test_an_unassessable_screening_ends_the_run_held_with_no_model_call():
    graph, log, *_ , proposer, _ = world("unassessable")
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == () and state.values["status"] == "held_unassessable" and model_events(log) == [] and proposer.attempts == []


async def test_when_the_rules_cannot_run_the_run_fails_and_no_model_is_ever_called():
    graph, log, *_ = world(engine_fail=RuntimeError("the rule table could not be read"))
    with pytest.raises(RuntimeError):
        await graph.ainvoke(START, CONFIG)
    assert model_events(log) == []


async def test_facts_that_do_not_verify_end_the_run_for_the_front_desk_without_a_proposal():
    from careflow.referral import ExtractedReferral

    graph, log, _, _, ingestor, proposer, _ = world(answer=ExtractedReferral(name="Asha Rau", name_quote="Asha Rau", dob="1980-03-05", dob_quote="5 March 1980"))
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.next == () and state.values["status"] == "needs_front_desk" and ingestor.calls == [] and proposer.attempts == []
    assert_rules_before_every_model_call(log)


async def test_a_model_outage_in_extraction_ends_the_run_visibly_not_silently():
    from careflow.ports import TransientError

    graph, *_ , proposer, _ = world(extract_raises=TransientError("down"))
    await graph.ainvoke(START, CONFIG)
    state = await graph.aget_state(CONFIG)
    assert state.values["status"] == "extraction_unavailable" and state.next == () and proposer.attempts == []


async def test_after_a_crash_in_the_extractor_the_resume_screens_again_before_calling_the_model_again():
    graph, log, _, extractor, ingestor, *_ = world(extract_raises=RuntimeError("the process died mid-call"))
    with pytest.raises(RuntimeError):
        await graph.ainvoke(START, CONFIG)
    extractor.raises = None
    await graph.ainvoke(None, CONFIG)
    assert (await graph.aget_state(CONFIG)).next == ("review",)
    assert log.count("MODEL:extract") == 2 and log.count("rules") >= 3  # the screen node, then the rules again before each extract
    assert_rules_before_every_model_call(log)
    assert len(ingestor.calls) == 1


# ---- crash and resume repeat no side effect --------------------------------------------------------------------------------------------------


async def test_a_crash_after_a_completed_step_resumes_from_the_checkpoint_and_does_not_redo_it():
    graph, _, _, _, ingestor, proposer, _ = world()
    proposer.crash_before_effect = 1  # the proposal step dies; the identity step before it has already been checkpointed
    with pytest.raises(RuntimeError):
        await graph.ainvoke(START, CONFIG)
    await graph.ainvoke(None, CONFIG)
    assert len(ingestor.calls) == 1  # identity was NOT redone
    assert len(proposer.performed) == 1 and len(proposer.attempts) == 2
    assert (await graph.aget_state(CONFIG)).next == ("review",)


async def test_a_crash_after_the_effect_but_before_the_checkpoint_repeats_the_node_but_not_the_effect():
    graph, *_, proposer, _ = world()
    proposer.crash_after_effect = 1  # the effect happened, then the process died before the step was saved
    with pytest.raises(RuntimeError):
        await graph.ainvoke(START, CONFIG)
    await graph.ainvoke(None, CONFIG)
    assert len(proposer.attempts) == 2 and len(proposer.performed) == 1  # same idempotency key both times: one proposal
    assert set(proposer.attempts) == {f"triage:{DOC}"}


async def test_a_crash_while_recording_the_decision_repeats_nothing_on_resume():
    graph, *_, recorder = world()
    await graph.ainvoke(START, CONFIG)
    recorder.crash_after_effect = 1
    with pytest.raises(RuntimeError):
        await graph.ainvoke(Command(resume={"decision": "reject", "reviewer_id": REVIEWER}), CONFIG)
    await graph.ainvoke(None, CONFIG)
    assert len(recorder.performed) == 1 and set(recorder.attempts) == {f"review:{DOC}"}
    assert (await graph.aget_state(CONFIG)).values["status"] == "reviewed"


async def test_running_the_same_referral_twice_in_the_same_thread_does_not_redo_finished_work():
    graph, _, _, _, ingestor, proposer, recorder = world()
    await graph.ainvoke(START, CONFIG)
    await graph.ainvoke(Command(resume={"decision": "approve", "reviewer_id": REVIEWER}), CONFIG)
    await graph.ainvoke(None, CONFIG)  # a finished thread: nothing runs
    assert len(ingestor.calls) == 1 and len(proposer.performed) == 1 and len(recorder.performed) == 1


# ---- what is checkpointed, and the ceilings -----------------------------------------------------------------------------------------------------


async def test_no_letter_text_and_no_extracted_value_is_ever_written_to_a_checkpoint():
    graph, _, saver, *_ = world()
    await graph.ainvoke(START, CONFIG)
    await graph.ainvoke(Command(resume={"decision": "approve", "reviewer_id": REVIEWER}), CONFIG)
    blobs = b"".join(v if isinstance(v, bytes) else repr(v).encode() for v in _walk(saver.storage)) + b"".join(
        blob[1] if isinstance(blob, tuple) and isinstance(blob[1], bytes) else b"" for blob in saver.blobs.values()
    )
    for needle in (b"SENTINEL-LETTER", b"Asha", b"Rao", b"1980", b"98765", b"9876543210"):
        assert needle not in blobs, needle
    values = repr((await graph.aget_state(CONFIG)).values)
    assert "Asha" not in values and "1980" not in values and "SENTINEL" not in values


def _walk(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, (tuple, list)):
        for v in obj:
            yield from _walk(v)
    elif isinstance(obj, bytes):
        yield obj


async def test_every_run_is_capped_at_40_steps_and_keyed_by_a_stable_thread_per_document():
    assert RECURSION_LIMIT == 40 and CONFIG["recursion_limit"] == 40
    assert CONFIG["configurable"]["thread_id"] == f"front_door:{DOC}"
    assert run_config(DOC) == run_config(DOC)


async def test_resuming_with_no_value_at_all_is_not_a_decision_whatever_langgraph_does_with_it():
    graph, *_, recorder = world()
    await graph.ainvoke(START, CONFIG)
    with contextlib.suppress(Exception):  # LangGraph 1.2 raises UnboundLocalError here; either way nothing may be recorded
        await graph.ainvoke(Command(resume=None), CONFIG)
    assert recorder.attempts == [] and (await graph.aget_state(CONFIG)).next == ("review",)
