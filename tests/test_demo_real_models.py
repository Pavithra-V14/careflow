"""The hosted providers wired into the DEMO Front Door (careflow/demo_real_models.py, scripts/demo_real_models.py). Offline, PROVIDERS FAKED: the vision and decision stages run behind the REAL gateway over fake adapters,
the real Front Door graph runs over the same fake database edges as tests/test_front_door.py, and no network or key is touched. Synthetic tokens only.

What is proven: the order (the transcription may come first, then the rules, and the decision model never before the rules); a flagged letter ends with no decision call; a model that cannot answer leaves a
proposal that still waits for a person; the model only reorders the fictional cards; every gate that is missing keeps the fakes; the translation draft is a suggestion with a named status; the script refuses outside
dev + demo + synthetic and prints no text, key or URL."""

import asyncio
import io
import re
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from careflow import demo_real_models, synthetic_letter
from careflow.adapters.vision import GatewayVision
from careflow.demo_front_door import DemoExtractor, DemoRetriever, DemoTexts
from careflow.extraction import worker_context
from careflow.front_door import (
    FrontDoorDeps,
    build_front_door,
    initial_state,
    run_config,
)
from careflow.front_door_runner import FrontDoorComponents
from careflow.gateway import (
    Gateway,
    InMemorySpendLedger,
    ModelRow,
    SpendCeilingReached,
    SpendLimits,
)
from careflow.ports import (
    AdapterResult,
    DataClass,
    DecisionResult,
    PermanentError,
    TranslationResult,
    Usage,
    VisionResult,
)
from careflow.provider_setup import ProviderSet
from tests.test_front_door import Holds, Proposer, Recorder
from tests.test_providers import KEY, Mock, json_reply
from tests.test_referral import CLINIC, DOC, Conn, Engine, Ingestor

ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPT = "DUMMY REFERRAL LETTER\nPATIENT: DEMO KILO SAMPLE, DOB 1990-01-01\nREF ZQ-4821. PLEASE SEE THIS PATIENT. ROUTINE REVIEW."
ON = {"CAREFLOW_DATA_POLICY": "synthetic_only", demo_real_models.SWITCH: "on"}
ZERO = Decimal(0)


class FakeVisionAdapter:
    def __init__(self, log, text=TRANSCRIPT, raises=None):
        self.log, self.text, self.raises, self.requests = log, text, raises, []

    async def __call__(self, request):
        self.log.append("MODEL:vision")
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return AdapterResult(VisionResult(self.text), Usage(100, 20))


class FakeDecisionAdapter:
    def __init__(self, log, choice="demo-synthetic-card-B", raises=None):
        self.log, self.choice, self.raises, self.requests = log, choice, raises, []

    async def __call__(self, request):
        self.log.append("MODEL:decision")
        self.requests.append(request)
        if self.raises:
            raise self.raises
        probabilities = {o: (0.8 if o == self.choice else 0.2) for o in request.options}
        return AdapterResult(DecisionResult(self.choice, probabilities, probabilities.get(self.choice, 0.5), "fake-jev", False, "fake-jev"), Usage(50, 5))


def providers_for(vision, decision, translation=None):
    rows, adapters = [], {}
    if vision is not None:
        rows.append(ModelRow("vlm", "gemini", "gemini-test-1", "synthetic_only", 1, ZERO, ZERO, ZERO, ZERO))
        adapters[("gemini", "gemini-test-1")] = vision
    if decision is not None:
        rows.append(ModelRow("decision", "jev", "typesafe/jev-router", "synthetic_only", 1, ZERO, ZERO, ZERO, ZERO))
        adapters[("jev", "typesafe/jev-router")] = decision
    gateway = Gateway(rows, adapters, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=Decimal(1)), origin="test")
    return ProviderSet(gateway=gateway, vision=GatewayVision(gateway, data_class=DataClass.SYNTHETIC) if vision is not None else None, translation=translation)


def base_components(log, outcome="clear"):
    return FrontDoorComponents(
        engine=Engine(log, outcome), texts=DemoTexts(), extractor=DemoExtractor(), ingestor=Ingestor(log), retriever=DemoRetriever(),  # type: ignore[arg-type]
        proposer=Proposer(log, "proposal"), recorder=Recorder(log, "decision"), holds=Holds(log, "hold"), dead_letters=None, retrieval_scope={"domain": "demo"}, profile="demo",
    )


async def loader(document_id):
    assert document_id == DOC
    return synthetic_letter.letter_scan()[0], "image/png", CLINIC


async def run_graph(log, *, outcome="clear", vision=None, decision=None):
    vision = vision or FakeVisionAdapter(log)
    decision = decision if decision is not None else FakeDecisionAdapter(log)
    fakes = base_components(log, outcome)
    up = demo_real_models.upgrade_components(fakes, providers_for(vision, decision), loader=loader, environ=ON, profile="demo")
    conn = Conn(log=log)

    @asynccontextmanager
    async def session():
        yield conn

    c = up.components
    deps = FrontDoorDeps(session=session, engine=c.engine, texts=c.texts, extractor=c.extractor, ingestor=c.ingestor, retriever=c.retriever, proposer=c.proposer, recorder=c.recorder, holds=c.holds,
                         retrieval_scope=dict(c.retrieval_scope), profile="demo")
    graph = build_front_door(deps, InMemorySaver(), environ={})
    config = run_config(DOC, CLINIC, "test")
    with worker_context():
        await graph.ainvoke(initial_state(DOC, CLINIC, "test"), config)
    return up, graph, config, c


def events_in(log, prefix):
    return [i for i, e in enumerate(log) if e == prefix]


# ---- the whole path, with fake providers --------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clear_dummy_letter_reads_screens_decides_and_waits_for_a_person_in_that_order():
    log: list[str] = []
    decision = FakeDecisionAdapter(log, "demo-synthetic-card-B")
    up, graph, config, c = await run_graph(log, decision=decision)
    state = await graph.aget_state(config)
    assert state.next == ("review",) and state.values["status"] == "triage_proposed"  # paused: a person decides
    assert c.recorder.performed == {}  # nothing approved
    vision_at, rules_at, decision_at = log.index("MODEL:vision"), log.index("rules"), log.index("MODEL:decision")
    assert vision_at < rules_at < decision_at, log  # the transcription (the one carve-out), then the rules, and the decision model only after the rules
    top, second = state.values["candidates"]
    assert (top["card_id"], top["version"]) == ("demo-synthetic-card-B", 1) and second["card_id"] == "demo-synthetic-card-A"  # the model only REORDERED the fictional cards
    assert top == {"card_id": "demo-synthetic-card-B", "version": 1, "probability": 0.8, "engine": "fake-jev", "model_id": "typesafe/jev-router", "confidence": 0.8, "rule": "chosen_option_ranks_first"}  # what the review screen shows: engine, model, confidence, rule, probability
    assert second == {"card_id": "demo-synthetic-card-A", "version": 1, "probability": 0.2}  # each alternative carries its own probability, and nothing else
    assert {x["card_id"] for x in state.values["candidates"]} == {"demo-synthetic-card-A", "demo-synthetic-card-B"}
    (call,) = c.ingestor.calls
    assert call["name"] == "Demo Kilo Sample" and call["dob"] == "1990-01-01"  # read by the regular expression, each claim quoted from the letter
    assert up.ranker.last.outcome == "suggested" and up.ranker.last.chosen == "demo-synthetic-card-B"
    assert up.text.startswith("real") and up.decision.startswith("real")


@pytest.mark.asyncio
async def test_the_decision_model_is_only_offered_the_masked_letter_and_only_as_synthetic():
    log: list[str] = []
    decision = FakeDecisionAdapter(log)
    await run_graph(log, decision=decision)
    (request,) = decision.requests
    assert request.data_class is DataClass.SYNTHETIC and request.question_type == demo_real_models.QUESTION
    assert "1990-01-01" not in request.text and "DEMO KILO SAMPLE" not in request.text.upper().replace("[", "")  # masked: no date, no labelled name


@pytest.mark.asyncio
async def test_a_flagged_letter_ends_at_the_rules_with_no_decision_call_and_no_proposal():
    log: list[str] = []
    decision = FakeDecisionAdapter(log)
    up, graph, config, c = await run_graph(log, outcome="red_flag", decision=decision)
    state = await graph.aget_state(config)
    assert state.values["status"] == "red_flag_escalated" and state.next == ()
    assert "MODEL:decision" not in log and decision.requests == [] and c.proposer.performed == {} and up.ranker.last.outcome == "not_asked"
    assert log.index("MODEL:vision") < log.index("rules")  # only the transcription came before the rules


@pytest.mark.asyncio
async def test_an_unassessable_letter_is_held_and_no_decision_model_is_asked():
    log: list[str] = []
    _, graph, config, c = await run_graph(log, outcome="unassessable")
    state = await graph.aget_state(config)
    assert state.values["status"] != "triage_proposed" and "MODEL:decision" not in log and c.proposer.performed == {}


@pytest.mark.asyncio
async def test_a_decision_model_that_cannot_answer_leaves_a_proposal_in_the_fake_order_for_a_person():
    for failure in (PermanentError("no"), SpendCeilingReached("the ceiling")):
        log: list[str] = []
        up, graph, config, _ = await run_graph(log, decision=FakeDecisionAdapter(log, raises=failure))
        state = await graph.aget_state(config)
        assert state.next == ("review",)  # still waits for a person
        assert [x["card_id"] for x in state.values["candidates"]] == ["demo-synthetic-card-A", "demo-synthetic-card-B"]  # the fake order: an unknown answer is not a value
        assert up.ranker.last.outcome == "unavailable"


@pytest.mark.asyncio
async def test_an_answer_that_is_not_one_of_the_options_is_not_used():
    log: list[str] = []
    up, graph, config, _ = await run_graph(log, decision=FakeDecisionAdapter(log, choice="invented-card"))
    state = await graph.aget_state(config)
    assert [x["card_id"] for x in state.values["candidates"]] == ["demo-synthetic-card-A", "demo-synthetic-card-B"] and up.ranker.last.outcome == "unavailable"


@pytest.mark.asyncio
async def test_a_vision_failure_is_a_hold_never_a_clear_and_nothing_else_is_asked():
    log: list[str] = []
    _, graph, config, c = await run_graph(log, vision=FakeVisionAdapter(log, raises=PermanentError("no")))
    state = await graph.aget_state(config)
    assert state.values["status"] == "extraction_unavailable"
    assert "rules" not in log and "MODEL:decision" not in log and c.proposer.performed == {}  # unknown is not clear: no rules verdict, no decision


@pytest.mark.asyncio
async def test_an_empty_transcription_is_never_treated_as_clear():
    log: list[str] = []
    _, graph, config, c = await run_graph(log, vision=FakeVisionAdapter(log, text="   "))
    state = await graph.aget_state(config)
    assert state.values["status"] != "triage_proposed" and "MODEL:decision" not in log and c.proposer.performed == {}


@pytest.mark.asyncio
async def test_the_vision_stage_is_offered_the_page_as_synthetic():
    log: list[str] = []
    vision = FakeVisionAdapter(log)
    await run_graph(log, vision=vision)
    (request,) = vision.requests
    assert request.clinic_id == CLINIC and request.content_type == "image/png"


# ---- every missing gate keeps the fakes -----------------------------------------------------------------------------------------------------------


def test_nothing_is_upgraded_unless_demo_synthetic_and_the_switch_all_hold():
    fakes = base_components([])
    p = providers_for(FakeVisionAdapter([]), FakeDecisionAdapter([]))
    for environ, profile in (({}, "demo"), ({"CAREFLOW_DATA_POLICY": "synthetic_only"}, "demo"), (ON, "clinical"), ({demo_real_models.SWITCH: "on"}, "demo"), ({**ON, "CAREFLOW_DATA_POLICY": "phi_approved"}, "demo")):
        up = demo_real_models.upgrade_components(fakes, p, loader=loader, environ=environ, profile=profile)
        assert up.components is fakes and up.text.startswith("fake") and up.decision.startswith("fake"), (environ, profile)


def test_a_missing_provider_keeps_that_stage_a_fake_and_says_why():
    fakes = base_components([])
    nothing = ProviderSet(unavailable={"gemini": "the gemini key is not set", "jev": "the openrouter key is not set"})
    up = demo_real_models.upgrade_components(fakes, nothing, loader=loader, environ=ON, profile="demo")
    assert up.text == "fake: the gemini key is not set" and up.decision == "fake: the openrouter key is not set"
    assert isinstance(up.components.texts, DemoTexts) and up.components.retriever is fakes.retriever
    only_decision = demo_real_models.upgrade_components(fakes, providers_for(None, FakeDecisionAdapter([])), loader=loader, environ=ON, profile="demo")
    assert only_decision.text.startswith("fake") and only_decision.decision.startswith("real")
    no_loader = demo_real_models.upgrade_components(fakes, providers_for(FakeVisionAdapter([]), None), loader=None, environ=ON, profile="demo")
    assert no_loader.text == "fake: no way to read the stored document"
    none_at_all = demo_real_models.upgrade_components(fakes, None, loader=None, environ=ON, profile="demo")
    assert none_at_all.text.startswith("fake") and none_at_all.decision.startswith("fake")


@pytest.mark.asyncio
async def test_a_canary_letter_is_still_made_up_and_every_other_document_is_read_for_real():
    log: list[str] = []
    up = demo_real_models.upgrade_components(base_components(log), providers_for(FakeVisionAdapter(log), None), loader=loader, environ=ON, profile="demo")
    canary = await up.components.texts.text_for_origin(DOC, "canary")  # no vision call, no stored file
    assert "SYNTHETIC CANARY" in canary and "MODEL:vision" not in log
    with worker_context():
        real = await up.components.texts.text_for_origin(DOC, "test")
    assert "DEMO KILO SAMPLE" in real and "MODEL:vision" in log


@pytest.mark.asyncio
async def test_a_decision_without_a_known_clinic_makes_no_call():
    log: list[str] = []
    decision = FakeDecisionAdapter(log)
    ranker = demo_real_models.DecisionRankedRetriever(DemoRetriever(), demo_real_models.DecisionService(providers_for(None, decision).gateway, demo_real_models.InMemoryDecisionRecorder()), demo_real_models.ClinicMemo())
    found = await ranker.retrieve("some masked letter", scope={})
    assert decision.requests == [] and ranker.last.outcome == "unknown_clinic" and [c.card_id for c in found.candidates] == ["demo-synthetic-card-A", "demo-synthetic-card-B"]


# ---- the identity reader --------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_letter_reader_accepts_any_letter_case_and_claims_only_what_it_can_quote():
    reader = demo_real_models.LetterExtractor()
    got = await reader.extract("Patient: demo kilo sample,  DOB  1990-01-01 please", None, clinic_id=CLINIC)
    assert got.name == "Demo Kilo Sample" and got.dob == "1990-01-01" and got.name_quote in "Patient: demo kilo sample,  DOB  1990-01-01 please" and got.dob_quote in "Patient: demo kilo sample,  DOB  1990-01-01 please"
    nothing = await reader.extract("a letter with no patient line", None, clinic_id=CLINIC)
    assert nothing.name is None and nothing.dob is None


# ---- the translation draft ----------------------------------------------------------------------------------------------------------------


class FakeTranslation:
    def __init__(self, raises=None):
        self.raises, self.requests = raises, []

    async def translate(self, request):
        self.requests.append(request)
        if self.raises:
            raise self.raises
        return AdapterResult(TranslationResult("TAMIL-FAKE", request.source, request.target), Usage(0, 0))


@pytest.mark.asyncio
async def test_the_translation_draft_is_a_suggestion_with_a_named_status_and_is_always_synthetic():
    fake = FakeTranslation()
    got = await demo_real_models.suggest_translation(fake)
    assert got.status == "suggested" and got.text == "TAMIL-FAKE" and got.label == "DEMO ONLY - NOT A CLINICAL DECISION" and got.characters == len(demo_real_models.DRAFT_EN)
    (request,) = fake.requests
    assert request.data_class is DataClass.SYNTHETIC and request.text == demo_real_models.DRAFT_EN and "DEMO DRAFT, NOT SENT" in request.text
    assert (await demo_real_models.suggest_translation(None)).status == "unavailable"
    assert (await demo_real_models.suggest_translation(FakeTranslation(SpendCeilingReached("c")))).status == "refused"
    failed = await demo_real_models.suggest_translation(FakeTranslation(PermanentError("x")))
    assert failed.status == "failed" and failed.text is None
    assert "TAMIL-FAKE" not in repr(got)  # the words never reach a repr


# ---- the dummy letter ----------------------------------------------------------------------------------------------------------------------


def test_the_dummy_letter_is_invented_tokens_a_text_layer_pdf_and_a_deterministic_scan():
    from pypdf import PdfReader

    pdf, ctype = synthetic_letter.letter_pdf()
    assert ctype == "application/pdf" and pdf.startswith(b"%PDF-")
    read = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "PATIENT: DEMO KILO SAMPLE, DOB 1990-01-01" in read
    png, ctype = synthetic_letter.letter_scan()
    assert ctype == "image/png" and png[:8] == b"\x89PNG\r\n\x1a\n" and png == synthetic_letter.letter_scan()[0]  # the same bytes every run
    assert png != synthetic_letter.png_bytes(synthetic_letter.letter_lines(), scale=synthetic_letter.LETTER_SCALE)  # the scan-style page is not the clean one
    assert "ZZDEMOALPHA" in " ".join(synthetic_letter.letter_lines(flagged=True)) and "ZZDEMOALPHA" not in " ".join(synthetic_letter.letter_lines())
    assert "DEMO" in synthetic_letter.LETTER_NAME and len(synthetic_letter.png_bytes()) < 2000  # the live check's own page is unchanged in kind
    with pytest.raises(ValueError):
        synthetic_letter.pdf_bytes(("lowercase is outside the font",))


def test_the_pdf_has_a_valid_cross_reference_table():
    pdf, _ = synthetic_letter.letter_pdf()
    start = int(re.search(rb"startxref\n(\d+)\n", pdf).group(1))
    assert pdf[start : start + 4] == b"xref"
    offsets = [int(m) for m in re.findall(rb"(\d{10}) 00000 n", pdf)]
    assert [pdf[o : o + 7].split(b" ")[0] for o in offsets] == [b"1", b"2", b"3", b"4", b"5"]


# ---- the script ----------------------------------------------------------------------------------------------------------------------------


def load_script():
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("demo_real_models_script", ROOT / "scripts" / "demo_real_models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_order_check_accepts_only_text_then_rules_then_decision():
    script = load_script()
    assert script.order_is_safe(["text", "rules", "decision"]) and script.order_is_safe(["text", "rules"]) and script.order_is_safe(["rules"])
    assert not script.order_is_safe(["decision", "rules"]) and not script.order_is_safe(["text", "decision"]) and not script.order_is_safe(["decision"])


def test_the_script_refuses_outside_dev_demo_synthetic_before_any_connection_and_prints_no_secret(tmp_path, monkeypatch, capsys):
    script = load_script()
    cases = {
        "clinical": "CAREFLOW_ENV=dev\nCAREFLOW_PROFILE=clinical\nCAREFLOW_DATA_POLICY=synthetic_only\n",
        "not-dev": "CAREFLOW_ENV=ci\nCAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=synthetic_only\n",
        "not-synthetic": "CAREFLOW_ENV=dev\nCAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=phi_approved\n",
        "no-urls": "CAREFLOW_ENV=dev\nCAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=synthetic_only\nSOME_OTHER_SECRET=SECRET-NEVER-PRINTED\n",
    }
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("asked for the host before refusing")))
    monkeypatch.setattr("sys.argv", ["demo_real_models.py", "--clinic-id", str(uuid.uuid4()), "--env-file", ""])
    for name, content in cases.items():
        env = tmp_path / f"{name}.env"
        env.write_text(content, encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["demo_real_models.py", "--clinic-id", str(uuid.uuid4()), "--env-file", str(env)])
        assert asyncio.run(script.main()) == 1, name
    out = capsys.readouterr().out
    assert out.count("refusing") == 4 and "SECRET-NEVER-PRINTED" not in out


def test_the_script_prints_only_metadata_never_a_letters_text_a_key_or_a_model_words():
    source = (ROOT / "scripts" / "demo_real_models.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "_API_KEY" not in code and "KEY\"" not in code  # the script never names or reads a provider key itself
    assert not re.search(r"say\([^)]*((?<!up)\.text\b|page\b|letter_text|answer)", code.replace("page:", "").replace("len(page)", ""))  # nothing printed is a letter or an answer (`up.text` is the stage's fixed-words label)
    assert "SWITCH" in code and "worker_context" in code and "order_is_safe" in code


def test_the_app_upgrades_only_under_the_gate_and_before_the_handlers_are_registered():
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    at = source.index("demo_real_models.allowed(os.environ, resolve_profile())")
    assert at < source.index("handlers = {FRONT_DOOR_TASK") and "demo_real_models.upgrade_components" in source[at : at + 900]
    assert "demo_real_models.allowed(" in source[at - 100 : at + 60] and "components is not None" in source[at - 60 : at + 40]



# ---- the live run of 2026-11-14: stage 1 took 123 s, ended extraction_unavailable, and the rules looked "clear" -------------------------------------------------------



from careflow.adapters.gemini import GeminiVisionAdapter
from careflow.gateway import (
    AllRoutesFailed,
    NoApprovedRoute,
    RoleDailyCeilings,
    RouteAttempt,
)
from careflow.ports import VisionRequest


def vision_gateway(adapter, *, paid=False, ceiling=Decimal(0), timeout=60.0):
    price = Decimal("0.001") if paid else Decimal(0)
    row = ModelRow("vlm", "gemini", "gemini-test-1", "synthetic_only", 1, price, price, Decimal("0.001"), Decimal("0.001"), timeout_seconds=timeout)
    return Gateway([row], {("gemini", "gemini-test-1"): adapter}, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=Decimal(1)), role_ceilings=RoleDailyCeilings({"vlm": ceiling}), origin="test", base_delay=0.0)


def page_request():
    return VisionRequest(synthetic_letter.letter_scan()[0], "image/png", 1, CLINIC)


@pytest.mark.asyncio
async def test_a_daily_ceiling_of_zero_does_not_refuse_a_free_vision_call_but_does_refuse_a_paid_one_and_says_so():
    log: list[str] = []
    free = vision_gateway(FakeVisionAdapter(log), ceiling=Decimal(0))
    got = await GatewayVision(free, data_class=DataClass.SYNTHETIC).read(page_request())  # the live run's ceiling was 0 and the row is free tier: a free call costs nothing, so no ceiling applies
    assert got.text and log == ["MODEL:vision"]
    paid_log: list[str] = []
    paid = GatewayVision(vision_gateway(FakeVisionAdapter(paid_log), paid=True, ceiling=Decimal(0)), data_class=DataClass.SYNTHETIC)
    with pytest.raises(SpendCeilingReached) as caught:
        await paid.read(page_request())
    assert paid_log == []  # no call was made
    assert demo_real_models.failure_reason(caught.value) == "budget refused: a spend ceiling was reached"


@pytest.mark.asyncio
async def test_a_provider_that_never_answers_is_three_timeouts_and_the_probe_says_exactly_that():
    class Hangs:
        calls = 0

        async def __call__(self, request):
            Hangs.calls += 1
            await asyncio.sleep(5)

    gateway = vision_gateway(Hangs(), timeout=0.05)
    probe = demo_real_models.VisionProbe(GatewayVision(gateway, data_class=DataClass.SYNTHETIC))
    started = __import__("time").monotonic()
    with pytest.raises(AllRoutesFailed):
        await probe.read(page_request())
    assert __import__("time").monotonic() - started < 2 and Hangs.calls == 3  # the gateway makes up to THREE attempts per route (spec 16.1) and the breaker only opens after ten failures: a hanging provider costs 3 x the row's timeout (3 x 60 s = 180 s in life)
    assert probe.calls == 1 and probe.reason == "timed out x3"
    assert "gemini" not in probe.reason and "https" not in probe.reason and KEY not in probe.reason


@pytest.mark.asyncio
async def test_a_provider_error_status_is_named_in_fixed_words_without_the_key_or_the_url():
    adapter = GeminiVisionAdapter(KEY, "gemini-3.5-flash-lite", timeout_seconds=60, http=Mock(json_reply({"error": {"message": f"echo {KEY} https://generativelanguage.googleapis.com"}}, 404)).client())
    probe = demo_real_models.VisionProbe(GatewayVision(vision_gateway_for(adapter), data_class=DataClass.SYNTHETIC))
    with pytest.raises(AllRoutesFailed):
        await probe.read(page_request())
    assert probe.reason == "provider refused the request [the provider refused the request (status 404)]"
    assert KEY not in probe.reason and "https" not in probe.reason and "generativelanguage" not in probe.reason
    adapter = GeminiVisionAdapter(KEY, "gemini-3.5-flash-lite", timeout_seconds=60, http=Mock(json_reply({}, 503)).client())
    probe = demo_real_models.VisionProbe(GatewayVision(vision_gateway_for(adapter), data_class=DataClass.SYNTHETIC))
    with pytest.raises(AllRoutesFailed):
        await probe.read(page_request())
    assert probe.reason == "provider error (retry-worthy) x3 [the provider answered with a retry-worthy status (503)]"


def vision_gateway_for(adapter):
    row = ModelRow("vlm", "gemini", "gemini-3.5-flash-lite", "synthetic_only", 1, Decimal(0), Decimal(0), Decimal("0.001"), Decimal("0.001"), timeout_seconds=60.0)
    return Gateway([row], {("gemini", "gemini-3.5-flash-lite"): adapter}, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=Decimal(1)), origin="test", base_delay=0.0)


def test_every_failure_has_a_fixed_words_reason_and_an_unknown_one_says_so():
    reason = demo_real_models.failure_reason
    assert reason(NoApprovedRoute("vlm", DataClass.SYNTHETIC)) == "gate missing: no approved route for this data"
    assert reason(SpendCeilingReached("x")).startswith("budget refused")
    assert reason(AllRoutesFailed("vlm", (RouteAttempt("p", "m", "skipped_spend"),))) == "budget refused (a spend ceiling)"
    assert reason(AllRoutesFailed("vlm", (RouteAttempt("p", "m", "skipped_no_adapter"),))) == "gate missing (no adapter built)"
    assert reason(AllRoutesFailed("vlm", (RouteAttempt("p", "m", "timeout", 2), RouteAttempt("p2", "m2", "skipped_breaker_open")))) == "timed out x2; route paused after repeated failures"
    mixed = RouteAttempt("p", "m", "failed_transient", 3, ("timeout", "timeout", "failed_transient"), "the provider answered with a retry-worthy status (429)")  # the live run's likely shape: two timeouts, then a quick refusal
    assert reason(AllRoutesFailed("vlm", (mixed,))) == "timed out x2, then provider error (retry-worthy) [the provider answered with a retry-worthy status (429)]"
    assert reason(AllRoutesFailed("vlm", ())) == "no route was tried"
    assert reason(PermanentError("the provider blocked this page")) == "the provider blocked this page"
    assert reason(RuntimeError("secret " + KEY)) == "unexpected error"  # an unknown failure never echoes its message


@pytest.mark.asyncio
async def test_a_page_that_was_not_read_is_never_shown_as_read_and_the_rules_are_never_shown_as_clear():
    script = load_script()
    log: list[str] = []
    up = demo_real_models.upgrade_components(base_components(log), providers_for(FakeVisionAdapter(log, raises=PermanentError("the provider refused the request (status 404)")), None), loader=loader, environ=ON, profile="demo")
    assert up.vision_probe is not None
    from careflow.front_door import (
        FrontDoorDeps,
        build_front_door,
        initial_state,
        run_config,
    )

    conn = Conn(log=log)

    @asynccontextmanager
    async def session():
        yield conn

    c = up.components
    deps = FrontDoorDeps(session=session, engine=c.engine, texts=c.texts, extractor=c.extractor, ingestor=c.ingestor, retriever=c.retriever, proposer=c.proposer, recorder=c.recorder, holds=c.holds, retrieval_scope=dict(c.retrieval_scope), profile="demo")
    graph = build_front_door(deps, InMemorySaver(), environ={})
    with worker_context():
        await graph.ainvoke(initial_state(DOC, CLINIC, "test"), run_config(DOC, CLINIC, "test"))
    state = (await graph.aget_state(run_config(DOC, CLINIC, "test"))).values
    assert state["status"] == "extraction_unavailable" and "rules" not in log
    events = [e for e in log if e in ("rules",)]
    t_status, t_note = script.text_stage(True, events, up.vision_probe, state["status"])
    assert t_status == "NOT READ" and "provider refused the request" in t_note and "status 404" in t_note
    r_status, r_note = script.rules_stage(events, False, state["status"])
    assert r_status == "NOT RUN" and "NOT a clear" in r_note  # the live run reported the rules as clear: they never ran
    d_status, _ = script.decision_stage(up.ranker.last if up.ranker else None, state["status"])
    assert d_status == "not run" or d_status == "not asked"
    assert script.run_reasons(state) and all(r.islower() for r in script.run_reasons(state))


def test_the_stage_words_for_a_good_run_a_flagged_run_an_earlier_run_and_an_unassessable_run():
    script = load_script()
    assert script.text_stage(True, ["text", "rules"], None, "triage_proposed") == ("read", "")
    assert script.text_stage(False, [], None, "triage_proposed")[0] == "not run (earlier run)"
    assert script.rules_stage(["text", "rules"], False, "triage_proposed")[0] == "clear" and script.rules_stage(["text", "rules"], True, "red_flag_escalated")[0] == "FIRED"
    assert script.rules_stage(["text", "rules"], False, "held_unassessable")[0] == "unassessable"
    trace = demo_real_models.DecisionTrace("unavailable", reason="timed out x2")
    assert script.decision_stage(trace, "triage_proposed") == ("unavailable", "timed out x2")
    assert script.decision_stage(demo_real_models.DecisionTrace(), "red_flag_escalated")[0] == "not asked"


def test_fresh_makes_a_new_document_key_every_time_and_without_it_the_key_is_fixed():
    script = load_script()
    fixed = {script.document_key("scan", False, False) for _ in range(5)}
    assert fixed == {"real-models-demo:scan:clean"}
    fresh = {script.document_key("scan", False, True) for _ in range(5)}
    assert len(fresh) == 5 and all(k.startswith("real-models-demo:scan:clean:fresh-") for k in fresh) and not (fresh & fixed)
    assert script.document_key("pdf", True, False) == "real-models-demo:pdf:flagged"
    source = (ROOT / "scripts" / "demo_real_models.py").read_text(encoding="utf-8")
    assert "--fresh" in source and "document_key(kind, flagged, fresh)" in source


def test_the_scan_style_page_is_small_like_the_live_checks_page_and_the_gemini_request_bounds_its_answer():
    page, ctype = synthetic_letter.letter_scan()
    assert ctype == "image/png" and len(page) <= synthetic_letter.MAX_SCAN_BYTES  # the first version was 113,677 bytes (about 150 times the live check's page); the live run timed out twice on it
    assert page == synthetic_letter.letter_scan()[0] and page != synthetic_letter.png_bytes(synthetic_letter.letter_lines(), scale=synthetic_letter.LETTER_SCALE)
    from careflow.adapters.gemini import MAX_OUTPUT_TOKENS

    assert 1024 <= MAX_OUTPUT_TOKENS <= 65536
    mock = Mock(json_reply({"candidates": [{"content": {"parts": [{"text": "TEXT"}]}, "finishReason": "STOP"}]}))
    asyncio.run(GeminiVisionAdapter(KEY, "gemini-3.5-flash-lite", timeout_seconds=60, http=mock.client())(page_request()))
    body = __import__("json").loads(mock.requests[0].content)
    assert body["generationConfig"] == {"temperature": 0, "maxOutputTokens": MAX_OUTPUT_TOKENS}



# ---- what a real decision leaves on the proposal, and the staff request list ------------------------------------------------------------------------------------------


from careflow import decision_info
from careflow.review_console import build_reviewer_view


def test_only_closed_decision_fields_can_be_stored_and_free_text_never_can():
    clean = decision_info.clean_extra({"probability": 0.8, "engine": "jev", "model_id": "typesafe/jev-router", "confidence": 0.8, "rule": "chosen_option_ranks_first", "text": "the whole letter", "note": "x"})
    assert clean == {"probability": 0.8, "engine": "jev", "model_id": "typesafe/jev-router", "confidence": 0.8, "rule": "chosen_option_ranks_first"}  # unknown keys are dropped
    for bad in ({"probability": 1.5}, {"probability": -0.1}, {"probability": float("nan")}, {"probability": True}, {"probability": "0.5"}, {"engine": "Has Spaces And Text!"}, {"engine": "x" * 41}, {"rule": "a free sentence about a patient"}, {"model_id": "bad model"}, {"model_id": "m" * 101}):
        assert decision_info.clean_extra(bad) == {}, bad  # a malformed field is dropped, never stored
    assert decision_info.candidate_entry({"card_id": "demo-card-a", "version": "2", "probability": 0.4, "letter": "text"}) == {"card_id": "demo-card-a", "version": 2, "probability": 0.4}
    assert decision_info.proposal_info([]) == {"engine": None, "model_id": None, "confidence": None, "rule": None}  # not recorded
    assert decision_info.proposal_info([{"card_id": "a", "version": 1}])["engine"] is None
    assert decision_info.proposal_info([{"card_id": "a", "version": 1, "engine": "jev", "model_id": "m-1", "confidence": 0.7, "rule": "chosen_option_ranks_first"}]) == {"engine": "jev", "model_id": "m-1", "confidence": 0.7, "rule": "chosen_option_ranks_first"}


def test_the_reviewer_view_shows_each_alternatives_probability_and_the_engine_and_confidence_or_none_when_not_recorded():
    stored = [{"card_id": "demo-card-b", "version": 1, "probability": 0.8, "engine": "jev", "model_id": "m-1", "confidence": 0.8, "rule": "chosen_option_ranks_first"}, {"card_id": "demo-card-a", "version": 1, "probability": 0.2}]
    info = decision_info.proposal_info(stored)
    view = build_reviewer_view(task_id="t", kind="triage_proposal", origin="test", candidates=stored, abstained=False, mode="shadow", engine=info["engine"], confidence=info["confidence"]).for_reviewer()
    assert view["engine"] == "jev" and view["confidence"] == 0.8 and [(a["label"], a["probability"]) for a in view["alternatives"]] == [("demo-card-b@1", 0.8), ("demo-card-a@1", 0.2)]
    old = [{"card_id": "demo-card-a", "version": 1}]  # a proposal stored before this: not recorded, never shown as 0 or as high
    view = build_reviewer_view(task_id="t", kind="triage_proposal", origin="test", candidates=old, abstained=False, mode="shadow", **{k: v for k, v in decision_info.proposal_info(old).items() if k in ("engine", "confidence")}).for_reviewer()
    assert view["engine"] is None and view["confidence"] is None and view["alternatives"][0]["probability"] is None


def test_the_proposal_writer_and_the_graph_keep_only_the_closed_fields():
    store = (ROOT / "careflow" / "triage_store.py").read_text(encoding="utf-8")
    assert "ids = [candidate_entry(c) for c in candidates]" in store
    front = (ROOT / "careflow" / "front_door.py").read_text(encoding="utf-8")
    assert "**clean_extra(c.extra)" in front
    app_source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    assert "proposal_info(p.candidates)" in app_source and "engine=info[\"engine\"], confidence=info[\"confidence\"]" in app_source


def test_the_staff_request_list_says_which_statuses_may_come_next_exactly_as_the_database_accepts_them():
    from careflow import portal_extras_sql

    assert portal_extras_sql.allowed_next("open") == ["acknowledged", "resolved", "declined"]
    assert portal_extras_sql.allowed_next("acknowledged") == ["resolved", "declined"]
    assert portal_extras_sql.allowed_next("resolved") == [] and portal_extras_sql.allowed_next("declined") == [] and portal_extras_sql.allowed_next("surprise") == []
    sql = (ROOT / "db" / "migrations" / "0080_patient_portal_profile_notifications_requests_public_grievance.sql").read_text(encoding="utf-8")
    assert "(v_r.status = 'open' and p_status in ('acknowledged', 'resolved', 'declined')) or (v_r.status = 'acknowledged' and p_status in ('resolved', 'declined'))" in sql  # the table above is that rule
    app_source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    assert "allowed_next=portal_extras_sql.allowed_next(r.status)" in app_source and "allowed_next: list[Literal[\"acknowledged\", \"resolved\", \"declined\"]]" in app_source
