"""The voice channel offline (careflow/voice.py). A scripted FAKE engine stands in for the speech-to-speech model: nothing is heard, spoken or sent. The tests the phase names: a red flag spoken mid-session
triggers the same safety response and nurse escalation as every other channel; the patient's opt-in gates the mic independently of the clinic flag; the interrupt gap is recorded and an unmeasured gap is
never reported as a figure. Every phrase here is a synthetic token (`zzalpha`), never a clinical term."""

import uuid

import pytest

from careflow import evalmetrics, voice
from careflow.evalmetrics import Outcome
from careflow.goldset import GoldCase
from careflow.rules import RuleSet, StreamEvaluator, compile_rule
from careflow.voice import (
    MicState,
    Ports,
    VoiceEvent,
    mic_state,
    run_session,
    summarise_gaps,
    voice_feature_on,
)

pytestmark = pytest.mark.asyncio
SESSION = uuid.uuid4()
SAFETY = "FIXED SAFETY TEMPLATE (pre-approved)"
DECLINE = "FIXED DECLINE TEXT (pre-approved)"
OFFER = "DEMO offer text"


def stream(*phrases, empty=False):
    rules = []
    for i, phrase in enumerate(phrases):
        rule, problem = compile_rule({"id": uuid.uuid4(), "rule_code": f"r{i}", "version_id": 1, "status": "approved", "phrases": [phrase], "patterns": []})
        assert problem is None
        rules.append(rule)
    return StreamEvaluator(RuleSet(tuple(rules), "clinical", 0, ()) if not empty else RuleSet((), "clinical", 0, ()))


class FakeEngine:
    def __init__(self, script, *, open_fails=False, die_after=None):
        self.script, self.open_fails, self.die_after = list(script), open_fails, die_after
        self.log: list[str] = []
        self.said: list[str] = []
        self.opened_with: tuple[str, str] | None = None
        self.closed = False

    async def open(self, *, system, user):
        self.log.append("open")
        if self.open_fails:
            raise RuntimeError("engine down")
        self.opened_with = (system, user)

    async def events(self):
        for i, ev in enumerate(self.script):
            if self.die_after is not None and i >= self.die_after:
                raise RuntimeError("engine died")
            self.log.append(f"event:{ev.kind}")
            yield ev

    async def force_say(self, text):
        self.log.append("force_say")
        self.said.append(text)

    async def close(self):
        self.closed = True
        self.log.append("close")


class Fakes:
    def __init__(self, engine, *, consent=True, scope=("in_scope",), safety=SAFETY, now=0):
        self.engine, self.consent_ok, self.scopes, self.safety_text = engine, consent, list(scope), safety
        self.events: list[str] = []
        self.escalated: list = []
        self.tasks: list[str] = []
        self.sink: list[str] = []
        self.now = now

    def ports(self) -> Ports:
        f = self

        class Consent:
            async def record(self, session_id):
                f.events.append("consent")
                f.engine.log.append("consent")
                return f.consent_ok

        class Escalator:
            async def escalate(self, session_id, evaluation):
                f.events.append("escalate")
                f.escalated.append(evaluation)

        class Sink:
            async def append(self, session_id, chunk):
                f.sink.append(chunk)

        class Scope:
            async def classify(self, turn_text):
                f.events.append("scope")
                return f.scopes.pop(0)

        class FollowUp:
            async def raise_task(self, session_id, reason, idempotency_key):
                f.tasks.append(reason)
                assert idempotency_key.startswith("voice:")

        class Safety:
            def text(self):
                return f.safety_text

        return Ports(self.engine, Consent(), Escalator(), Sink(), Scope(), FollowUp(), Safety(), clock_ms=lambda: f.now)


async def go(fakes, st, *, workflow="slot_offer", max_turns=3):
    return await run_session(fakes.ports(), st, session_id=SESSION, workflow=workflow, scripted_text=OFFER, decline_text=DECLINE, max_turns=max_turns)


# ---- the mic: every condition, independently ----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["clinic_flag", "patient_voice_enabled", "engine_available", "rules_assessable"])
@pytest.mark.parametrize("value", [False, None])
def test_the_mic_needs_every_condition_and_unknown_is_off(missing, value):
    on = {"clinic_flag": True, "patient_voice_enabled": True, "engine_available": True, "rules_assessable": True}
    assert mic_state(**on) == MicState(True, "ok")
    assert mic_state(**{**on, missing: value}).visible is False


def test_the_patient_opt_in_gates_the_mic_with_the_clinic_flag_on_and_the_flag_gates_it_with_the_opt_in_on():
    assert mic_state(clinic_flag=True, patient_voice_enabled=False, engine_available=True, rules_assessable=True).reason == "patient_not_opted_in"
    assert mic_state(clinic_flag=False, patient_voice_enabled=True, engine_available=True, rules_assessable=True).reason == "clinic_flag_off"


def test_the_clinic_flag_is_off_unless_explicitly_on():
    for env in ({}, {"FEATURE_VOICE_CHANNEL": ""}, {"FEATURE_VOICE_CHANNEL": "false"}, {"FEATURE_VOICE_CHANNEL": "0"}):
        assert voice_feature_on(env) is False
    for value in ("1", "true", "TRUE", "yes", "on"):
        assert voice_feature_on({"FEATURE_VOICE_CHANNEL": value}) is True


# ---- scope: the first line --------------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("workflow", voice.WORKFLOWS)
async def test_each_workflow_opens_with_its_own_static_scope_restricted_prompt_and_the_governed_text(workflow):
    engine = FakeEngine([VoiceEvent("closed")])
    await go(Fakes(engine), stream("zzalpha"), workflow=workflow)
    system, user = engine.opened_with
    assert system == voice.system_prompt(workflow).system and "helpful" not in system.lower().replace("be helpful beyond that", "")
    assert OFFER in user and DECLINE in user and workflow in user
    assert "never book" in system.lower() or "never decide" in system.lower() or "never record" in system.lower()


async def test_the_three_system_prompts_differ_and_none_has_a_placeholder():
    systems = {w: voice.system_prompt(w).system for w in voice.WORKFLOWS}
    assert len(set(systems.values())) == 3 and not any("{" in s for s in systems.values())


async def test_an_out_of_scope_turn_is_declined_with_the_governed_text_and_a_person_follows_up():
    engine = FakeEngine([VoiceEvent("transcript", "an ordinary words", 10), VoiceEvent("turn_end")])
    fakes = Fakes(engine, scope=("clinical_question",))
    result = await go(fakes, stream("zzalpha"))
    assert result.status == "declined_out_of_scope" and engine.said == [DECLINE] and fakes.tasks == ["out_of_scope"] and engine.closed


async def test_a_scope_verdict_outside_the_closed_set_is_declined_too():
    engine = FakeEngine([VoiceEvent("transcript", "words", 10), VoiceEvent("turn_end")])
    result = await go(Fakes(engine, scope=("whatever",)), stream("zzalpha"))
    assert result.status == "declined_out_of_scope"


async def test_an_in_scope_session_completes():
    engine = FakeEngine([VoiceEvent("transcript", "yes please", 10), VoiceEvent("turn_end"), VoiceEvent("closed")])
    result = await go(Fakes(engine), stream("zzalpha"))
    assert result.status == "completed" and result.turns == 1 and engine.said == []


async def test_a_session_stops_at_the_turn_limit_and_a_missing_limit_refuses_to_open():
    script = [VoiceEvent("transcript", "words", 1), VoiceEvent("turn_end")] * 3
    engine = FakeEngine(script)
    result = await go(Fakes(engine, scope=("in_scope",) * 3), stream("zzalpha"), max_turns=2)
    assert result.status == "turn_limit" and result.turns == 2
    refused = await go(Fakes(FakeEngine([])), stream("zzalpha"), max_turns=None)
    assert refused.status == "refused_bad_request"


# ---- the rules on the live transcript: the second line ---------------------------------------------------------------------------------------------------


async def test_a_red_flag_spoken_mid_session_forces_the_fixed_safety_response_and_the_nurse_task_and_ends_the_session():
    engine = FakeEngine([VoiceEvent("transcript", "some ordinary words", 100), VoiceEvent("transcript", " and then zzalpha", 250), VoiceEvent("transcript", "never reached", 300)])
    fakes = Fakes(engine, now=262)
    result = await go(fakes, stream("zzalpha"))
    assert result.status == "red_flag_escalated" and result.spoken_safety_response and result.nurse_escalated
    assert engine.said == [SAFETY] and len(fakes.escalated) == 1 and engine.closed
    assert result.gap_ms == 12  # the window between the chunk finishing (250) and the interrupt landing (262), recorded
    assert "event:transcript" in engine.log and engine.log.count("event:transcript") == 2  # the third chunk was never consumed
    assert fakes.scopes == ["in_scope"]  # no scope classifier ran on the flagged turn


async def test_the_rules_run_before_the_scope_classifier_or_any_model_on_a_turn():
    engine = FakeEngine([VoiceEvent("transcript", "zzalpha", 5), VoiceEvent("turn_end")])
    fakes = Fakes(engine)
    await go(fakes, stream("zzalpha"))
    assert "scope" not in fakes.events and fakes.events == ["consent", "escalate"]


async def test_a_phrase_split_across_chunks_is_still_caught():
    engine = FakeEngine([VoiceEvent("transcript", "zz", 1), VoiceEvent("transcript", "alpha", 2)])
    result = await go(Fakes(engine), stream("zzalpha"))
    assert result.status == "red_flag_escalated"


async def test_the_safety_response_is_never_composed_live_and_no_template_still_escalates_but_is_not_called_spoken():
    engine = FakeEngine([VoiceEvent("transcript", "zzalpha", 1)])
    fakes = Fakes(engine, safety=None)
    result = await go(fakes, stream("zzalpha"))
    assert engine.said == [] and result.spoken_safety_response is False and result.nurse_escalated and len(fakes.escalated) == 1


async def test_a_dead_engine_does_not_stop_the_nurse_task():
    engine = FakeEngine([VoiceEvent("transcript", "zzalpha", 1)])

    async def broken(text):
        raise RuntimeError("cannot speak")

    engine.force_say = broken
    fakes = Fakes(engine)
    result = await go(fakes, stream("zzalpha"))
    assert result.nurse_escalated and result.spoken_safety_response is False and len(fakes.escalated) == 1


async def test_the_flagged_chunk_is_still_kept_as_working_context_and_nothing_is_dropped():
    engine = FakeEngine([VoiceEvent("transcript", "zzalpha", 1)])
    fakes = Fakes(engine)
    await go(fakes, stream("zzalpha"))
    assert fakes.sink == ["zzalpha"]


# ---- consent, unknown is not clear, decorative ----------------------------------------------------------------------------------------------------------


async def test_without_consent_the_engine_is_never_opened_and_no_audio_is_processed():
    engine = FakeEngine([VoiceEvent("transcript", "zzalpha", 1)])
    result = await go(Fakes(engine, consent=False), stream("zzalpha"))
    assert result.status == "refused_no_consent" and "open" not in engine.log and engine.log == ["consent"]


async def test_consent_is_recorded_before_the_engine_opens():
    engine = FakeEngine([VoiceEvent("closed")])
    await go(Fakes(engine), stream("zzalpha"))
    assert engine.log.index("consent") < engine.log.index("open")


async def test_a_rule_set_that_cannot_assess_never_opens_a_session():
    engine = FakeEngine([VoiceEvent("transcript", "words", 1)])
    fakes = Fakes(engine)
    result = await go(fakes, stream(empty=True))
    assert result.status == "refused_rules_unassessable" and engine.log == [] and fakes.events == []  # not even consent was asked: nothing can be vouched for


async def test_an_engine_that_will_not_open_ends_the_session_quietly():
    result = await go(Fakes(FakeEngine([], open_fails=True)), stream("zzalpha"))
    assert result.status == "engine_failed"


async def test_an_engine_that_dies_mid_session_ends_it_and_what_arrived_was_already_screened():
    engine = FakeEngine([VoiceEvent("transcript", "words", 1), VoiceEvent("transcript", "more", 2)], die_after=1)
    fakes = Fakes(engine)
    result = await go(fakes, stream("zzalpha"))
    assert result.status == "engine_failed" and fakes.sink == ["words"]


# ---- the measured gap and the gate ----------------------------------------------------------------------------------------------------------------------


def test_with_no_flag_samples_the_gap_is_not_measured_and_no_number_is_reported():
    assert summarise_gaps([]) == voice.GapSummary(False, 0, None, None)
    assert summarise_gaps([None, None]).measured is False
    s = summarise_gaps([30, None, 10, 20])
    assert (s.measured, s.samples, s.p50_ms, s.max_ms) == (True, 3, 20, 30)


def _case(cid, red=True):
    return GoldCase(cid, "voice", "held_out", "en", "DEMO scripted utterance", {"red_flag": red}, "llm", "Dr Example", "clinician", "2026-11-06")


async def test_the_voice_gold_gate_counts_a_case_only_when_both_the_spoken_response_and_the_nurse_task_happened():
    both = Outcome(voice_safety_response_spoken=True, voice_nurse_escalated=True)
    pairs = [(_case("a"), both), (_case("b"), Outcome(voice_safety_response_spoken=True, voice_nurse_escalated=False)), (_case("c"), Outcome()), (_case("d", red=False), Outcome())]
    m = evalmetrics.voice_safety_escalation_rate(pairs)
    assert (m.value, m.n, m.hits) == (1 / 3, 3, 1)
    ev = evalmetrics.evidence_for_gates([(_case("a"), both)], source="run 1")
    assert ev["voice_safety_escalation_rate"]["value"] == 1.0
    assert "voice_safety_escalation_rate" not in evalmetrics.evidence_for_gates([], source="run 1")  # no voice case: NOT MEASURED


async def test_a_scripted_red_flag_session_feeds_the_voice_gate_end_to_end():
    engine = FakeEngine([VoiceEvent("transcript", "zzalpha", 1)])
    fakes = Fakes(engine)
    r = await go(fakes, stream("zzalpha"))
    outcome = Outcome(voice_safety_response_spoken=r.spoken_safety_response, voice_nurse_escalated=r.nurse_escalated)
    assert evalmetrics.voice_safety_escalation_rate([(_case("a"), outcome)]).value == 1.0


async def test_the_escalation_adapter_uses_the_session_as_the_source_and_never_the_words():
    calls = []

    class FakeRedFlagEngine:
        async def escalate_red_flag(self, conn, clinic_id, origin, evaluation, source_message_id):
            calls.append((clinic_id, origin, source_message_id, evaluation))

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def session():
        yield object()

    clinic = uuid.uuid4()
    st = stream("zzalpha")
    evaluation = st.feed("zzalpha")
    await voice.RedFlagEscalator(FakeRedFlagEngine(), session, clinic, "test").escalate(SESSION, evaluation)
    assert calls == [(clinic, "test", f"voice:{SESSION}", evaluation)]
