"""The in-app voice channel (BUILD-SPEC.md 23, 11.6, 5.2, 12.1; CLAUDE.md rules 2 and 11; Appendix D Phase 8; docs/design/phase8-voice.md). MECHANISM ONLY: the engine is a port with a scripted fake; no
audio, no WebSocket and no Gemini call exists here (a new external service, rule 13; `synthetic_only` at launch, 23.1).

A voice session is not a lower-scrutiny channel, it is a higher-urgency one. The two-layer guardrail of 11.6, as code:

  FIRST LINE, SCOPE: the session is opened with a per-workflow SYSTEM PROMPT that restricts it to reading one governed text (an offer, a card's intake question, a checklist item) and listening
    (`prompts/voice_*.v1.system.txt`; static, no placeholder, scanned by CI). The text it reads and the sentence it says when the patient strays are the CLINIC'S governed templates, passed in by the caller;
    nothing in this file words a thing to a patient. As a second check, each finished patient turn that the rules passed clean is classified in scope or not by the `decision` tier; an out-of-scope turn is
    declined with the governed decline text, the session ends and a person follows up in text.
  SECOND LINE, THE RULES ON THE LIVE TRANSCRIPT: every transcribed chunk goes through `StreamEvaluator.feed` (the same deterministic engine as every other channel) AS IT ARRIVES, before the scope
    classifier and before anything else. On a match the session is INTERRUPTED: the engine is forced to say the fixed, pre-approved safety template (never composed live), the immediate nurse task is
    raised through `RedFlagEngine.escalate_red_flag`, and the session ends. A model's opinion can never undo this.
  THE HONEST GAP: the check runs alongside the model, not in front of it, so there is a window between the end of the concerning utterance and the interrupt landing. `run_session` records that window for
    every flag (`gap_ms`); `summarise_gaps` reports it, and with no samples reports NOT MEASURED. Only a real engine can give the real figure (Appendix B); the fake's numbers are never reported as one.

ALSO
  * THE MIC IS SHOWN ONLY WHEN ALL OF IT HOLDS (`mic_state`): the clinic flag FEATURE_VOICE_CHANNEL, the patient's own `voice_enabled`, an available engine route, and rules that can actually assess (unknown is
    not on). Each is independent of the others.
  * CONSENT FIRST (23.4): a per-session consent is recorded BEFORE the engine is opened; without it no audio is processed.
  * UNKNOWN IS NOT CLEAR: if the rule set cannot vouch for a transcript (`unassessable`), the session is not opened, or ends and a person follows up.
  * NO MEMORY: the transcript goes to a sink as a stored working-context record (30-day retention applies there); nothing about the patient is kept by the session object once it ends.
  * DECORATIVE (23.5): an engine that fails to open or dies mid-session ends the session in a closed word. Nothing else changes; the patient continues in text. Chunks already received were screened.
"""

import contextlib
import os
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from careflow.prompts import PromptSet, load_prompt
from careflow.rules import Evaluation, StreamEvaluator

WORKFLOWS: Final = ("slot_offer", "intake_question", "readiness_item")
PROMPT_NAMES: Final = {"slot_offer": "voice_slot_offer", "intake_question": "voice_intake_question", "readiness_item": "voice_readiness_item"}
SCOPE_VERDICTS: Final = ("in_scope", "clinical_question", "out_of_scope")
_TRUE: Final = frozenset({"1", "true", "yes", "on"})


def voice_feature_on(environ: dict[str, str] | None = None) -> bool:
    """The clinic-level FEATURE_VOICE_CHANNEL flag, off unless explicitly on."""
    env = os.environ if environ is None else environ
    return env.get("FEATURE_VOICE_CHANNEL", "").strip().lower() in _TRUE


@dataclass(frozen=True)
class MicState:
    visible: bool
    reason: str  # a closed word


def mic_state(*, clinic_flag: bool | None, patient_voice_enabled: bool | None, engine_available: bool | None, rules_assessable: bool | None) -> MicState:
    """The mic button appears only if the clinic flag AND the patient's own opt-in AND an engine AND assessable rules are all exactly True. Anything else, including unknown (None), hides it."""
    if clinic_flag is not True:
        return MicState(False, "clinic_flag_off")
    if patient_voice_enabled is not True:
        return MicState(False, "patient_not_opted_in")
    if engine_available is not True:
        return MicState(False, "engine_unavailable")
    if rules_assessable is not True:
        return MicState(False, "rules_unassessable")
    return MicState(True, "ok")


def system_prompt(workflow: str) -> PromptSet:
    if workflow not in WORKFLOWS:
        raise ValueError("unknown voice workflow")
    return load_prompt(PROMPT_NAMES[workflow], "v1")


# ---- the ports ----------------------------------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceEvent:
    kind: Literal["transcript", "turn_end", "closed"]
    text: str = ""  # a transcribed chunk of the PATIENT's speech
    at_ms: int = 0  # when the chunk finished, on the same clock as `Ports.clock_ms`


class VoiceEngine(Protocol):
    async def open(self, *, system: str, user: str) -> None: ...

    def events(self) -> AsyncIterator[VoiceEvent]: ...

    async def force_say(self, text: str) -> None:
        """Cut the model's current output and speak exactly this fixed text."""
        ...

    async def close(self) -> None: ...


class SessionConsent(Protocol):
    async def record(self, session_id: uuid.UUID) -> bool:
        """The spoken and on-screen consent and recording disclosure (23.4). True only if the patient consented and it was recorded."""
        ...


class Escalator(Protocol):
    async def escalate(self, session_id: uuid.UUID, evaluation: Evaluation) -> None:
        """Raises the immediate nurse task for a live transcript (idempotent per session and rule)."""
        ...


class TranscriptSink(Protocol):
    async def append(self, session_id: uuid.UUID, chunk: str) -> None: ...


class ScopeClassifier(Protocol):
    async def classify(self, turn_text: str) -> str: ...  # a `decision` call, one of SCOPE_VERDICTS; reached only for a turn the rules passed clean


class FollowUp(Protocol):
    async def raise_task(self, session_id: uuid.UUID, reason: str, idempotency_key: str) -> None:
        """A front-desk follow-up in text. `reason` is a closed word: out_of_scope, unassessable, turn_limit."""
        ...


class SafetyTemplates(Protocol):
    def text(self) -> str | None:
        """The pre-approved, pre-translated safety response, or None if none is approved. Never composed."""
        ...


@dataclass
class Ports:
    engine: VoiceEngine
    consent: SessionConsent
    escalator: Escalator
    sink: TranscriptSink
    scope: ScopeClassifier
    follow_up: FollowUp
    safety: SafetyTemplates
    clock_ms: Callable[[], int] = field(default=lambda: int(time.monotonic() * 1000))


@dataclass(frozen=True)
class SessionResult:
    status: str  # refused_* | completed | red_flag_escalated | declined_out_of_scope | not_clear | turn_limit | engine_failed
    spoken_safety_response: bool = False
    nurse_escalated: bool = False
    gap_ms: int | None = None  # the window between the flagged chunk finishing and the interrupt landing; None unless a flag fired
    turns: int = 0


async def run_session(
    ports: Ports, stream: StreamEvaluator, *, session_id: uuid.UUID, workflow: str, scripted_text: str, decline_text: str, max_turns: int | None
) -> SessionResult:
    """One scoped voice session. `max_turns` is a REQUIRED value with no default (a session that could run forever is a cost and a safety exposure); None refuses to open."""
    if workflow not in WORKFLOWS or not scripted_text.strip() or not decline_text.strip() or max_turns is None or max_turns < 1:
        return SessionResult("refused_bad_request")
    # Unknown is not clear: with no usable rule the transcript could not be vouched for, so no session is opened at all.
    if stream.feed("").outcome == "unassessable":
        return SessionResult("refused_rules_unassessable")
    if not await ports.consent.record(session_id):
        return SessionResult("refused_no_consent")  # nothing was opened and no audio processed
    prompt = system_prompt(workflow)
    user = prompt.render_user(workflow_label=workflow, scripted_text=scripted_text, decline_text=decline_text)
    try:
        await ports.engine.open(system=prompt.system, user=user)
    except Exception:  # noqa: BLE001 - decorative (23.5): the mic simply is not offered and text continues
        return SessionResult("engine_failed")
    turns, turn_text = 0, ""
    try:
        async for event in ports.engine.events():
            if event.kind == "closed":
                return SessionResult("completed", turns=turns)
            if event.kind == "transcript":
                await ports.sink.append(session_id, event.text)
                evaluation = stream.feed(event.text)  # SECOND LINE: the rules read the live transcript as each chunk arrives, before anything else
                if evaluation.outcome == "red_flag":
                    return await _interrupt(ports, session_id, evaluation, event.at_ms, turns)
                if evaluation.outcome != "clear":
                    await ports.engine.close()
                    await ports.follow_up.raise_task(session_id, "unassessable", f"voice:{session_id}:unassessable")
                    return SessionResult("not_clear", turns=turns)
                turn_text += event.text
                continue
            if event.kind == "turn_end":
                turns += 1
                verdict = await ports.scope.classify(turn_text)  # only a turn the rules passed clean reaches a model
                turn_text = ""
                if verdict != "in_scope":  # a clinical question, anything out of scope, or a verdict outside the closed set
                    await ports.engine.force_say(decline_text)
                    await ports.engine.close()
                    await ports.follow_up.raise_task(session_id, "out_of_scope", f"voice:{session_id}:out_of_scope")
                    return SessionResult("declined_out_of_scope", turns=turns)
                if turns >= max_turns:
                    await ports.engine.close()
                    await ports.follow_up.raise_task(session_id, "turn_limit", f"voice:{session_id}:turn_limit")
                    return SessionResult("turn_limit", turns=turns)
    except Exception:  # noqa: BLE001 - the engine died mid-session; what arrived was already screened, and the patient continues in text
        return SessionResult("engine_failed", turns=turns)
    return SessionResult("completed", turns=turns)


async def _interrupt(ports: Ports, session_id: uuid.UUID, evaluation: Evaluation, chunk_done_ms: int, turns: int) -> SessionResult:
    """On a match: INTERRUPT, don't wait (11.6). Speak the fixed safety template, raise the nurse task, end the session. The nurse task is raised even if no template is approved or the engine
    cannot speak; the result then says the response was not spoken (a miss for the gate), never that it was."""
    template = ports.safety.text()
    spoken = False
    if template:
        try:
            await ports.engine.force_say(template)
            spoken = True
        except Exception:  # noqa: BLE001 - a dead engine must not stop the escalation
            spoken = False
    gap = max(0, ports.clock_ms() - chunk_done_ms)
    escalated = False
    try:
        await ports.escalator.escalate(session_id, evaluation)
        escalated = True
    finally:
        with contextlib.suppress(Exception):
            await ports.engine.close()
    return SessionResult("red_flag_escalated", spoken_safety_response=spoken, nurse_escalated=escalated, gap_ms=gap, turns=turns)


# ---- the escalation adapter and the measured gap ------------------------------------------------------------------------------------------------------------------


class RedFlagEscalator:
    """Adapts `RedFlagEngine.escalate_red_flag` (a live transcript is not a stored message) to the `Escalator` port. The source id is the session, so a repeat is the same task."""

    def __init__(self, engine, session_factory, clinic_id: uuid.UUID, origin: str) -> None:
        self._engine, self._session, self._clinic, self._origin = engine, session_factory, clinic_id, origin

    async def escalate(self, session_id: uuid.UUID, evaluation: Evaluation) -> None:
        async with self._session() as conn:
            await self._engine.escalate_red_flag(conn, self._clinic, self._origin, evaluation, f"voice:{session_id}")


@dataclass(frozen=True)
class GapSummary:
    measured: bool
    samples: int
    p50_ms: int | None
    max_ms: int | None


def summarise_gaps(gaps: list[int | None]) -> GapSummary:
    """The interrupt-latency window over real flags. No sample means NOT MEASURED (`measured=False`, no number): a figure from nothing would read as a figure."""
    real = sorted(g for g in gaps if isinstance(g, int) and not isinstance(g, bool) and g >= 0)
    if not real:
        return GapSummary(False, 0, None, None)
    return GapSummary(True, len(real), real[len(real) // 2], real[-1])
