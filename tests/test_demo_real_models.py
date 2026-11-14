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
    assert state.values["candidates"][0] == {"card_id": "demo-synthetic-card-B", "version": 1}  # the model only REORDERED the fictional cards
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
    assert not re.search(r"say\([^)]*((?<!up)\.text\b|page\b|letter_text|answer)", code.replace("page:", ""))  # nothing printed is a letter or an answer (`up.text` is the stage's fixed-words label)
    assert "SWITCH" in code and "worker_context" in code and "order_is_safe" in code


def test_the_app_upgrades_only_under_the_gate_and_before_the_handlers_are_registered():
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    at = source.index("demo_real_models.allowed(os.environ, resolve_profile())")
    assert at < source.index("handlers = {FRONT_DOOR_TASK") and "demo_real_models.upgrade_components" in source[at : at + 900]
    assert "demo_real_models.allowed(" in source[at - 100 : at + 60] and "components is not None" in source[at - 60 : at + 40]
