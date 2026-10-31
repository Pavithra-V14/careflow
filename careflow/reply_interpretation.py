"""Reply interpretation, FAKES ONLY (docs/design/phase3-scheduling-agent.md section 5; remaining-work A8). Nothing real is called.

A front-desk person asks "what does this patient's reply mean?". In order, in the caller's one transaction and under the caller's own claims:

  1. the deterministic red-flag rules screen the reply FIRST, on every path (`messages.screen_stored_message`, no model): a red flag raises the immediate nurse task, an unreadable or unassessable
     reply raises the standard one; either way NO model is asked, and the answer is "no suggestion: needs a person" (CLAUDE.md rule 2);
  2. only a CLEAR reply, sent by the patient on the appointments topic, is read again (audited, like every read of a message body) and put to the `decision` tier as one closed question;
  3. the answer is a SUGGESTION for a person to confirm: nothing books, nothing is sent, nothing is stored about the patient. `needs_person` is always true in Phase 3.

"No suggestion" is the answer, with a closed reason word, when: the rules flagged or could not assess the reply; the message is not a patient's reply on the appointments topic; no engine is approved for patient
text; the spend ceiling is reached (a VISIBLE stop, never a silent skip); or the engine failed. An uncalibrated confidence is raw, so it is reported as unknown (None), never as a probability
(CLAUDE.md rule 3). The reply text is passed to the decision service as patient data (`DataClass.PHI`), so only a `phi_approved` route may see it, and it is never returned, logged or stored here.
The model's own opinion can never turn a flagged reply into a clear one: the screening happens before and apart from it.
"""

import uuid
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.crypto import DemographicVault
from careflow.decision import DecisionService
from careflow.gateway import AllRoutesFailed, NoApprovedRoute, SpendCeilingReached
from careflow.messages import read_stored_message, screen_stored_message
from careflow.ports import DataClass, DecisionRequest, PermanentError, TransientError
from careflow.rules import RedFlagEngine

QUESTION_TYPE: Final = "offer_reply"
OPTIONS: Final = ("accept", "decline", "other_time", "question", "unclear")
Reason = Literal["red_flag", "unassessable", "not_a_patient_reply", "no_route", "spend_ceiling", "engine_failed"]


@dataclass(frozen=True)
class Interpretation:
    """Ids, closed words and numbers only: never the reply, a rule or a phrase."""

    message_id: uuid.UUID
    screening_id: uuid.UUID
    outcome: str  # red_flag | unassessable | clear
    replayed: bool
    suggestion: str | None  # one of OPTIONS, or None ("no suggestion: needs a person")
    confidence: float | None  # only when the engine's confidence is CALIBRATED; raw confidence is unknown, not a probability
    calibrated: bool | None
    engine: str | None
    reason: Reason | None
    needs_person: bool = True


async def interpret_reply(
    conn: AsyncConnection, vault: DemographicVault, rules: RedFlagEngine, service: DecisionService, message_id: uuid.UUID, *, actor_id: uuid.UUID, idempotency_key: str,
) -> Interpretation:
    # 1. THE RULES FIRST. Raises MessageNotFound / NotPermitted / MessageErased exactly as the screening route does.
    screened = await screen_stored_message(conn, vault, rules, message_id, idempotency_key=idempotency_key)
    base = {"message_id": screened.message_id, "screening_id": screened.screening_id, "outcome": screened.outcome, "replayed": screened.replayed}
    if screened.outcome != "clear":
        return Interpretation(**base, suggestion=None, confidence=None, calibrated=None, engine=None, reason=screened.outcome)  # type: ignore[arg-type]

    # 2. Only now is the text read for the model (an audited read), and only a patient's reply on the appointments topic is ever put to it.
    read = await read_stored_message(conn, vault, message_id, actor_id=actor_id)
    if read.sender_kind != "patient" or read.topic != "appointments":
        return Interpretation(**base, suggestion=None, confidence=None, calibrated=None, engine=None, reason="not_a_patient_reply")
    request = DecisionRequest(QUESTION_TYPE, read.body, OPTIONS, data_class=DataClass.PHI)  # patient text: only a phi_approved route is offered it
    try:
        result = await service.decide(request, clinic_id=read.clinic_id)  # the ORDINARY class: budget-limited (the safety class is never used here)
    except NoApprovedRoute:
        return Interpretation(**base, suggestion=None, confidence=None, calibrated=None, engine=None, reason="no_route")
    except SpendCeilingReached:
        return Interpretation(**base, suggestion=None, confidence=None, calibrated=None, engine=None, reason="spend_ceiling")
    except (AllRoutesFailed, PermanentError, TransientError):
        return Interpretation(**base, suggestion=None, confidence=None, calibrated=None, engine=None, reason="engine_failed")
    decision = result.value
    if decision.choice not in OPTIONS:  # the adapters already enforce this; a stray word is never shown as a suggestion
        return Interpretation(**base, suggestion=None, confidence=None, calibrated=None, engine=None, reason="engine_failed")
    return Interpretation(
        **base, suggestion=decision.choice, confidence=decision.confidence if decision.calibrated else None, calibrated=decision.calibrated, engine=decision.engine, reason=None,
    )
