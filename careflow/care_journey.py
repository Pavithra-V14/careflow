"""The Care Journey agent (BUILD-SPEC.md 1, 4, 7.1, 7.2, 12.1; Appendix D Phase 6; docs/design/phase6-care-journey.md). Behind FEATURE_CARE_JOURNEY, off by default. MECHANISM ONLY.

A doctor's free-text care plan becomes a task graph that WAITS (days, weeks) for results and closes conditional loops. The one place a model proposes anything is the plan parse; a DOCTOR CONFIRMS BEFORE
ANYTHING EXECUTES (7.1), and every conditional outcome that is not clearly met or clearly not met goes to a doctor.

    screen_plan -> parse_plan -> request_confirmation -> await_confirmation (PAUSE) -> activate_plan -> next_wait -> await_result (PAUSE) -> claim_result -> evaluate_result
         -> propose_referral | close_step | hold_for_human -> advance -> next_wait ... -> complete_plan                      (a rejected plan -> reject_plan)

  * `screen_plan`          the deterministic red-flag rules read the plan text FIRST, before any model, on every path (CLAUDE.md rule 2). A flag or an unassessable text raises the nurse task and ENDS the run.
  * `parse_plan`           the model (`llm_large`) proposes steps; deterministic validation (`parse_proposal`) accepts only a closed schema. A malformed proposal is never repaired: it goes to a doctor.
  * `request_confirmation` raises the doctor's confirmation task. The side effect sits IMMEDIATELY before the pause (7.2).
  * `await_confirmation`   PAUSES. The confirmation is unconditional in every autonomy mode: care-plan tasks are `human` (careflow/autonomy.py) and this graph never consults a mode to skip it.
  * `activate_plan`        writes the confirmed plan's tasks and REGISTERS A WAIT per result step (idempotent keys).
  * `await_result`         PAUSES until a result is routed here. WAIT-CLAIM BY PREFIX: a wait names a result-code prefix; `match_wait` picks, among the patient's OPEN waits, the one with the LONGEST matching
                           prefix; two with the same longest prefix are AMBIGUOUS and go to a doctor; none matches means the result is not this plan's. `claim_result` then lets exactly ONE result claim a wait
                           (first claim wins; a replay of the same result is a no-op; a second result for a claimed wait is ignored).
  * `evaluate_result`      the condition (`field op threshold`, written by the doctor and confirmed) is evaluated by `evaluate`. A MISSING, NON-NUMERIC OR UNVERIFIED value is `unknown`, never `not_met`
                           (CLAUDE.md rule 3); `unknown` goes to a doctor. Only a verified value that fails the condition CLOSES the step, with the result id as its evidence.
  * `propose_referral`     a met condition raises a DOCTOR TASK proposing the referral. Nothing leaves the clinic: records release is `human` in every mode (autonomy.py).
  * NO RUN ENDS SILENTLY: every route that is not a clean close reaches `hold_for_human`.

WHAT IS IN THE STATE: ids, counters and closed words. NO plan text, label, value or name: the plan and its steps live in the store, and the graph names them by id. No clinical content is in this file: the
steps, prefixes, fields, operators and thresholds all come from the doctor's own plan and are confirmed by the doctor.
"""

import math
import os
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, TypedDict

from langgraph._internal._constants import CONFIG_KEY_DURABILITY
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.front_door import (
    FrontDoorError,
    NodeSpec,
    assert_tracing_off,
    feature_flags,
)

RECURSION_LIMIT: Final = 80
MAX_STEPS: Final = 20  # an engineering ceiling on a proposal, not a clinical limit
STEP_KINDS: Final = ("task", "wait_result")
OPS: Final = ("lt", "le", "gt", "ge", "eq")
ON_MET: Final = ("propose_referral",)
CONFIRM_ACTIONS: Final = ("approve", "reject")
RESULT_KINDS: Final = ("result", "overdue")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
_STEP_KEYS = {"key", "kind", "label", "result_prefix", "condition", "on_met"}
_CONDITION_KEYS = {"field", "op", "threshold"}

Outcome = Literal["met", "not_met", "unknown"]


@dataclass(frozen=True)
class Condition:
    field: str
    op: str
    threshold: float


@dataclass(frozen=True)
class Step:
    key: str
    kind: str
    label: str
    result_prefix: str | None = None
    condition: Condition | None = None
    on_met: str | None = None


@dataclass(frozen=True)
class Result:
    """A result as the care journey sees it. `values` holds only what the source verified; an unverified result satisfies no condition."""

    result_id: str
    patient_id: str
    code: str
    values: Mapping[str, float | None]
    verified: bool


@dataclass(frozen=True)
class Wait:
    wait_key: str
    plan_id: str
    step_key: str
    patient_id: str
    prefix: str
    open: bool = True


def _number(x: object) -> float | None:
    return float(x) if isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x) else None


# ---- the plan proposal: a closed schema, validated by code ---------------------------------------------------------------------------------------------------------


def parse_proposal(raw: object) -> tuple[list[Step], list[str]]:
    """(steps, problems). ANY problem means NO steps: a proposal is accepted whole or not at all, and never repaired. Problems are closed words, never the proposal's own text."""
    if not isinstance(raw, dict) or set(raw) != {"steps"} or not isinstance(raw["steps"], list):
        return [], ["proposal_shape"]
    items = raw["steps"]
    if not 1 <= len(items) <= MAX_STEPS:
        return [], ["step_count"]
    steps: list[Step] = []
    problems: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not set(item) <= _STEP_KEYS or not {"key", "kind", "label"} <= set(item):
            problems.append("step_shape")
            continue
        key, kind, label = item["key"], item["kind"], item["label"]
        if not (isinstance(key, str) and _KEY.match(key)) or any(s.key == key for s in steps):
            problems.append("step_key")
            continue
        if kind not in STEP_KINDS or not isinstance(label, str) or not 1 <= len(label.strip()) <= 120:
            problems.append("step_kind_or_label")
            continue
        prefix, cond_raw, on_met = item.get("result_prefix"), item.get("condition"), item.get("on_met")
        if kind == "task":
            if prefix is not None or cond_raw is not None or on_met is not None:
                problems.append("task_with_wait_fields")
                continue
            steps.append(Step(key, kind, label.strip()))
            continue
        if not (isinstance(prefix, str) and _PREFIX.match(prefix)):
            problems.append("result_prefix")
            continue
        condition = None
        if cond_raw is not None:
            threshold = _number(cond_raw.get("threshold")) if isinstance(cond_raw, dict) else None
            if not isinstance(cond_raw, dict) or set(cond_raw) != _CONDITION_KEYS or cond_raw["op"] not in OPS or not (isinstance(cond_raw["field"], str) and _FIELD.match(cond_raw["field"])) or threshold is None:
                problems.append("condition")
                continue
            condition = Condition(cond_raw["field"], cond_raw["op"], threshold)
        if on_met is not None and (on_met not in ON_MET or condition is None):
            problems.append("on_met")
            continue
        steps.append(Step(key, kind, label.strip(), prefix, condition, on_met))
    return ([], problems) if problems else (steps, [])


def evaluate(condition: Condition, result: Result) -> Outcome:
    """met / not_met / unknown. UNKNOWN unless the result is verified and carries a finite number for the field: an absent, null, text or unverified value satisfies no rule, in either direction."""
    if result.verified is not True:
        return "unknown"
    value = _number(result.values.get(condition.field)) if isinstance(result.values, Mapping) else None
    if value is None:
        return "unknown"
    t = condition.threshold
    holds = {"lt": value < t, "le": value <= t, "gt": value > t, "ge": value >= t, "eq": value == t}[condition.op]
    return "met" if holds else "not_met"


def match_wait(waits: Sequence[Wait], result: Result) -> tuple[Literal["matched", "none", "ambiguous"], Wait | None]:
    """The wait this result belongs to: same patient, still open, the result's code starts with the wait's prefix; the LONGEST prefix wins; a tie for longest is ambiguous (a doctor decides)."""
    code = result.code.casefold()
    hits = [w for w in waits if w.open and w.patient_id == result.patient_id and code.startswith(w.prefix.casefold())]
    if not hits:
        return "none", None
    longest = max(len(w.prefix) for w in hits)
    best = [w for w in hits if len(w.prefix) == longest]
    return ("matched", best[0]) if len(best) == 1 else ("ambiguous", None)


# ---- the state and the ports --------------------------------------------------------------------------------------------------------------------------------------


class CareJourneyState(TypedDict, total=False):
    clinic_id: str
    origin: str
    plan_id: str
    patient_id: str
    step_key: str
    wait_key: str
    result_id: str
    resume_kind: str
    confirm_action: str
    outcome: str
    status: str
    task_id: str
    reasons: list[str]


class PlanScreener(Protocol):
    async def screen(self, conn: AsyncConnection, *, plan_id: uuid.UUID, clinic_id: uuid.UUID) -> str:
        """`clear`, `red_flag_escalated` or `held_unassessable`. The rules only; no model. A red flag raises its own nurse task."""
        ...


class PlanParser(Protocol):
    async def parse(self, conn: AsyncConnection, *, plan_id: uuid.UUID, clinic_id: uuid.UUID) -> object:
        """The MODEL (`llm_large`) proposes {"steps": [...]} from the doctor's plan text. A proposal only: it is validated by code and confirmed by a doctor."""
        ...


class PlanProposals(Protocol):
    async def save(self, conn: AsyncConnection, *, plan_id: uuid.UUID, steps: list[Step], idempotency_key: str) -> None: ...


class PlanActivator(Protocol):
    async def activate(self, conn: AsyncConnection, *, plan_id: uuid.UUID, patient_id: uuid.UUID, idempotency_key: str) -> None:
        """Writes the confirmed plan's tasks and registers one wait per result step."""
        ...


class PlanReader(Protocol):
    async def next_open_wait(self, conn: AsyncConnection, *, plan_id: uuid.UUID) -> dict[str, str] | None:
        """The next step still open that waits for a result: {"step_key", "wait_key"}, or None when none is left."""
        ...

    async def step(self, conn: AsyncConnection, *, plan_id: uuid.UUID, step_key: str) -> Step: ...

    async def wait(self, conn: AsyncConnection, *, wait_key: str) -> Wait: ...


class ResultReader(Protocol):
    async def get(self, conn: AsyncConnection, *, result_id: uuid.UUID) -> Result | None: ...


class WaitClaims(Protocol):
    async def claim(self, conn: AsyncConnection, *, wait_key: str, result_id: uuid.UUID, idempotency_key: str) -> bool:
        """True if THIS result now holds the wait (first claim wins; the same result again is True and writes nothing). False if another result already holds it."""
        ...


class PlanCloser(Protocol):
    async def close_step(self, conn: AsyncConnection, *, plan_id: uuid.UUID, step_key: str, outcome: str, evidence_result_id: uuid.UUID | None, idempotency_key: str) -> None: ...

    async def complete(self, conn: AsyncConnection, *, plan_id: uuid.UUID, idempotency_key: str) -> None: ...

    async def reject(self, conn: AsyncConnection, *, plan_id: uuid.UUID, idempotency_key: str) -> None: ...


class DoctorTasks(Protocol):
    async def raise_task(
        self, conn: AsyncConnection, *, clinic_id: uuid.UUID, origin: str, plan_id: uuid.UUID, reason: str, reasons: list[str], priority: str, assigned_role: str, idempotency_key: str
    ) -> uuid.UUID:
        """`reason` is a closed word: plan_confirmation, referral_proposal, needs_doctor."""
        ...


@dataclass
class CareJourneyDeps:
    session: Callable[[], AbstractAsyncContextManager[AsyncConnection]]
    screener: PlanScreener
    parser: PlanParser
    proposals: PlanProposals
    activator: PlanActivator
    plans: PlanReader
    results: ResultReader
    waits: WaitClaims
    closer: PlanCloser
    tasks: DoctorTasks
    task_priority: str  # required settings, never defaulted: how urgent and for whom a care-plan task is is a clinical-governance decision
    task_role: str


SIDE_EFFECT_PORTS: Final = {"screener": "escalation", "proposals": "plan_proposal", "activator": "plan_activation", "waits": "wait_claim", "closer": "plan_closure", "tasks": "review_task"}
MODEL_PORTS: Final = {"parser": "model"}
NEUTRAL_PORTS: Final = {"session", "plans", "results", "task_priority", "task_role"}

NODE_SPECS: Final = {
    "screen_plan": NodeSpec("screen_plan", frozenset({"escalation"}), pauses=False, models=False),
    "parse_plan": NodeSpec("parse_plan", frozenset({"plan_proposal"}), pauses=False, models=True),
    "request_confirmation": NodeSpec("request_confirmation", frozenset({"review_task"}), pauses=False, models=False),
    "await_confirmation": NodeSpec("await_confirmation", frozenset(), pauses=True, models=False),
    "reject_plan": NodeSpec("reject_plan", frozenset({"plan_closure"}), pauses=False, models=False),
    "activate_plan": NodeSpec("activate_plan", frozenset({"plan_activation"}), pauses=False, models=False),
    "next_wait": NodeSpec("next_wait", frozenset(), pauses=False, models=False),
    "await_result": NodeSpec("await_result", frozenset(), pauses=True, models=False),
    "claim_result": NodeSpec("claim_result", frozenset({"wait_claim"}), pauses=False, models=False),
    "evaluate_result": NodeSpec("evaluate_result", frozenset(), pauses=False, models=False),
    "propose_referral": NodeSpec("propose_referral", frozenset({"review_task"}), pauses=False, models=False),
    "close_step": NodeSpec("close_step", frozenset({"plan_closure"}), pauses=False, models=False),
    "complete_plan": NodeSpec("complete_plan", frozenset({"plan_closure"}), pauses=False, models=False),
    "hold_for_human": NodeSpec("hold_for_human", frozenset({"review_task"}), pauses=False, models=False),
}


def _ids(state: CareJourneyState) -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.UUID(state["clinic_id"]), uuid.UUID(state["plan_id"])


def thread_id_for(plan_id: uuid.UUID) -> str:
    return f"care_journey:{plan_id}"


def initial_state(clinic_id: uuid.UUID, plan_id: uuid.UUID, patient_id: uuid.UUID, origin: str) -> CareJourneyState:
    return {"clinic_id": str(clinic_id), "plan_id": str(plan_id), "patient_id": str(patient_id), "origin": origin, "status": "started", "reasons": []}


def run_config(plan_id: uuid.UUID, clinic_id: uuid.UUID | None = None, origin: str | None = None) -> dict[str, Any]:
    configurable: dict[str, Any] = {"thread_id": thread_id_for(plan_id)}
    if clinic_id is not None and origin is not None:
        configurable.update({"clinic_id": str(clinic_id), "origin": origin, CONFIG_KEY_DURABILITY: "sync"})
    return {"configurable": configurable, "recursion_limit": RECURSION_LIMIT}


def care_journey_enabled(environ: dict[str, str] | None = None) -> bool:
    return feature_flags(environ)["FEATURE_CARE_JOURNEY"]


def build_care_journey_graph(deps: CareJourneyDeps, checkpointer: Any, *, environ: dict[str, str] | None = None):
    """Compile the Care Journey graph. REFUSED while FEATURE_CARE_JOURNEY is off (no Care Journey node exists with the flag off, spec 4); a checkpointer is required; hosted tracing is refused."""
    if not care_journey_enabled(environ if environ is not None else dict(os.environ)):
        raise FrontDoorError("FEATURE_CARE_JOURNEY is off: the Care Journey graph is not built")
    assert_tracing_off(environ)
    if checkpointer is None:
        raise FrontDoorError("a checkpointer is required")
    for variable in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
        os.environ.setdefault(variable, "false")

    async def screen_plan(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        clinic_id, plan_id = _ids(state)
        async with deps.session() as conn:
            outcome = await deps.screener.screen(conn, plan_id=plan_id, clinic_id=clinic_id)
        if outcome == "clear":
            return {"status": "screened"}
        if outcome == "red_flag_escalated":
            return {"status": "red_flag_escalated"}
        return {"status": "held_unassessable", "reasons": ["plan_unassessable"]}

    async def parse_plan(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        clinic_id, plan_id = _ids(state)
        async with deps.session() as conn:
            raw = await deps.parser.parse(conn, plan_id=plan_id, clinic_id=clinic_id)
            steps, problems = parse_proposal(raw)
            if problems:
                return {"status": "plan_invalid", "reasons": sorted(set(problems))[:10]}  # nothing is saved, nothing is repaired
            await deps.proposals.save(conn, plan_id=plan_id, steps=steps, idempotency_key=f"cj:{plan_id}:proposal")
        return {"status": "proposed"}

    async def request_confirmation(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        clinic_id, plan_id = _ids(state)
        async with deps.session() as conn:
            task_id = await deps.tasks.raise_task(
                conn, clinic_id=clinic_id, origin=state["origin"], plan_id=plan_id, reason="plan_confirmation", reasons=[], priority=deps.task_priority, assigned_role=deps.task_role,
                idempotency_key=f"cj:{plan_id}:confirm",
            )
        return {"status": "confirmation_requested", "task_id": str(task_id)}

    def await_confirmation(state: CareJourneyState) -> CareJourneyState:
        # PAUSE. No side effect and no port. The doctor approves or rejects the proposed plan; nothing executes before this returns approve.
        answer = interrupt({"plan_id": state["plan_id"], "waiting_for": "doctor_confirmation", "actions": list(CONFIRM_ACTIONS)})
        if not isinstance(answer, dict) or answer.get("action") not in CONFIRM_ACTIONS:
            raise FrontDoorError("a plan confirmation needs one of the listed actions")
        return {"confirm_action": answer["action"]}

    async def reject_plan(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        _, plan_id = _ids(state)
        async with deps.session() as conn:
            await deps.closer.reject(conn, plan_id=plan_id, idempotency_key=f"cj:{plan_id}:reject")
        return {"status": "rejected"}

    async def activate_plan(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        _, plan_id = _ids(state)
        async with deps.session() as conn:
            await deps.activator.activate(conn, plan_id=plan_id, patient_id=uuid.UUID(state["patient_id"]), idempotency_key=f"cj:{plan_id}:activate")
        return {"status": "active"}

    async def next_wait(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        _, plan_id = _ids(state)
        async with deps.session() as conn:
            nxt = await deps.plans.next_open_wait(conn, plan_id=plan_id)
        if nxt is None:
            return {"status": "no_more_waits"}
        return {"status": "waiting", "step_key": nxt["step_key"], "wait_key": nxt["wait_key"]}

    def await_result(state: CareJourneyState) -> CareJourneyState:
        # PAUSE until a result is routed to this wait (by prefix) or the expected window passes. No side effect and no port.
        answer = interrupt({"plan_id": state["plan_id"], "wait_key": state["wait_key"], "waiting_for": "result", "kinds": list(RESULT_KINDS)})
        if not isinstance(answer, dict) or answer.get("kind") not in RESULT_KINDS:
            raise FrontDoorError("a result entry needs one of the listed kinds")
        if answer["kind"] == "overdue":
            return {"resume_kind": "overdue", "status": "overdue", "reasons": ["result_overdue"]}
        try:
            uuid.UUID(str(answer.get("result_id")))
        except ValueError:
            raise FrontDoorError("a result needs its id") from None
        return {"resume_kind": "result", "result_id": str(answer["result_id"]), "status": "result_in"}

    async def claim_result(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        result_id = uuid.UUID(state["result_id"])
        async with deps.session() as conn:
            held = await deps.waits.claim(conn, wait_key=state["wait_key"], result_id=result_id, idempotency_key=f"cj:{state['wait_key']}:claim:{result_id}")
        return {"status": "claimed"} if held else {"status": "claim_lost"}

    async def evaluate_result(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        _, plan_id = _ids(state)
        async with deps.session() as conn:
            result = await deps.results.get(conn, result_id=uuid.UUID(state["result_id"]))
            step = await deps.plans.step(conn, plan_id=plan_id, step_key=state["step_key"])
            wait = await deps.plans.wait(conn, wait_key=state["wait_key"])
        if result is None:
            return {"status": "needs_doctor", "outcome": "unknown", "reasons": ["result_unreadable"]}
        if result.patient_id != state["patient_id"] or not result.code.casefold().startswith(wait.prefix.casefold()):
            return {"status": "needs_doctor", "outcome": "unknown", "reasons": ["result_does_not_match_wait"]}  # defence in depth: the router's match is re-checked here
        if step.condition is None:
            return {"status": "needs_doctor", "outcome": "unknown", "reasons": ["no_condition_to_evaluate"]}  # a result with nothing to decide it by is read by a doctor
        outcome = evaluate(step.condition, result)
        if outcome == "unknown":
            return {"status": "needs_doctor", "outcome": "unknown", "reasons": ["value_missing_or_unverified"]}  # NEVER closed as not met
        if outcome == "met":
            return {"status": "condition_met", "outcome": "met"} if step.on_met == "propose_referral" else {"status": "needs_doctor", "outcome": "met", "reasons": ["met_no_action_defined"]}
        return {"status": "condition_not_met", "outcome": "not_met"}

    async def propose_referral(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        clinic_id, plan_id = _ids(state)
        async with deps.session() as conn:
            task_id = await deps.tasks.raise_task(
                conn, clinic_id=clinic_id, origin=state["origin"], plan_id=plan_id, reason="referral_proposal", reasons=[], priority=deps.task_priority, assigned_role=deps.task_role,
                idempotency_key=f"cj:{plan_id}:{state['step_key']}:referral",
            )
        return {"status": "referral_proposed", "task_id": str(task_id)}

    async def close_step(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        _, plan_id = _ids(state)
        async with deps.session() as conn:
            await deps.closer.close_step(
                conn, plan_id=plan_id, step_key=state["step_key"], outcome="not_met", evidence_result_id=uuid.UUID(state["result_id"]), idempotency_key=f"cj:{plan_id}:{state['step_key']}:close"
            )
        return {"status": "step_closed"}

    async def complete_plan(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        _, plan_id = _ids(state)
        async with deps.session() as conn:
            await deps.closer.complete(conn, plan_id=plan_id, idempotency_key=f"cj:{plan_id}:complete")
        return {"status": "completed"}

    async def hold_for_human(state: CareJourneyState) -> CareJourneyState:
        assert_tracing_off()
        clinic_id, plan_id = _ids(state)
        async with deps.session() as conn:
            task_id = await deps.tasks.raise_task(
                conn, clinic_id=clinic_id, origin=state["origin"], plan_id=plan_id, reason="needs_doctor", reasons=[r for r in state.get("reasons", []) if isinstance(r, str)][:20],
                priority=deps.task_priority, assigned_role=deps.task_role, idempotency_key=f"cj:{plan_id}:hold:{state.get('wait_key', 'plan')}:{state.get('status', '')}",
            )
        return {"task_id": str(task_id)}

    def after_screen(state: CareJourneyState) -> str:
        status = state.get("status")
        return "parse_plan" if status == "screened" else END if status == "red_flag_escalated" else "hold_for_human"

    def after_parse(state: CareJourneyState) -> str:
        return "request_confirmation" if state.get("status") == "proposed" else "hold_for_human"

    def after_request(state: CareJourneyState) -> str:
        return "await_confirmation" if state.get("status") == "confirmation_requested" else "hold_for_human"

    def after_confirmation(state: CareJourneyState) -> str:
        action = state.get("confirm_action")
        return "activate_plan" if action == "approve" else "reject_plan" if action == "reject" else "hold_for_human"

    def after_activate(state: CareJourneyState) -> str:
        return "next_wait" if state.get("status") == "active" else "hold_for_human"

    def after_next_wait(state: CareJourneyState) -> str:
        status = state.get("status")
        return "await_result" if status == "waiting" else "complete_plan" if status == "no_more_waits" else "hold_for_human"

    def after_await_result(state: CareJourneyState) -> str:
        return "claim_result" if state.get("status") == "result_in" else "hold_for_human"

    def after_claim(state: CareJourneyState) -> str:
        status = state.get("status")
        return "evaluate_result" if status == "claimed" else "await_result" if status == "claim_lost" else "hold_for_human"

    def after_evaluate(state: CareJourneyState) -> str:
        status = state.get("status")
        return "propose_referral" if status == "condition_met" else "close_step" if status == "condition_not_met" else "hold_for_human"

    def after_step_done(state: CareJourneyState) -> str:
        return "next_wait" if state.get("status") in {"referral_proposed", "step_closed"} else "hold_for_human"

    functions = {
        "screen_plan": screen_plan, "parse_plan": parse_plan, "request_confirmation": request_confirmation, "await_confirmation": await_confirmation, "reject_plan": reject_plan, "activate_plan": activate_plan,
        "next_wait": next_wait, "await_result": await_result, "claim_result": claim_result, "evaluate_result": evaluate_result, "propose_referral": propose_referral,
        "close_step": close_step, "complete_plan": complete_plan, "hold_for_human": hold_for_human,
    }
    builder = StateGraph(CareJourneyState)
    for name, fn in functions.items():
        builder.add_node(name, fn)
    builder.add_edge(START, "screen_plan")
    builder.add_conditional_edges("screen_plan", after_screen, {"parse_plan": "parse_plan", "hold_for_human": "hold_for_human", END: END})
    builder.add_conditional_edges("parse_plan", after_parse, {"request_confirmation": "request_confirmation", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("request_confirmation", after_request, {"await_confirmation": "await_confirmation", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("await_confirmation", after_confirmation, {"activate_plan": "activate_plan", "reject_plan": "reject_plan", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("activate_plan", after_activate, {"next_wait": "next_wait", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("next_wait", after_next_wait, {"await_result": "await_result", "complete_plan": "complete_plan", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("await_result", after_await_result, {"claim_result": "claim_result", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("claim_result", after_claim, {"evaluate_result": "evaluate_result", "await_result": "await_result", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("evaluate_result", after_evaluate, {"propose_referral": "propose_referral", "close_step": "close_step", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("propose_referral", after_step_done, {"next_wait": "next_wait", "hold_for_human": "hold_for_human"})
    builder.add_conditional_edges("close_step", after_step_done, {"next_wait": "next_wait", "hold_for_human": "hold_for_human"})
    builder.add_edge("reject_plan", END)
    builder.add_edge("complete_plan", END)
    builder.add_edge("hold_for_human", END)
    graph = builder.compile(checkpointer=checkpointer)
    graph.node_functions = functions  # type: ignore[attr-defined]
    graph.router_functions = {  # type: ignore[attr-defined]
        "after_screen": after_screen, "after_parse": after_parse, "after_request": after_request, "after_confirmation": after_confirmation, "after_activate": after_activate, "after_next_wait": after_next_wait,
        "after_await_result": after_await_result, "after_claim": after_claim, "after_evaluate": after_evaluate, "after_step_done": after_step_done,
    }
    return graph
