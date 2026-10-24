"""The Front Door graph (BUILD-SPEC.md §1, §4, §5.2, §7; CLAUDE.md rules 2, 3, 4, 5, 6, 7). Package P2-8.

A LangGraph graph whose every routing decision is deterministic code: no model chooses the path (§7.1). Nodes, in order:

    screen -> resolve_identity -> match_protocol -> propose_triage -> review (PAUSE) -> apply_review

  * `screen`           the deterministic red-flag rules on the letter. A red flag escalates (nurse task) and ENDS the run; an unassessable
                       screening ENDS the run (held); only `clear` goes on. No model has been consulted at this point.
  * `resolve_identity` P2-7 `process_referral`: it screens AGAIN before it will call the extractor (rules first on EVERY path, including a
                       resume after a crash), grounds the extracted facts, and calls mpi ingestion with verified facts only. Anything short of
                       `identity_resolved` ENDS the run for a human (front desk). The model call and the identity write are in one node on
                       purpose: the extracted name and date of birth never enter the checkpoint (see below).
  * `match_protocol`   card retrieval (P2-6). It screens AGAIN first (the retriever embeds the letter with a model, and a resume after a crash lands here
                       without passing through `resolve_identity`); a red flag found now ends the run. Abstention is a normal result and goes on to a human.
  * `propose_triage`   writes the triage PROPOSAL and its review task (P2-9 supplies the real writer). A SIDE EFFECT, and the step immediately
                       before the pause, as §7.2 requires.
  * `hold_for_human`   NO RUN ENDS SILENTLY. Every outcome that is not a finished review and not a red-flag escalation (which has its own immediate nurse task) lands
                       here: an unassessable screening, an unavailable extraction, facts that did not verify, and ANY status this graph does not know. It writes a
                       review task (kind `referral_hold`): a letter with no usable date of birth or an unverified name goes to front desk (§17.2), everything
                       else to a nurse (§16.2). The task's priority is a required argument of the writer, marked CLINICAL-PLACEHOLDER, never a default.
  * `review`           PAUSES (`interrupt`) for a human decision. NO side effect. It re-executes from its start on resume, which is harmless.
  * `apply_review`     records the human's decision. A side effect, after the pause. Nothing is approved by a timeout: with no resume value the
                       run simply stays paused.

THE STRUCTURAL RULE (§7.2, §4): a node that pauses never has a side effect. Each node below is declared with a `NodeSpec` (its side effects, whether
it calls a model, whether it pauses) and `tests/test_front_door_structure.py` checks the declarations against the node SOURCE (which ports it touches,
whether it calls `interrupt`) and against the compiled graph, as a hard CI gate. The topology test also pins the agent inventory: one graph
(`front_door`), no fourth agent, and no Care Journey graph while `FEATURE_CARE_JOURNEY` is off (turning the flag on before the graph exists fails loudly).

WHAT IS IN THE STATE (and so in every checkpoint): ids, short fixed-word statuses, retrieval candidates as (card id, version), a proposal id. NO letter
text, NO extracted name, date of birth or phone, NO query. Nodes fetch the letter through `TextSource` each time they need it (the real one reads the
stored document through `DocumentIntake.read_verified`, which re-hashes the bytes). So the checkpoint store holds operational identifiers, not patient
text, and rule 6 (no agent-owned memory of a patient) holds.

EVERY WRITE CARRIES AN IDEMPOTENCY KEY derived from the document id: `redflag:document:<id>:<digest>` (escalation, in the rules engine),
`referral:<id>` (identity), `triage:<id>` (proposal), `review:<id>` (decision). A node re-run after a crash therefore repeats no effect.

CEILINGS (§7.3): the run is invoked with `recursion_limit = 40` (LangGraph's own default is 1000). Model-call and tool-call ceilings are enforced where the
calls are made (the gateway and its RunBudget), not here.

LangSmith/LangChain tracing would send graph inputs to a hosted service. `assert_tracing_off` refuses to build the graph if it is enabled in the
environment (CLAUDE.md rule 7: patient data leaves the trust boundary only through a listed, approved call, and this is not one).
"""

import hashlib
import os
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.referral import ReferralExtractor, process_referral, screen_referral
from careflow.retrieval import CardRetriever

RECURSION_LIMIT: Final = 40  # §7.3: 40 steps per run
REVIEW_DECISIONS: Final = ("approve", "edit", "reject")
TRACING_VARIABLES: Final = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING_V2")
_TRUE: Final = {"1", "true", "yes", "on"}


class FrontDoorError(Exception):
    """Fixed words only; never letter text or an extracted value."""


def assert_tracing_off(environ: dict[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    on = [v for v in TRACING_VARIABLES if env.get(v, "").strip().lower() in _TRUE]
    if on:
        raise FrontDoorError("hosted tracing is enabled in the environment; refusing to run a graph that handles patient data")


# ---- the state -----------------------------------------------------------------------------------------------------------------------------


class FrontDoorState(TypedDict, total=False):
    document_id: str
    clinic_id: str
    origin: str
    status: str  # a fixed word; see STATUSES
    reasons: list[str]
    patient_id: str
    candidates: list[dict[str, Any]]  # {"card_id": str, "version": int}: ids only
    abstained: bool
    abstain_reason: str
    proposal_id: str
    hold_task_id: str  # the review task that keeps a held run from being silent
    decision: dict[str, str]  # {"decision": ..., "reviewer_id": ...}


ABSTAIN_REASONS: Final = ("no_calibrated_policy", "no_candidates", "dense_unavailable", "below_threshold", "all_stale")  # a fixed vocabulary only goes into state

FRONT_DESK_HOLDS: Final = ("needs_front_desk",)  # §17.2: a letter that cannot supply a date of birth routes to the front desk, who supply the missing detail
NO_HOLD_NEEDED: Final = ("red_flag_escalated",)  # the rules engine already raised its immediate nurse task


def hold_role_for(status: str) -> str:
    """front_desk for the statuses §17.2 sends to the front desk; a NURSE for everything else, including a status this graph does not know (§16.2: never silent)."""
    return "front_desk" if status in FRONT_DESK_HOLDS else "nurse"


STATUSES: Final = (
    "started", "red_flag_escalated", "held_unassessable", "extraction_unavailable", "needs_front_desk", "identity_resolved", "matched",
    "triage_proposed", "reviewed", "held_for_human",
)


# ---- the ports the nodes use -----------------------------------------------------------------------------------------------------------------


class TextSource(Protocol):
    async def text_for(self, document_id: uuid.UUID) -> str:
        """The letter's text. The real one reads the stored document through `DocumentIntake.read_verified` (so the bytes are re-hashed) and
        the P2-4 extraction. Never cached in graph state."""
        ...


@dataclass(frozen=True)
class TriageProposal:
    proposal_id: uuid.UUID
    task_id: uuid.UUID | None = None


class TriageProposer(Protocol):
    async def propose(
        self, conn: AsyncConnection, *, document_id: uuid.UUID, clinic_id: uuid.UUID, origin: str, patient_id: uuid.UUID | None,
        candidates: list[dict[str, Any]], abstained: bool, idempotency_key: str,
    ) -> TriageProposal:
        """Write the triage proposal and its review task. P2-9 supplies the real writer; it must be idempotent on the key and must never approve
        anything itself."""
        ...


class HoldTaskWriter(Protocol):
    async def raise_hold(
        self, conn: AsyncConnection, *, document_id: uuid.UUID, clinic_id: uuid.UUID, origin: str, assigned_role: str, status: str, reasons: list[str],
        idempotency_key: str,
    ) -> uuid.UUID:
        """Write (idempotently) the review task that stops a held referral from being silent. `reasons` are fixed words, never text."""
        ...


class ReviewRecorder(Protocol):
    async def record(
        self, conn: AsyncConnection, *, proposal_id: uuid.UUID, clinic_id: uuid.UUID, decision: str, reviewer_id: uuid.UUID, idempotency_key: str
    ) -> None: ...


@dataclass
class FrontDoorDeps:
    session: Callable[[], AbstractAsyncContextManager[AsyncConnection]]  # a transaction with the verified claims set
    engine: Any  # RedFlagEngine
    texts: TextSource
    extractor: ReferralExtractor
    ingestor: Any  # PatientIngestionPipeline
    retriever: CardRetriever
    proposer: TriageProposer
    recorder: ReviewRecorder
    holds: HoldTaskWriter
    retrieval_scope: dict[str, str] = field(default_factory=dict)
    profile: str | None = None


# Which deps attribute is which kind of port. The structural test reads every node's source and requires each `deps.<name>` it touches to be listed
# here, and requires the node's declared effects to equal the effects of the side-effect ports it touches.
SIDE_EFFECT_PORTS: Final = {
    "engine": "escalation",  # evaluate_text reads; escalate_red_flag writes a nurse task (idempotent)
    "ingestor": "identity",
    "proposer": "triage_proposal",
    "recorder": "review_decision",
    "holds": "hold_task",
}
MODEL_PORTS: Final = {"extractor": "model", "retriever": "model"}  # the retriever calls the embedding model
NEUTRAL_PORTS: Final = {"session", "texts", "retrieval_scope", "profile"}


@dataclass(frozen=True)
class NodeSpec:
    name: str
    effects: frozenset[str]  # side effects the node performs
    pauses: bool
    models: bool


NODE_SPECS: Final = {
    "screen": NodeSpec("screen", frozenset({"escalation"}), pauses=False, models=False),
    "resolve_identity": NodeSpec("resolve_identity", frozenset({"escalation", "identity"}), pauses=False, models=True),
    "match_protocol": NodeSpec("match_protocol", frozenset({"escalation"}), pauses=False, models=True),
    "hold_for_human": NodeSpec("hold_for_human", frozenset({"hold_task"}), pauses=False, models=False),
    "propose_triage": NodeSpec("propose_triage", frozenset({"triage_proposal"}), pauses=False, models=False),
    "review": NodeSpec("review", frozenset(), pauses=True, models=False),
    "apply_review": NodeSpec("apply_review", frozenset({"review_decision"}), pauses=False, models=False),
}


# ---- the graph -----------------------------------------------------------------------------------------------------------------------------


def _ids(state: FrontDoorState) -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.UUID(state["document_id"]), uuid.UUID(state["clinic_id"])


def thread_id_for(document_id: uuid.UUID) -> str:
    return f"front_door:{document_id}"


def _digest(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def build_front_door(deps: FrontDoorDeps, checkpointer: Any, *, environ: dict[str, str] | None = None):
    """Compile the Front Door graph. `checkpointer` is required (no default): a graph without durable state is not allowed to run (§7.2)."""
    assert_tracing_off(environ)
    if checkpointer is None:
        raise FrontDoorError("a checkpointer is required")
    for variable in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ.setdefault(variable, "false")  # pin it off when unset; an explicit "true" was refused above

    async def screen(state: FrontDoorState) -> FrontDoorState:
        assert_tracing_off()  # also at run time: the environment can change after the graph was built
        document_id, _ = _ids(state)
        async with deps.session() as conn:
            text_ = await deps.texts.text_for(document_id)
            screened = await screen_referral(conn, deps.engine, document_id, text_, profile=deps.profile)  # type: ignore[arg-type]
        if screened.result is not None:
            return {"status": screened.result.status, "reasons": list(screened.result.reasons)}
        return {"status": "started", "reasons": []}

    async def resolve_identity(state: FrontDoorState) -> FrontDoorState:
        assert_tracing_off()  # also at run time: the environment can change after the graph was built
        document_id, _ = _ids(state)
        async with deps.session() as conn:
            text_ = await deps.texts.text_for(document_id)
            result = await process_referral(conn, deps.engine, deps.extractor, deps.ingestor, document_id, text_, profile=deps.profile)  # type: ignore[arg-type]
        update: FrontDoorState = {"status": result.status, "reasons": list(result.reasons)}
        if result.identity is not None:
            update["patient_id"] = str(result.identity.patient_id)
        return update

    async def match_protocol(state: FrontDoorState) -> FrontDoorState:
        assert_tracing_off()  # also at run time: the environment can change after the graph was built
        document_id, _ = _ids(state)
        # Rules first on EVERY path, including a resume that lands here after a crash: the retriever embeds the letter text with a model.
        async with deps.session() as conn:
            text_ = await deps.texts.text_for(document_id)
            screened = await screen_referral(conn, deps.engine, document_id, text_, profile=deps.profile)  # type: ignore[arg-type]
        if screened.clearance is None:
            assert screened.result is not None
            return {"status": screened.result.status, "reasons": list(screened.result.reasons)}
        found = await deps.retriever.retrieve(text_, scope=deps.retrieval_scope)
        return {
            "status": "matched",
            "abstained": found.abstained,
            "abstain_reason": found.reason if found.reason in ABSTAIN_REASONS else "",
            "candidates": [{"card_id": c.card_id, "version": c.version} for c in found.candidates],
        }

    async def hold_for_human(state: FrontDoorState) -> FrontDoorState:
        assert_tracing_off()  # also at run time: the environment can change after the graph was built
        document_id, clinic_id = _ids(state)
        status = state.get("status", "")
        async with deps.session() as conn:
            task_id = await deps.holds.raise_hold(
                conn, document_id=document_id, clinic_id=clinic_id, origin=state["origin"], assigned_role=hold_role_for(status), status=status,
                reasons=[r for r in state.get("reasons", []) if isinstance(r, str)][:20], idempotency_key=f"hold:{document_id}:{status}",
            )
        return {"hold_task_id": str(task_id)}  # the status stays what it was: the reason a person is needed

    async def propose_triage(state: FrontDoorState) -> FrontDoorState:
        assert_tracing_off()  # also at run time: the environment can change after the graph was built
        document_id, clinic_id = _ids(state)
        async with deps.session() as conn:
            proposal = await deps.proposer.propose(
                conn, document_id=document_id, clinic_id=clinic_id, origin=state["origin"],
                patient_id=uuid.UUID(state["patient_id"]) if state.get("patient_id") else None,
                candidates=state.get("candidates", []), abstained=state.get("abstained", True), idempotency_key=f"triage:{document_id}",
            )
        return {"status": "triage_proposed", "proposal_id": str(proposal.proposal_id)}

    def review(state: FrontDoorState) -> FrontDoorState:
        assert_tracing_off()
        # PAUSE. No side effect here: on resume this function runs again from its first line, and it only asks and validates.
        answer = interrupt({"proposal_id": state["proposal_id"], "document_id": state["document_id"], "decisions": list(REVIEW_DECISIONS)})
        if not isinstance(answer, dict) or answer.get("decision") not in REVIEW_DECISIONS or not isinstance(answer.get("reviewer_id"), str):
            raise FrontDoorError("a review needs a decision and a reviewer")
        try:
            uuid.UUID(answer["reviewer_id"])
        except ValueError:
            raise FrontDoorError("a review needs a decision and a reviewer") from None
        return {"decision": {"decision": answer["decision"], "reviewer_id": answer["reviewer_id"]}}

    async def apply_review(state: FrontDoorState) -> FrontDoorState:
        assert_tracing_off()  # also at run time: the environment can change after the graph was built
        document_id, clinic_id = _ids(state)
        async with deps.session() as conn:
            await deps.recorder.record(
                conn, proposal_id=uuid.UUID(state["proposal_id"]), clinic_id=clinic_id, decision=state["decision"]["decision"],
                reviewer_id=uuid.UUID(state["decision"]["reviewer_id"]), idempotency_key=f"review:{document_id}",
            )
        return {"status": "reviewed"}

    def after_screen(state: FrontDoorState) -> str:
        status = state.get("status")
        if status == "started":
            return "resolve_identity"
        return END if status == "red_flag_escalated" else "hold_for_human"  # anything else, known or not, is held for a person

    def after_identity(state: FrontDoorState) -> str:
        status = state.get("status")
        if status == "identity_resolved":
            return "match_protocol"
        return END if status == "red_flag_escalated" else "hold_for_human"

    def after_match(state: FrontDoorState) -> str:
        status = state.get("status")
        if status == "matched":
            return "propose_triage"
        return END if status == "red_flag_escalated" else "hold_for_human"

    functions = {"screen": screen, "resolve_identity": resolve_identity, "match_protocol": match_protocol, "propose_triage": propose_triage,
                 "hold_for_human": hold_for_human, "review": review, "apply_review": apply_review}
    builder = StateGraph(FrontDoorState)
    for name, fn in functions.items():
        builder.add_node(name, fn)
    builder.add_edge(START, "screen")
    builder.add_conditional_edges("screen", after_screen, {"resolve_identity": "resolve_identity", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("resolve_identity", after_identity, {"match_protocol": "match_protocol", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("match_protocol", after_match, {"propose_triage": "propose_triage", "hold_for_human": "hold_for_human", END: END})
    builder.add_edge("hold_for_human", END)
    builder.add_edge("propose_triage", "review")
    builder.add_edge("review", "apply_review")
    builder.add_edge("apply_review", END)
    graph = builder.compile(checkpointer=checkpointer)
    graph.node_functions = functions  # type: ignore[attr-defined]  # for the structural test, which reads the node sources
    graph.router_functions = {"after_screen": after_screen, "after_identity": after_identity, "after_match": after_match}  # type: ignore[attr-defined]
    return graph


def run_config(document_id: uuid.UUID) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id_for(document_id)}, "recursion_limit": RECURSION_LIMIT}


def initial_state(document_id: uuid.UUID, clinic_id: uuid.UUID, origin: str) -> FrontDoorState:
    return {"document_id": str(document_id), "clinic_id": str(clinic_id), "origin": origin, "status": "started", "reasons": []}


# ---- the agent inventory (§1, §4) ---------------------------------------------------------------------------------------------------------------

AGENT_INVENTORY: Final = ("front_door", "scheduling", "care_journey", "visit_readiness")  # the four agents of §1, in build order
BUILT_AGENTS: Final = ("front_door",)


def feature_flags(environ: dict[str, str] | None = None) -> dict[str, bool]:
    env = os.environ if environ is None else environ
    return {name: env.get(name, "").strip().lower() in _TRUE for name in ("FEATURE_CARE_JOURNEY", "FEATURE_VISIT_READINESS")}


def agents_to_build(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """The agent graphs this process may build. A flag that is ON for an agent whose graph does not exist yet is an error, never a silent no-op."""
    flags = feature_flags(environ)
    asked = [name for name, on in (("care_journey", flags["FEATURE_CARE_JOURNEY"]), ("visit_readiness", flags["FEATURE_VISIT_READINESS"])) if on]
    if asked:
        raise FrontDoorError(f"a feature flag is on for an agent that is not built yet: {', '.join(asked)}")
    return BUILT_AGENTS

