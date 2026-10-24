"""The decision service: ask the gateway for role `decision`, and RECORD every call (BUILD-SPEC.md §5.2, §14.4).

"Every decision call records which engine answered and its confidence. An eval number is meaningless if you can't tell which engine produced
it." One `DecisionRecord` is written per call, with ids, numbers and labels only: the question type, the engine and provider and pinned model
id, the confidence and whether it is calibrated, the route (checkpoint or prompt version), the data class, the call class, and the attempts
made on the way (so a fallback is visible). NEVER the text, the options' content beyond their count, or the probabilities of other options.
A call that fails is recorded too (outcome `failed`, with the attempts), because a decision that could not be made is evidence.

The record goes through a `DecisionRecorder` port (an in-memory list here; a table is a later migration). The service adds nothing to the
decision itself: it is not a policy layer. In particular the red-flag RULES run first, on every path, before any call here (CLAUDE.md rule 2),
and a decision engine's safety opinion may only ESCALATE a case the rules passed clean; that is enforced by the callers that use a decision
for a safety purpose, not here.
"""

import uuid
from dataclasses import dataclass
from typing import Protocol

from careflow.budget import CallClass, RunBudget
from careflow.gateway import (
    AllRoutesFailed,
    Gateway,
    GatewayResult,
    NoApprovedRoute,
    SpendCeilingReached,
)
from careflow.ports import DecisionRequest, DecisionResult


@dataclass(frozen=True)
class DecisionRecord:
    clinic_id: uuid.UUID
    question_type: str
    option_count: int
    data_class: str
    call_class: str
    outcome: str  # decided | no_approved_route | spend_ceiling | failed
    engine: str | None = None
    provider: str | None = None
    model_id: str | None = None
    confidence: float | None = None
    calibrated: bool | None = None
    route: str | None = None
    attempts: tuple[tuple[str, str, str], ...] = ()  # (provider, model_id, outcome) for each route tried


class DecisionRecorder(Protocol):
    def record(self, entry: DecisionRecord) -> None: ...


class InMemoryDecisionRecorder:
    def __init__(self) -> None:
        self.entries: list[DecisionRecord] = []

    def record(self, entry: DecisionRecord) -> None:
        self.entries.append(entry)


class DecisionService:
    def __init__(self, gateway: Gateway, recorder: DecisionRecorder) -> None:
        self._gateway, self._recorder = gateway, recorder

    async def decide(
        self, request: DecisionRequest, *, clinic_id: uuid.UUID, call_class: CallClass = CallClass.ORDINARY, budget: RunBudget | None = None
    ) -> GatewayResult[DecisionResult]:
        base = {
            "clinic_id": clinic_id, "question_type": request.question_type, "option_count": len(request.options),
            "data_class": request.data_class.value, "call_class": call_class.value,
        }
        try:
            result = await self._gateway.call("decision", request, data_class=request.data_class, clinic_id=clinic_id, call_class=call_class, budget=budget)
        except NoApprovedRoute:
            self._recorder.record(DecisionRecord(**base, outcome="no_approved_route"))
            raise
        except SpendCeilingReached:
            self._recorder.record(DecisionRecord(**base, outcome="spend_ceiling"))
            raise
        except AllRoutesFailed as exc:
            self._recorder.record(DecisionRecord(**base, outcome="failed", attempts=tuple((a.provider, a.model_id, a.outcome) for a in exc.attempts)))
            raise
        decision = result.value
        self._recorder.record(
            DecisionRecord(
                **base, outcome="decided", engine=decision.engine, provider=result.provider, model_id=result.model_id,
                confidence=decision.confidence, calibrated=decision.calibrated, route=decision.route,
                attempts=tuple((a.provider, a.model_id, a.outcome) for a in result.attempts),
            )
        )
        return result
