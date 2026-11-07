"""The Visit Readiness agent (BUILD-SPEC.md 22, 12.1, 11.4, 5.2; Appendix D Phase 7; docs/design/phase7-visit-readiness.md). Behind FEATURE_VISIT_READINESS, off by default, built after Care Journey.
MECHANISM ONLY. One agent, four triggers; the graph's entry router picks the path from the trigger.

  brief      build_brief                                     decorative: a failure leaves the clinician with the raw chart; nothing is blocked
  intake     next_item -> ask_item -> await_reply (PAUSE) -> screen_reply -> classify_intent -> resolve_fact | hold_for_human | barrier   (loops until the gap is closed, then the readiness checklist)
  readiness  the same loop over the visit type's checklist: classify_readiness -> record_readiness -> escalate_nurse on ANY answer that is not `confirmed`
  barrier    classify_barrier -> handoff_rebooking (scheduling conflict) | hold_for_human (front desk, every other barrier or an unknown class)

THE RULES THIS FILE HOLDS TO
  * NO QUESTION IS WRITTEN HERE. A question is a protocol card's `intake_question` for a declared fact, or a visit type's `readiness_checklist` item: clinician-authored, versioned, sent as the card's EXACT
    text (a governed template, so it is not a non-template outbound message, 12.1). The code selects; it never words (CLAUDE.md rule 1). A fact with no question is NOT asked and NOT guessed: it is
    reported (`unaskable`) and becomes part of the incomplete-intake flag for front desk.
  * THE SELECTION IS THE GAP, computed by code (`fact_gap`): a declared fact is asked only if it is missing or not usable by a rule (unverified is unknown, CLAUDE.md rule 3). A detailed referral gives a
    short conversation, a sparse one a longer one. A model never selects.
  * RULES BEFORE ANY MODEL ON EVERY REPLY (CLAUDE.md rule 2, 5.2): `screen_reply` runs before `classify_intent`/`classify_readiness`/`resolve_fact`; a red flag volunteered mid-conversation, in an answer to an
    unrelated question, ends the run in the fixed safety response and the nurse task. The model's opinion can never undo a flag.
  * A CLINICAL QUESTION is never answered: it routes to a human (11.4). An unclear reply is re-asked ONCE, then goes to front desk.
  * READINESS IS LOCKED TOWARD A NURSE IN EVERY MODE: any item that is `not_done` or `unclear`, or that has no answer at all, raises a nurse task. The autonomy mode is not consulted for it (12.1).
  * AN ANSWER BECOMES A FACT only through grounding (11.2): `resolve_fact` records it only if the grounded fact is usable; otherwise it is asked about no further and joins the incomplete flag.
  * ANYTHING INCOMPLETE WHEN THE WINDOW CLOSES is flagged for front desk (`finish_visit`), never silently dropped and never guessed.

WHAT IS IN THE STATE: ids, counters and closed words. NO reply, question text, fact value or name. Questions and values live in the stores the ports read.
"""

import os
import re
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol, TypedDict

from langgraph._internal._constants import CONFIG_KEY_DURABILITY
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.autonomy import Policy
from careflow.front_door import (
    FrontDoorError,
    NodeSpec,
    assert_tracing_off,
    feature_flags,
)
from careflow.grounding import GroundedFact

RECURSION_LIMIT: Final = 120
TRIGGERS: Final = ("brief", "intake", "barrier")
INTENTS: Final = ("answers", "clinical_question", "barrier", "unclear")  # closed set (22.2)
READINESS_CLASSES: Final = ("confirmed", "not_done", "unclear")  # closed set (22.3)
BARRIERS: Final = ("transport", "cost", "childcare", "forgot", "scheduling_conflict", "other")  # closed set (22.4)
REPLY_OUTCOMES: Final = ("replied", "no_reply")
MAX_REASKS: Final = 1  # an unclear reply is asked again once, then goes to front desk
_ID = re.compile(r"^[a-z][a-z0-9_]{0,59}$")


# ---- the card extension (22.2, 22.3) -------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IntakeCard:
    """What the agent needs from a protocol card: its declared facts (10.2) and, per fact, the clinician's own question (22.2). A fact without a question is `unaskable`."""

    card_id: str
    version: int
    facts: tuple[str, ...]
    intake_questions: Mapping[str, str] = field(default_factory=dict)


def card_problems(card: IntakeCard) -> list[str]:
    """Structure only (never wording): fact names well formed and unique, every question belongs to a declared fact and is non-empty text. A fact with no question is NOT a problem here; it is `unaskable`."""
    problems: list[str] = []
    if len(set(card.facts)) != len(card.facts) or any(not _ID.match(f) for f in card.facts):
        problems.append("fact_names")
    if any(f not in card.facts for f in card.intake_questions):
        problems.append("question_for_undeclared_fact")
    if any(not isinstance(q, str) or not q.strip() for q in card.intake_questions.values()):
        problems.append("empty_question")
    return problems


@dataclass(frozen=True)
class ChecklistItem:
    item_id: str
    text: str  # the clinician's own wording, sent as the exact text


@dataclass(frozen=True)
class ReadinessChecklist:
    """A visit type's `readiness_checklist` (22.3): clinician-authored, versioned, NEVER derived from a gap computation (every item is always confirmed, whatever is on file)."""

    visit_type_id: str
    version: int
    items: tuple[ChecklistItem, ...]


def checklist_problems(checklist: ReadinessChecklist) -> list[str]:
    """Structure only. An empty checklist is a problem: an empty list must never read as 'nothing to confirm' for a procedure (see `readiness_outcome`)."""
    ids = [i.item_id for i in checklist.items]
    problems: list[str] = []
    if not ids:
        problems.append("empty_checklist")
    if len(set(ids)) != len(ids) or any(not _ID.match(i) for i in ids):
        problems.append("item_ids")
    if any(not i.text.strip() for i in checklist.items):
        problems.append("empty_item_text")
    return problems


def fact_gap(card: IntakeCard, known: Iterable[GroundedFact]) -> tuple[list[str], list[str]]:
    """(to_ask, unaskable). A declared fact needs asking unless EVERY entry known for it is usable by a rule (a fact with two conflicting entries is usable only if both are, like grounding.needs_review).
    A fact that needs asking but has no clinician question is unaskable. Order is the card's declaration order, so the conversation is reproducible."""
    by_name: dict[str, list[GroundedFact]] = {}
    for f in known:
        by_name.setdefault(f.field_name, []).append(f)
    needed = [f for f in card.facts if f not in by_name or not all(g.usable_by_rules for g in by_name[f])]
    return [f for f in needed if card.intake_questions.get(f, "").strip()], [f for f in needed if not card.intake_questions.get(f, "").strip()]


# ---- readiness (22.3) ---------------------------------------------------------------------------------------------------------------------------------------------


def readiness_outcome(expected_items: Sequence[str], answers: Mapping[str, str]) -> tuple[Literal["proceed", "escalate_nurse"], list[str]]:
    """`proceed` only when EVERY expected item is `confirmed`. An item with no answer, an `unclear` one, a `not_done` one, or an answer outside the closed set counts against it. The autonomy mode is not
    an input: this is locked toward a nurse in every mode (12.1)."""
    bad = [item for item in expected_items if answers.get(item) != "confirmed"]
    return ("proceed", []) if expected_items and not bad else ("escalate_nurse", bad or list(expected_items))


# ---- barriers (22.4) ----------------------------------------------------------------------------------------------------------------------------------------------


def barrier_route(barrier: object, policy: Policy) -> Literal["rebook", "rebook_needs_confirmation", "front_desk"]:
    """A scheduling conflict hands off to the Scheduling agent: automatically where the matrix says `barrier_resolvable_by_rebooking` is automatic, with a person's confirmation where it says so (shadow).
    Every other barrier, and anything outside the closed set, goes to front desk in every mode."""
    if barrier != "scheduling_conflict":
        return "front_desk"
    return "rebook" if policy.may_run_without_a_human_first("barrier_resolvable_by_rebooking") else "rebook_needs_confirmation"


# ---- the pre-visit brief (22.1) -----------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BriefLine:
    field_name: str
    value: str
    status: Literal["verified", "unverified", "model_generated"]


def compose_brief(facts: Iterable[GroundedFact]) -> list[BriefLine]:
    """A structured brief for the CLINICIAN, never the patient: each fact carries its grounding status. A verified tier-1/2 fact is `verified`; a free-text summary is `model_generated` and carries no
    clinical weight; everything else is shown `unverified`. Nothing is dropped and nothing is upgraded."""
    lines: list[BriefLine] = []
    for f in facts:
        if f.usable_by_rules:
            status: Literal["verified", "unverified", "model_generated"] = "verified"
        elif f.status.value == "model_generated":
            status = "model_generated"
        else:
            status = "unverified"
        lines.append(BriefLine(f.field_name, f.value, status))
    return lines


# ---- the state and the ports ----------------------------------------------------------------------------------------------------------------------------------------


class VisitReadinessState(TypedDict, total=False):
    clinic_id: str
    origin: str
    visit_id: str
    patient_id: str
    trigger: str
    phase: str  # intake | readiness
    intake_items: list[str]  # fact names still to ask (declaration order)
    readiness_items: list[str]  # checklist item ids
    unaskable: list[str]
    index: int
    reasks: int
    current_item: str
    message_id: str
    intent: str
    readiness_class: str
    readiness_verdict: str
    barrier_route: str
    readiness_answers: dict[str, str]
    unresolved: list[str]
    barrier: str
    status: str
    task_id: str
    reasons: list[str]


class GapPlanner(Protocol):
    async def plan(self, conn: AsyncConnection, *, visit_id: uuid.UUID, clinic_id: uuid.UUID) -> dict[str, list[str]]:
        """{"intake": [...], "unaskable": [...], "readiness": [...]} computed by `fact_gap` from the card and the facts known for this patient, plus the visit type's checklist ids."""
        ...


class QuestionSender(Protocol):
    async def ask(self, conn: AsyncConnection, *, visit_id: uuid.UUID, phase: str, item: str, idempotency_key: str) -> uuid.UUID:
        """Sends the clinician's EXACT question text for this item as a governed template and returns the stored message id. It never composes text."""
        ...


class ReplyScreener(Protocol):
    async def screen(self, conn: AsyncConnection, *, message_id: uuid.UUID) -> str:
        """`clear`, `red_flag_escalated` or `held_unassessable`. The rules only; no model. A flag raises its own immediate nurse task and the fixed safety response."""
        ...


class IntentClassifier(Protocol):
    async def classify(self, conn: AsyncConnection, *, message_id: uuid.UUID) -> str: ...  # a model call (decision tier): one of INTENTS


class FactResolver(Protocol):
    async def resolve(self, conn: AsyncConnection, *, visit_id: uuid.UUID, message_id: uuid.UUID, fact: str) -> bool:
        """A model extracts the answer; it is GROUNDED (11.2). True only if the grounded fact is usable by a rule."""
        ...


class FactWriter(Protocol):
    async def record(self, conn: AsyncConnection, *, visit_id: uuid.UUID, fact: str, message_id: uuid.UUID, idempotency_key: str) -> None: ...


class ReadinessClassifier(Protocol):
    async def classify(self, conn: AsyncConnection, *, message_id: uuid.UUID, item: str) -> str: ...  # a model call: one of READINESS_CLASSES


class ReadinessRecorder(Protocol):
    async def record(self, conn: AsyncConnection, *, visit_id: uuid.UUID, item: str, answer: str, message_id: uuid.UUID, idempotency_key: str) -> None: ...


class BarrierClassifier(Protocol):
    async def classify(self, conn: AsyncConnection, *, visit_id: uuid.UUID) -> str: ...  # a model call: one of BARRIERS


class BarrierRecorder(Protocol):
    async def record(self, conn: AsyncConnection, *, visit_id: uuid.UUID, barrier: str, idempotency_key: str) -> None:
        """Records the barrier type so recurring barriers become visible (22.4); feeds no_show_rate and followups_closed_vs_lost."""
        ...


class Rebooker(Protocol):
    async def hand_off(self, conn: AsyncConnection, *, visit_id: uuid.UUID, needs_confirmation: bool, idempotency_key: str) -> None:
        """Hands the visit to the existing Scheduling agent's flow. This agent books nothing itself."""
        ...


class BriefMaker(Protocol):
    async def generate(self, conn: AsyncConnection, *, visit_id: uuid.UUID, clinic_id: uuid.UUID) -> list[GroundedFact]: ...  # the model drafts narrative; facts come back with their grounding

    async def store(self, conn: AsyncConnection, *, visit_id: uuid.UUID, lines: list[BriefLine], idempotency_key: str) -> None: ...


class PeopleTasks(Protocol):
    async def raise_task(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, visit_id: uuid.UUID, reason: str, reasons: list[str], role: str, idempotency_key: str
    ) -> uuid.UUID:
        """`reason` is a closed word: readiness_escalation, clinical_question, unclear_reply, incomplete_intake, barrier, needs_person. `role` is `nurse` or `front_desk`."""
        ...


@dataclass
class VisitReadinessDeps:
    session: Callable[[], AbstractAsyncContextManager[AsyncConnection]]
    planner: GapPlanner
    sender: QuestionSender
    screener: ReplyScreener
    intents: IntentClassifier
    resolver: FactResolver
    facts: FactWriter
    readiness_classifier: ReadinessClassifier
    readiness_recorder: ReadinessRecorder
    barrier_classifier: BarrierClassifier
    barriers: BarrierRecorder
    rebooker: Rebooker
    briefs: BriefMaker
    tasks: PeopleTasks
    policy: Policy


SIDE_EFFECT_PORTS: Final = {
    "sender": "patient_message", "screener": "escalation", "facts": "fact_write", "readiness_recorder": "readiness_record", "barriers": "barrier_record", "rebooker": "rebook_handoff",
    "tasks": "review_task",
}
MODEL_PORTS: Final = {"intents": "model", "resolver": "model", "readiness_classifier": "model", "barrier_classifier": "model", "briefs": "model"}
NEUTRAL_PORTS: Final = {"session", "planner", "policy"}

NODE_SPECS: Final = {
    "build_brief": NodeSpec("build_brief", frozenset(), pauses=False, models=True),
    "next_item": NodeSpec("next_item", frozenset(), pauses=False, models=False),
    "ask_item": NodeSpec("ask_item", frozenset({"patient_message"}), pauses=False, models=False),
    "await_reply": NodeSpec("await_reply", frozenset(), pauses=True, models=False),
    "screen_reply": NodeSpec("screen_reply", frozenset({"escalation"}), pauses=False, models=False),
    "classify_intent": NodeSpec("classify_intent", frozenset(), pauses=False, models=True),
    "resolve_fact": NodeSpec("resolve_fact", frozenset({"fact_write"}), pauses=False, models=True),
    "classify_readiness": NodeSpec("classify_readiness", frozenset({"readiness_record"}), pauses=False, models=True),
    "escalate_nurse": NodeSpec("escalate_nurse", frozenset({"review_task"}), pauses=False, models=False),
    "classify_barrier": NodeSpec("classify_barrier", frozenset({"barrier_record"}), pauses=False, models=True),
    "handoff_rebooking": NodeSpec("handoff_rebooking", frozenset({"rebook_handoff"}), pauses=False, models=False),
    "finish_visit": NodeSpec("finish_visit", frozenset({"review_task"}), pauses=False, models=False),
    "hold_for_human": NodeSpec("hold_for_human", frozenset({"review_task"}), pauses=False, models=False),
    "reask": NodeSpec("reask", frozenset(), pauses=False, models=False),
}


def _ids(state: VisitReadinessState) -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.UUID(state["clinic_id"]), uuid.UUID(state["visit_id"])


def thread_id_for(visit_id: uuid.UUID, trigger: str) -> str:
    return f"visit_readiness:{visit_id}:{trigger}"


def initial_state(clinic_id: uuid.UUID, visit_id: uuid.UUID, patient_id: uuid.UUID, origin: str, trigger: str) -> VisitReadinessState:
    if trigger not in TRIGGERS:
        raise FrontDoorError("unknown trigger")
    return {
        "clinic_id": str(clinic_id), "visit_id": str(visit_id), "patient_id": str(patient_id), "origin": origin, "trigger": trigger, "status": "started", "reasons": [], "index": 0, "reasks": 0,
        "readiness_answers": {}, "unresolved": [],
    }


def run_config(visit_id: uuid.UUID, trigger: str, clinic_id: uuid.UUID | None = None, origin: str | None = None) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id_for(visit_id, trigger)}
    if clinic_id is not None and origin is not None:
        configurable.update({"clinic_id": str(clinic_id), "origin": origin, CONFIG_KEY_DURABILITY: "sync"})
    return {"configurable": configurable, "recursion_limit": RECURSION_LIMIT}


def visit_readiness_enabled(environ: dict[str, str] | None = None) -> bool:
    return feature_flags(environ)["FEATURE_VISIT_READINESS"]


def build_visit_readiness_graph(deps: VisitReadinessDeps, checkpointer: Any, *, environ: dict[str, str] | None = None):
    """Compile the graph. REFUSED while FEATURE_VISIT_READINESS is off; a checkpointer is required; hosted tracing is refused (replies would reach a third party)."""
    if not visit_readiness_enabled(environ if environ is not None else dict(os.environ)):
        raise FrontDoorError("FEATURE_VISIT_READINESS is off: the Visit Readiness graph is not built")
    assert_tracing_off(environ)
    if checkpointer is None:
        raise FrontDoorError("a checkpointer is required")
    for variable in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ.setdefault(variable, "false")

    async def build_brief(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        clinic_id, visit_id = _ids(state)
        try:
            async with deps.session() as conn:
                lines = compose_brief(await deps.briefs.generate(conn, visit_id=visit_id, clinic_id=clinic_id))
        except Exception:  # noqa: BLE001 - decorative (22.1): a failed brief blocks nothing; the clinician reads the raw chart
            return {"status": "brief_unavailable"}
        async with deps.session() as conn:
            await deps.briefs.store(conn, visit_id=visit_id, lines=lines, idempotency_key=f"vr:{visit_id}:brief")
        return {"status": "brief_stored"}

    async def next_item(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        clinic_id, visit_id = _ids(state)
        if "phase" not in state:  # the first time through: the gap is computed ONCE by code and fixed for the conversation
            async with deps.session() as conn:
                plan = await deps.planner.plan(conn, visit_id=visit_id, clinic_id=clinic_id)
            intake, readiness, unaskable = (list(plan.get(k, [])) for k in ("intake", "readiness", "unaskable"))
            first = "intake" if intake else "readiness"
            queue = intake if intake else readiness
            return {"phase": first, "intake_items": intake, "readiness_items": readiness, "unaskable": unaskable, "index": 0, "reasks": 0, **({"current_item": queue[0], "status": "next"} if queue else {"status": "nothing_to_ask"})}
        phase, index = state["phase"], state.get("index", 0)
        queue = state["intake_items"] if phase == "intake" else state["readiness_items"]
        if index < len(queue):
            return {"current_item": queue[index], "status": "next", "reasks": 0}
        if phase == "intake" and state.get("readiness_items"):
            return {"phase": "readiness", "index": 0, "reasks": 0, "current_item": state["readiness_items"][0], "status": "next"}
        return {"status": "nothing_to_ask"}

    async def ask_item(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        _, visit_id = _ids(state)
        async with deps.session() as conn:
            message_id = await deps.sender.ask(
                conn, visit_id=visit_id, phase=state["phase"], item=state["current_item"], idempotency_key=f"vr:{visit_id}:{state['phase']}:{state['current_item']}:ask:{state.get('reasks', 0)}"
            )
        return {"message_id": str(message_id), "status": "asked"}

    def await_reply(state: VisitReadinessState) -> VisitReadinessState:
        # PAUSE until the patient's reply is stored (or none came before the window closed). No side effect and no port.
        answer = interrupt({"visit_id": state["visit_id"], "waiting_for": "reply", "item": state["current_item"], "outcomes": list(REPLY_OUTCOMES)})
        if not isinstance(answer, dict) or answer.get("outcome") not in REPLY_OUTCOMES:
            raise FrontDoorError("a reply entry needs one of the listed outcomes")
        if answer["outcome"] == "no_reply":
            return {"status": "no_reply"}
        try:
            uuid.UUID(str(answer.get("message_id")))
        except ValueError:
            raise FrontDoorError("a reply needs the id of the stored message") from None
        return {"message_id": str(answer["message_id"]), "status": "reply_in"}

    async def screen_reply(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        # The deterministic rules read EVERY reply first, before any model, whatever question was asked (CLAUDE.md rule 2): a danger phrase volunteered in an unrelated answer is caught here.
        async with deps.session() as conn:
            outcome = await deps.screener.screen(conn, message_id=uuid.UUID(state["message_id"]))
        if outcome == "clear":
            return {"status": "screened"}
        if outcome == "red_flag_escalated":
            return {"status": "red_flag_escalated"}
        return {"status": "held_unassessable", "reasons": ["reply_unassessable"]}

    async def classify_intent(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        async with deps.session() as conn:
            intent = await deps.intents.classify(conn, message_id=uuid.UUID(state["message_id"]))
        return {"status": "classified", "intent": intent if intent in INTENTS else "unclear"}  # outside the closed set is unclear: unknown is not a value

    async def resolve_fact(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        _, visit_id = _ids(state)
        message_id = uuid.UUID(state["message_id"])
        async with deps.session() as conn:
            grounded = await deps.resolver.resolve(conn, visit_id=visit_id, message_id=message_id, fact=state["current_item"])
            if grounded:
                await deps.facts.record(conn, visit_id=visit_id, fact=state["current_item"], message_id=message_id, idempotency_key=f"vr:{visit_id}:fact:{state['current_item']}")
        if grounded:
            return {"status": "fact_written", "index": state.get("index", 0) + 1}
        return {"status": "fact_not_grounded", "unresolved": [*state.get("unresolved", []), state["current_item"]], "index": state.get("index", 0) + 1}

    async def classify_readiness(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        _, visit_id = _ids(state)
        message_id = uuid.UUID(state["message_id"])
        async with deps.session() as conn:
            answer = await deps.readiness_classifier.classify(conn, message_id=message_id, item=state["current_item"])
            answer = answer if answer in READINESS_CLASSES else "unclear"
            await deps.readiness_recorder.record(conn, visit_id=visit_id, item=state["current_item"], answer=answer, message_id=message_id, idempotency_key=f"vr:{visit_id}:ready:{state['current_item']}:{state.get('reasks', 0)}")
        answers = {**state.get("readiness_answers", {}), state["current_item"]: answer}
        index = state.get("index", 0) + 1
        verdict = readiness_outcome(state.get("readiness_items", []), answers)[0] if index >= len(state.get("readiness_items", [])) else "more"  # decided by code, here, so the router only reads the state
        return {"status": "readiness_recorded", "readiness_answers": answers, "index": index, "readiness_verdict": verdict}

    async def escalate_nurse(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        clinic_id, visit_id = _ids(state)
        _, bad = readiness_outcome(state.get("readiness_items", []), state.get("readiness_answers", {}))
        async with deps.session() as conn:
            task_id = await deps.tasks.raise_task(
                conn, clinic_id=clinic_id, origin=state["origin"], visit_id=visit_id, reason="readiness_escalation", reasons=[f"item:{i}" for i in bad][:20], role="nurse",
                idempotency_key=f"vr:{visit_id}:readiness-escalation",
            )
        return {"status": "nurse_escalated", "task_id": str(task_id)}

    async def classify_barrier(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        _, visit_id = _ids(state)
        async with deps.session() as conn:
            barrier = await deps.barrier_classifier.classify(conn, visit_id=visit_id)
            if barrier in BARRIERS:
                await deps.barriers.record(conn, visit_id=visit_id, barrier=barrier, idempotency_key=f"vr:{visit_id}:barrier")
        if barrier not in BARRIERS:
            return {"status": "barrier_unknown", "reasons": ["barrier_unclassified"]}
        return {"status": "barrier_classified", "barrier": barrier, "barrier_route": barrier_route(barrier, deps.policy)}

    async def handoff_rebooking(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        _, visit_id = _ids(state)
        needs_confirmation = state.get("barrier_route") == "rebook_needs_confirmation"
        async with deps.session() as conn:
            await deps.rebooker.hand_off(conn, visit_id=visit_id, needs_confirmation=needs_confirmation, idempotency_key=f"vr:{visit_id}:rebook")
        return {"status": "rebooking_handed_off"}

    async def finish_visit(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        clinic_id, visit_id = _ids(state)
        leftover = [*state.get("unaskable", []), *state.get("unresolved", [])]
        async with deps.session() as conn:
            task_id = await deps.tasks.raise_task(
                conn, clinic_id=clinic_id, origin=state["origin"], visit_id=visit_id, reason="incomplete_intake", reasons=[f"fact:{f}" for f in leftover][:20], role="front_desk",
                idempotency_key=f"vr:{visit_id}:incomplete",
            )
        return {"status": "incomplete_flagged", "task_id": str(task_id)}

    async def hold_for_human(state: VisitReadinessState) -> VisitReadinessState:
        assert_tracing_off()
        clinic_id, visit_id = _ids(state)
        status, intent = state.get("status", ""), state.get("intent", "")
        reason = "clinical_question" if intent == "clinical_question" else "barrier" if intent == "barrier" or status == "barrier_classified" else "unclear_reply" if intent == "unclear" else "needs_person"
        async with deps.session() as conn:
            task_id = await deps.tasks.raise_task(
                conn, clinic_id=clinic_id, origin=state["origin"], visit_id=visit_id, reason=reason, reasons=[r for r in state.get("reasons", []) if isinstance(r, str)][:20], role="front_desk",
                idempotency_key=f"vr:{visit_id}:hold:{state.get('current_item', 'visit')}:{reason}",
            )
        return {"task_id": str(task_id)}

    # ---- routing: read the state only; no model chooses a path ------------------------------------------------------------------------------------------------

    def entry(state: VisitReadinessState) -> str:
        trigger = state.get("trigger")
        return "build_brief" if trigger == "brief" else "classify_barrier" if trigger == "barrier" else "next_item" if trigger == "intake" else "hold_for_human"

    def after_next_item(state: VisitReadinessState) -> str:
        status = state.get("status")
        if status == "next":
            return "ask_item"
        if status == "nothing_to_ask":
            return "finish_visit" if state.get("unaskable") or state.get("unresolved") else END
        return "hold_for_human"

    def after_ask(state: VisitReadinessState) -> str:
        return "await_reply" if state.get("status") == "asked" else "hold_for_human"

    def after_await(state: VisitReadinessState) -> str:
        status = state.get("status")
        return "screen_reply" if status == "reply_in" else "hold_for_human"  # no reply is never treated as an answer: a person follows up

    def after_screen(state: VisitReadinessState) -> str:
        status = state.get("status")
        if status == "red_flag_escalated":
            return END
        if status != "screened":
            return "hold_for_human"
        return "classify_intent" if state.get("phase") == "intake" else "classify_readiness"

    def after_intent(state: VisitReadinessState) -> str:
        if state.get("status") != "classified":
            return "hold_for_human"
        intent = state.get("intent")
        if intent == "answers":
            return "resolve_fact"
        if intent == "barrier":
            return "classify_barrier"
        if intent == "unclear" and state.get("reasks", 0) < MAX_REASKS:
            return "reask"
        return "hold_for_human"  # a clinical question or a second unclear reply: a person

    def after_resolve(state: VisitReadinessState) -> str:
        return "next_item" if state.get("status") in {"fact_written", "fact_not_grounded"} else "hold_for_human"

    def after_readiness(state: VisitReadinessState) -> str:
        if state.get("status") != "readiness_recorded":
            return "hold_for_human"
        verdict = state.get("readiness_verdict")
        if verdict == "more":
            return "next_item"
        if verdict == "escalate_nurse":
            return "escalate_nurse"
        return "finish_visit" if state.get("unaskable") or state.get("unresolved") else END

    def after_barrier(state: VisitReadinessState) -> str:
        if state.get("status") != "barrier_classified":
            return "hold_for_human"
        return "handoff_rebooking" if state.get("barrier_route") in {"rebook", "rebook_needs_confirmation"} else "hold_for_human"

    def reask(state: VisitReadinessState) -> VisitReadinessState:
        return {"reasks": state.get("reasks", 0) + 1, "status": "next"}

    def after_escalate(state: VisitReadinessState) -> str:
        if state.get("status") != "nurse_escalated":
            return "hold_for_human"
        return "finish_visit" if state.get("unaskable") or state.get("unresolved") else END

    functions = {
        "build_brief": build_brief, "next_item": next_item, "ask_item": ask_item, "await_reply": await_reply, "screen_reply": screen_reply, "classify_intent": classify_intent,
        "resolve_fact": resolve_fact, "classify_readiness": classify_readiness, "escalate_nurse": escalate_nurse, "classify_barrier": classify_barrier, "handoff_rebooking": handoff_rebooking,
        "finish_visit": finish_visit, "hold_for_human": hold_for_human,
    }
    builder = StateGraph(VisitReadinessState)
    for name, fn in functions.items():
        builder.add_node(name, fn)
    builder.add_node("reask", reask)
    builder.add_conditional_edges(START, entry, {"build_brief": "build_brief", "classify_barrier": "classify_barrier", "next_item": "next_item", "hold_for_human": "hold_for_human"})
    builder.add_edge("build_brief", END)
    builder.add_conditional_edges("next_item", after_next_item, {"ask_item": "ask_item", "finish_visit": "finish_visit", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("ask_item", after_ask, {"await_reply": "await_reply", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("await_reply", after_await, {"screen_reply": "screen_reply", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges(
        "screen_reply", after_screen, {"classify_intent": "classify_intent", "classify_readiness": "classify_readiness", "hold_for_human": "hold_for_human", END: END}
    )
    builder.add_conditional_edges("classify_intent", after_intent, {"resolve_fact": "resolve_fact", "reask": "reask", "classify_barrier": "classify_barrier", "hold_for_human": "hold_for_human"})
    builder.add_edge("reask", "ask_item")
    builder.add_conditional_edges("resolve_fact", after_resolve, {"next_item": "next_item", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("classify_readiness", after_readiness, {"next_item": "next_item", "escalate_nurse": "escalate_nurse", "finish_visit": "finish_visit", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("escalate_nurse", after_escalate, {"finish_visit": "finish_visit", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("classify_barrier", after_barrier, {"handoff_rebooking": "handoff_rebooking", "hold_for_human": "hold_for_human"})
    builder.add_edge("handoff_rebooking", END)
    builder.add_edge("finish_visit", END)
    builder.add_edge("hold_for_human", END)
    graph = builder.compile(checkpointer=checkpointer)
    graph.node_functions = {**functions, "reask": reask}  # type: ignore[attr-defined]
    graph.router_functions = {  # type: ignore[attr-defined]
        "entry": entry, "after_next_item": after_next_item, "after_ask": after_ask, "after_await": after_await, "after_screen": after_screen, "after_intent": after_intent,
        "after_resolve": after_resolve, "after_readiness": after_readiness, "after_barrier": after_barrier, "after_escalate": after_escalate,
    }
    return graph
