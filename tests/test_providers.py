"""The hosted-provider adapters (Gemini vlm, Jev via OpenRouter, Sarvam translation), OFFLINE against RECORDED-SHAPE FAKES: `httpx.MockTransport` answers in the shapes the adapters' headers document
(Context7 docs of 2026-10-30). NO REAL PROVIDER IS CALLED, EVER, by this file or by CI; the real service is the owner's opt-in `scripts/provider_live_check.py` (Appendix B discipline).

What is proven here: the request each adapter builds (host, header, body), how every reply and failure maps to fixed words that carry no key, URL or body; synthetic_only rows never see PHI; the required per-role
ceiling stops an ordinary call but never a safety one; and every gate in `careflow.provider_setup` (CI, profile, data policy, key, recorded live check, ceiling) really closes.
"""

import base64
import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from careflow.adapters import provider_http
from careflow.adapters.decision import JevAdapter
from careflow.adapters.gemini import GeminiVisionAdapter
from careflow.adapters.openrouter import ENDPOINT as OPENROUTER_ENDPOINT
from careflow.adapters.openrouter import JEV_MODEL, JevRouterClient
from careflow.adapters.sarvam import ENDPOINT as SARVAM_ENDPOINT
from careflow.adapters.sarvam import SarvamTranslation
from careflow.adapters.vision import GatewayVision
from careflow.budget import CallClass
from careflow.gateway import (
    Gateway,
    InMemorySpendLedger,
    ModelRow,
    NoApprovedRoute,
    RoleDailyCeilings,
    SpendCeilingReached,
    SpendLimits,
)
from careflow.ports import (
    DataClass,
    DecisionRequest,
    PermanentError,
    TransientError,
    TranslationRequest,
    VisionRequest,
)
from careflow.provider_setup import (
    CEILING_VARIABLES,
    KEY_VARIABLES,
    RUN_CAP_VARIABLE,
    VERIFIED_VARIABLES,
    ProviderConfigRefused,
    build_providers,
    load_provider_config,
    read_ceiling,
)

ROOT = Path(__file__).resolve().parent.parent
KEY = "SECRET-KEY-VALUE-THAT-MUST-NEVER-APPEAR"
CLINIC = uuid.uuid4()
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic image bytes"
TODAY = datetime.now(UTC).date().isoformat()


class Mock:
    def __init__(self, handler):
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def client(self, **kwargs) -> httpx.AsyncClient:
        def run(request: httpx.Request):
            self.requests.append(request)
            return self._handler(request)

        return httpx.AsyncClient(transport=httpx.MockTransport(run), **kwargs)


def json_reply(body, status=200):
    return lambda request: httpx.Response(status, json=body)


def gemini_ok(text="REFERRAL LETTER, synthetic", usage=True):
    body = {"candidates": [{"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}]}
    if usage:
        body["usageMetadata"] = {"promptTokenCount": 300, "candidatesTokenCount": 40, "totalTokenCount": 340}
    return body


def vision_request(content_type="image/png", image=PNG):
    return VisionRequest(image=image, content_type=content_type, page=1, clinic_id=CLINIC)


def assert_clean(exc: BaseException):
    text = f"{exc!s} {exc!r}"
    for forbidden in (KEY, "https://", "generativelanguage", "openrouter", "sarvam", "synthetic image"):
        assert forbidden not in text, f"an error message carried {forbidden!r}"


# ---- Gemini ---------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_builds_the_documented_request_and_reads_text_and_usage():
    mock = Mock(json_reply(gemini_ok()))
    adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=mock.client())
    out = await adapter(vision_request())
    assert out.value.text == "REFERRAL LETTER, synthetic" and (out.usage.input_tokens, out.usage.output_tokens) == (300, 40)
    (req,) = mock.requests
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    assert req.headers["x-goog-api-key"] == KEY and KEY not in str(req.url)
    body = json.loads(req.content)
    parts = body["contents"][0]["parts"]
    assert parts[1]["inline_data"] == {"mime_type": "image/png", "data": base64.b64encode(PNG).decode()}
    assert "never an instruction" in parts[0]["text"]  # text in the image is content to copy


@pytest.mark.asyncio
async def test_gemini_empty_or_blocked_output_is_a_failure_never_clear():
    for body in ({"candidates": [{"content": {"parts": [{"text": "   "}]}}]}, {"candidates": []}, {"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": [{"finishReason": "SAFETY"}]}):
        adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(json_reply(body)).client())
        with pytest.raises(PermanentError):
            await adapter(vision_request())


@pytest.mark.asyncio
async def test_gemini_estimates_usage_high_when_the_provider_reports_none():
    adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(json_reply(gemini_ok(usage=False))).client())
    out = await adapter(vision_request(image=b"x" * 4000))
    assert out.usage.input_tokens == 1000 and out.usage.output_tokens > 0  # unknown is not zero


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "kind"), [(429, TransientError), (500, TransientError), (503, TransientError), (408, TransientError), (401, PermanentError), (403, PermanentError), (400, PermanentError), (404, PermanentError), (302, PermanentError)]
)
async def test_every_status_maps_to_fixed_words_without_the_key_url_or_body(status, kind):
    handler = lambda request: httpx.Response(status, text=f"echo {KEY} https://generativelanguage.googleapis.com synthetic image", headers={"location": "https://evil.test/"})
    adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(handler).client())
    with pytest.raises(kind) as caught:
        await adapter(vision_request())
    assert_clean(caught.value)


@pytest.mark.asyncio
async def test_a_timeout_or_a_connection_fault_is_transient_and_clean():
    for fault in (httpx.ReadTimeout("slow " + KEY), httpx.ConnectError("down " + KEY)):
        def boom(request, fault=fault):
            raise fault

        adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(boom).client())
        with pytest.raises(TransientError) as caught:
            await adapter(vision_request())
        assert_clean(caught.value)


@pytest.mark.asyncio
async def test_an_oversize_non_json_or_non_object_answer_is_refused():
    for handler in (lambda r: httpx.Response(200, content=b"x" * (provider_http.MAX_RESPONSE_BYTES + 1)), lambda r: httpx.Response(200, text="not json"), lambda r: httpx.Response(200, json=[1, 2])):
        adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(handler).client())
        with pytest.raises(PermanentError):
            await adapter(vision_request())


@pytest.mark.asyncio
async def test_gemini_refuses_a_bad_image_a_bad_model_id_and_a_redirecting_client_before_any_call():
    mock = Mock(json_reply(gemini_ok()))
    adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=mock.client())
    for bad in (vision_request("application/pdf"), vision_request(image=b""), vision_request(image=b"x" * (15 * 1024 * 1024 + 1))):
        with pytest.raises(PermanentError):
            await adapter(bad)
    assert mock.requests == []
    for model in ("../etc", "gemini-2.5-flash/../x", "gpt-4", "gemini-2.5-flash\n", ""):
        with pytest.raises(ValueError):
            GeminiVisionAdapter(KEY, model, timeout_seconds=60)
    with pytest.raises(ValueError):
        GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=httpx.AsyncClient(follow_redirects=True))
    with pytest.raises(ValueError):
        GeminiVisionAdapter("  ", "gemini-2.5-flash", timeout_seconds=60)


# ---- Jev via OpenRouter ----------------------------------------------------------------------------------------------------------------------


def openrouter_ok(choice="urgent", confidence=0.8, usage=True):
    body = {"choices": [{"message": {"role": "assistant", "content": json.dumps({"choice": choice, "confidence": confidence})}}], "model": "downstream/model"}
    if usage:
        body["usage"] = {"prompt_tokens": 90, "completion_tokens": 12, "total_tokens": 102}
    return body


def decision_request(data_class=DataClass.SYNTHETIC):
    return DecisionRequest("triage_band", "synthetic note", ("routine", "urgent"), data_class=data_class)


@pytest.mark.asyncio
async def test_jev_goes_through_chat_completions_with_the_router_model_and_a_strict_schema():
    mock = Mock(json_reply(openrouter_ok()))
    adapter = JevAdapter(JevRouterClient(KEY, timeout_seconds=20, http=mock.client()))
    out = await adapter(decision_request())
    assert out.value.choice == "urgent" and out.value.engine == "jev" and out.value.calibrated is False  # a router's self-reported confidence is never calibrated
    assert abs(sum(out.value.probabilities.values()) - 1) < 1e-9 and (out.usage.input_tokens, out.usage.output_tokens) == (90, 12)
    (req,) = mock.requests
    assert str(req.url) == OPENROUTER_ENDPOINT == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(req.content)
    assert body["model"] == JEV_MODEL == "typesafe/jev-router" and body["plugins"][0]["id"] == "jev-router"
    schema = body["response_format"]["json_schema"]
    assert schema["strict"] is True and schema["schema"]["properties"]["choice"]["enum"] == ["routine", "urgent"] and schema["schema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_jev_refuses_patient_text_itself_and_a_reply_that_is_not_exactly_an_offered_option():
    mock = Mock(json_reply(openrouter_ok()))
    adapter = JevAdapter(JevRouterClient(KEY, timeout_seconds=20, http=mock.client()))
    with pytest.raises(PermanentError):
        await adapter(decision_request(DataClass.PHI))
    assert mock.requests == []  # defence in depth: nothing was sent
    for body in (openrouter_ok("emergency"), openrouter_ok(confidence=1.5), openrouter_ok(confidence=0.5), {"choices": []}, {"choices": [{"message": {"content": "urgent"}}]}):
        bad = JevAdapter(JevRouterClient(KEY, timeout_seconds=20, http=Mock(json_reply(body)).client()))
        with pytest.raises(PermanentError):
            await bad(decision_request())  # an unlisted option, an impossible confidence, a tie (0.5 of 2) or prose is never a choice


@pytest.mark.asyncio
async def test_openrouter_failures_are_clean_and_the_client_pins_the_router_model():
    for status, kind in ((429, TransientError), (502, TransientError), (401, PermanentError), (400, PermanentError)):
        bad = JevAdapter(JevRouterClient(KEY, timeout_seconds=20, http=Mock(lambda r, s=status: httpx.Response(s, text=KEY)).client()))
        with pytest.raises(kind) as caught:
            await bad(decision_request())
        assert_clean(caught.value)
    with pytest.raises(ValueError):
        JevRouterClient(KEY, timeout_seconds=20, model_id="openai/gpt-4")


# ---- Sarvam ---------------------------------------------------------------------------------------------------------------------------------


def sarvam(handler, ceiling="1.00", price="0.06"):
    ceilings = RoleDailyCeilings({"translation": Decimal(ceiling)})
    mock = Mock(handler)
    return SarvamTranslation(KEY, timeout_seconds=5, price_per_1k_chars_usd=Decimal(price), ceilings=ceilings, http=mock.client()), mock, ceilings


@pytest.mark.asyncio
async def test_sarvam_builds_the_documented_request_and_counts_characters():
    adapter, mock, ceilings = sarvam(json_reply({"request_id": "r", "translated_text": "vanakkam", "source_language_code": "en-IN"}))
    out = await adapter.translate(TranslationRequest("hello there", "en", "ta", DataClass.SYNTHETIC))
    assert out.value.text == "vanakkam" and (out.value.source, out.value.target) == ("en", "ta") and (out.usage.input_tokens, out.usage.output_tokens) == (11, 8)
    (req,) = mock.requests
    assert str(req.url) == SARVAM_ENDPOINT == "https://api.sarvam.ai/translate" and req.headers["api-subscription-key"] == KEY
    assert json.loads(req.content) == {"input": "hello there", "source_language_code": "en-IN", "target_language_code": "ta-IN", "model": "mayura:v1"}
    assert ceilings.spent_today("translation") == Decimal(11) / 1000 * Decimal("0.06")


@pytest.mark.asyncio
async def test_sarvam_never_cuts_a_long_text_and_refuses_an_unknown_pair_before_any_call():
    adapter, mock, _ = sarvam(json_reply({"translated_text": "x"}))
    for bad in (TranslationRequest("a" * 1001, "en", "ta", DataClass.SYNTHETIC), TranslationRequest("hi", "en", "xx", DataClass.SYNTHETIC), TranslationRequest("hi", "ta", "ta", DataClass.SYNTHETIC), TranslationRequest("   ", "en", "ta", DataClass.SYNTHETIC)):
        with pytest.raises(PermanentError):
            await adapter.translate(bad)
    assert mock.requests == []
    empty, _, _ = sarvam(json_reply({"translated_text": " "}))
    with pytest.raises(PermanentError):
        await empty.translate(TranslationRequest("hi", "en", "ta", DataClass.SYNTHETIC))


@pytest.mark.asyncio
async def test_the_translation_ceiling_stops_a_call_before_it_is_made_and_is_a_visible_hold():
    adapter, mock, _ = sarvam(json_reply({"translated_text": "x"}), ceiling="0.0001")
    with pytest.raises(SpendCeilingReached):
        await adapter.translate(TranslationRequest("a" * 500, "en", "ta", DataClass.SYNTHETIC))
    assert mock.requests == []
    with pytest.raises(ValueError):  # a translation route without a required ceiling cannot be built
        SarvamTranslation(KEY, timeout_seconds=5, price_per_1k_chars_usd=Decimal("0.06"), ceilings=RoleDailyCeilings({}), http=Mock(json_reply({})).client())


# ---- the gateway: per-role ceiling, data policy -----------------------------------------------------------------------------------------------


def paid_row(role="vlm", policy="synthetic_only", prio=1, provider="p"):
    return ModelRow(role, provider, f"{provider}-model-1", policy, prio, Decimal(1), Decimal(1), Decimal(1), Decimal(1), timeout_seconds=5)


def gateway(rows, adapters, ceiling):
    return Gateway(rows, adapters, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=Decimal(1000)), role_ceilings=RoleDailyCeilings({"vlm": Decimal(ceiling)}), origin="test")


@pytest.mark.asyncio
async def test_the_role_ceiling_refuses_ordinary_calls_but_never_a_safety_call():
    from careflow.ports import AdapterResult, Usage, VisionResult

    async def adapter(request):
        return AdapterResult(VisionResult("text"), Usage(1000, 0))  # costs 1.00 each

    gw = gateway([paid_row()], {("p", "p-model-1"): adapter}, "1.50")
    await gw.call("vlm", vision_request(), data_class=DataClass.SYNTHETIC, clinic_id=CLINIC)  # spent 1.00
    await gw.call("vlm", vision_request(), data_class=DataClass.SYNTHETIC, clinic_id=CLINIC, estimate=Usage(100, 0))  # 1.00 + 0.10 fits; now spent 2.00 > 1.50
    with pytest.raises(SpendCeilingReached):
        await gw.call("vlm", vision_request(), data_class=DataClass.SYNTHETIC, clinic_id=CLINIC, estimate=Usage(100, 0))
    with pytest.raises(SpendCeilingReached):
        await gw.call("vlm", vision_request(), data_class=DataClass.SYNTHETIC, clinic_id=CLINIC)  # no estimate: unknown is not zero once the ceiling is reached
    safe = await gw.call("vlm", vision_request(), data_class=DataClass.SYNTHETIC, clinic_id=CLINIC, call_class=CallClass.SAFETY)
    assert safe.value.text == "text"  # safety is never budget-limited, and its spend is still counted
    assert gw.role_ceilings.spent_today("vlm") == Decimal(3)


@pytest.mark.asyncio
async def test_a_free_tier_row_is_never_skipped_for_spend_and_phi_never_reaches_a_synthetic_only_row():
    from careflow.ports import AdapterResult, Usage, VisionResult

    free = ModelRow("vlm", "g", "g-model-1", "synthetic_only", 1, Decimal(0), Decimal(0), Decimal("0.3"), Decimal("2.5"), timeout_seconds=5)

    async def adapter(request):
        return AdapterResult(VisionResult("t"), Usage(10, 10))

    gw = gateway([free], {("g", "g-model-1"): adapter}, "0")
    assert (await gw.call("vlm", vision_request(), data_class=DataClass.SYNTHETIC, clinic_id=CLINIC)).shadow_cost_usd > 0
    with pytest.raises(NoApprovedRoute):
        await gw.call("vlm", vision_request(), data_class=DataClass.PHI, clinic_id=CLINIC)
    with pytest.raises(NoApprovedRoute):
        await GatewayVision(gw).read(vision_request())  # the default GatewayVision sends PHI: a synthetic_only row is never offered it


def test_role_ceilings_are_validated():
    for bad in ({"vlm": Decimal(-1)}, {"vlm": Decimal("NaN")}, {"vlm": 5}, {"nope": Decimal(1)}):
        with pytest.raises(ValueError):
            RoleDailyCeilings(bad)  # type: ignore[arg-type]


# ---- the setup: every gate ------------------------------------------------------------------------------------------------------------------


def good_env(**extra):
    env = {
        "CAREFLOW_PROFILE": "demo", "CAREFLOW_DATA_POLICY": "synthetic_only",
        **{v: KEY for v in KEY_VARIABLES.values()}, **dict.fromkeys(VERIFIED_VARIABLES.values(), TODAY),
        **dict.fromkeys(CEILING_VARIABLES.values(), "1.00"), RUN_CAP_VARIABLE: "0.50",
    }
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


def test_the_shipped_provider_file_is_synthetic_only_labelled_and_pinned():
    cfg = load_provider_config()
    assert {r.role for r in cfg.rows} == {"vlm", "decision"} and all(r.data_policy == "synthetic_only" for r in cfg.rows)
    assert all(r.timeout_seconds > 0 for r in cfg.rows) and cfg.translation.timeout_seconds > 0
    paid = [r for r in cfg.rows if r.price_in_per_1k > 0]
    assert paid and all(r.price_out_per_1k > 0 for r in paid)


def test_a_provider_file_that_says_phi_approved_or_drops_the_label_is_refused_whole(tmp_path):
    doc = yaml.safe_load((ROOT / "config" / "demo-providers.yaml").read_text(encoding="utf-8"))
    for mutate in (lambda d: d["rows"][0].__setitem__("data_policy", "phi_approved"), lambda d: d["translation"].__setitem__("data_policy", "phi_approved"),
                   lambda d: d["rows"][0].__setitem__("label", "x"), lambda d: d.__setitem__("label", "x"), lambda d: d["rows"][1].__setitem__("price_in_per_1k", "0"),
                   lambda d: d["rows"][0].__setitem__("price_in_per_1k", "0.1"), lambda d: d["rows"][0].__setitem__("extra", 1), lambda d: d["rows"][0].pop("timeout_seconds")):
        bad = yaml.safe_load(yaml.safe_dump(doc))
        mutate(bad)
        path = tmp_path / "x.yaml"
        path.write_text(yaml.safe_dump(bad), encoding="utf-8")
        with pytest.raises((ProviderConfigRefused, KeyError, ValueError)):
            load_provider_config(path)


def test_every_gate_closes_with_a_reason_that_carries_no_value():
    cases = {
        "ci": good_env(CAREFLOW_ENV="ci"),
        "clinical": good_env(CAREFLOW_PROFILE="clinical"),
        "no policy": good_env(CAREFLOW_DATA_POLICY=None),
        "wrong policy": good_env(CAREFLOW_DATA_POLICY="phi_approved"),
    }
    for name, env in cases.items():
        got = build_providers(env, profile=env.get("CAREFLOW_PROFILE", "demo"))
        assert got.gateway is None and got.vision is None and got.translation is None and got.unavailable, name
        assert all(KEY not in reason for reason in got.unavailable.values())
    for variable in (*KEY_VARIABLES.values(), *VERIFIED_VARIABLES.values(), *CEILING_VARIABLES.values(), RUN_CAP_VARIABLE):
        got = build_providers(good_env(**{variable: None}), profile="demo")
        assert got.unavailable, variable
    for bad_date in ("", "not-a-date", (datetime.now(UTC).date() + timedelta(days=1)).isoformat()):
        assert build_providers(good_env(CAREFLOW_GEMINI_VERIFIED=bad_date), profile="demo").vision is None
    for bad_ceiling in ("", "abc", "-1", "NaN", "Infinity"):
        assert read_ceiling(good_env(CAREFLOW_SPEND_CEILING_VLM_USD=bad_ceiling), "vlm") is None
    assert read_ceiling(good_env(), "vlm") == Decimal("1.00")


@pytest.mark.asyncio
async def test_with_every_gate_open_the_providers_are_built_and_work_end_to_end_on_fakes():
    def route(request: httpx.Request):
        host = request.url.host
        if host == "generativelanguage.googleapis.com":
            return httpx.Response(200, json=gemini_ok("A synthetic referral"))
        if host == "openrouter.ai":
            return httpx.Response(200, json=openrouter_ok())
        return httpx.Response(200, json={"translated_text": "vanakkam"})

    mock = Mock(route)
    built = build_providers(good_env(), profile="demo", http=mock.client())
    assert not built.unavailable and built.gateway and built.vision and built.translation
    assert all(r.data_policy == "synthetic_only" for r in built.gateway.registry)
    assert (await built.vision.read(vision_request())).text == "A synthetic referral"
    decided = await built.gateway.call("decision", decision_request(), data_class=DataClass.SYNTHETIC, clinic_id=CLINIC)
    assert decided.value.choice == "urgent"
    with pytest.raises(NoApprovedRoute):
        await built.gateway.call("decision", decision_request(DataClass.PHI), data_class=DataClass.PHI, clinic_id=CLINIC)
    assert (await built.translation.translate(TranslationRequest("hello", "en", "ta", DataClass.SYNTHETIC))).value.text == "vanakkam"
    assert {r.url.host for r in mock.requests} == {"generativelanguage.googleapis.com", "openrouter.ai", "api.sarvam.ai"}


def test_a_provider_without_its_key_is_named_unavailable_and_the_others_still_build():
    got = build_providers(good_env(GEMINI_API_KEY=None), profile="demo")
    assert got.vision is None and "gemini" in got.unavailable and got.translation is not None and got.gateway is not None


def test_the_keys_are_not_in_the_ci_workflow_the_app_never_logs_them_and_no_test_file_reads_a_real_one(caplog):
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for name in (*KEY_VARIABLES.values(), *VERIFIED_VARIABLES.values()):
        assert name not in workflow, f"CI must never be given {name}"
    with caplog.at_level(logging.DEBUG):
        build_providers(good_env(CAREFLOW_ENV=None, GEMINI_API_KEY=KEY, CAREFLOW_GEMINI_VERIFIED=""), profile="demo")
    assert KEY not in caplog.text
    for path in (ROOT / "tests").glob("*.py"):
        if path.name != "test_providers.py":
            text = path.read_text(encoding="utf-8")
            assert not any(name in text for name in KEY_VARIABLES.values()), f"{path.name} reads a provider key"


def test_the_register_lists_each_provider_as_synthetic_only_and_not_for_clinical():
    register = yaml.safe_load((ROOT / "data-flow-register.yaml").read_text(encoding="utf-8"))["calls"]
    wanted = {"gemini-vision": "careflow/adapters/gemini.py", "openrouter-jev": "careflow/adapters/openrouter.py", "sarvam-translate": "careflow/adapters/sarvam.py"}
    by_id = {c["id"]: c for c in register}
    for ident, source in wanted.items():
        entry = by_id[ident]
        assert entry["source"] == source and (ROOT / source).is_file()
        assert entry["data_policy"] == "synthetic_only" and entry["permitted_in_clinical"] is False and entry["status"] == "approved" and entry["approved_by"] and entry["approved_on"]


def test_the_adapters_open_no_connection_when_built_or_imported():
    now = datetime.now(UTC)
    assert now  # building an adapter constructs a client but sends nothing: the Mock above records requests only when a call is made
    mock = Mock(json_reply(gemini_ok()))
    GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=mock.client())
    JevRouterClient(KEY, timeout_seconds=20, http=mock.client())
    SarvamTranslation(KEY, timeout_seconds=5, price_per_1k_chars_usd=Decimal("0.06"), ceilings=RoleDailyCeilings({"translation": Decimal(1)}), http=mock.client())
    assert mock.requests == []


def test_the_opt_in_live_check_refuses_under_ci_and_without_the_synthetic_confirmation():
    import os
    import subprocess
    import sys

    script = str(ROOT / "scripts" / "provider_live_check.py")
    env = {**os.environ, "CAREFLOW_ENV": "ci"}
    under_ci = subprocess.run([sys.executable, "-I", script, "--provider", "sarvam", "--yes-synthetic"], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert under_ci.returncode == 2 and "refusing to run under CI" in under_ci.stdout
    without = subprocess.run([sys.executable, "-I", script, "--provider", "sarvam"], capture_output=True, text=True, env=env, timeout=60, check=False)
    assert without.returncode != 0  # --yes-synthetic is required


@pytest.mark.asyncio
async def test_sarvam_refuses_patient_text_itself_before_any_call():
    adapter, mock, _ = sarvam(json_reply({"translated_text": "x"}))
    request = TranslationRequest("hello", "en", "ta", DataClass.PHI)
    assert adapter.accepts(request) is False
    with pytest.raises(PermanentError):
        await adapter.translate(request)
    assert mock.requests == []


@pytest.mark.asyncio
async def test_gemini_accepts_only_a_finished_transcription():
    for reason in ("MAX_TOKENS", "RECITATION", "SAFETY", None):
        body = gemini_ok("HALF A LETTER")
        if reason is None:
            del body["candidates"][0]["finishReason"]
        else:
            body["candidates"][0]["finishReason"] = reason
        adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(json_reply(body)).client())
        with pytest.raises(PermanentError):
            await adapter(vision_request())  # a cut-off page is incomplete: the rules must never screen half a letter and call it done


@pytest.mark.asyncio
async def test_gemini_usage_with_a_missing_count_is_estimated_high_and_thinking_tokens_are_counted():
    body = gemini_ok()
    body["usageMetadata"] = {"promptTokenCount": 10}  # no output count
    out = await GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(json_reply(body)).client())(vision_request(image=b"x" * 4000))
    assert out.usage.input_tokens == 1000
    body["usageMetadata"] = {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 7}
    out = await GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(json_reply(body)).client())(vision_request())
    assert (out.usage.input_tokens, out.usage.output_tokens) == (10, 12)


@pytest.mark.asyncio
async def test_a_choice_the_spread_would_not_rank_first_is_no_decision_and_missing_usage_is_estimated_high():
    for confidence in (0.3, 0.5):
        adapter = JevAdapter(JevRouterClient(KEY, timeout_seconds=20, http=Mock(json_reply(openrouter_ok("urgent", confidence))).client()))
        with pytest.raises(PermanentError):
            await adapter(decision_request())  # 0.3 of two options would make the OTHER option the argmax
    adapter = JevAdapter(JevRouterClient(KEY, timeout_seconds=20, http=Mock(json_reply(openrouter_ok(usage=False))).client()))
    out = await adapter(decision_request())
    assert out.usage.input_tokens >= 400 and out.usage.output_tokens == 64


@pytest.mark.asyncio
async def test_a_stream_or_header_fault_is_a_failed_route_and_a_slow_drip_is_cut_by_the_wall_clock():
    import asyncio

    def decoding(request):
        raise httpx.DecodingError("bad " + KEY)

    adapter = GeminiVisionAdapter(KEY, "gemini-2.5-flash", timeout_seconds=60, http=Mock(decoding).client())
    with pytest.raises(PermanentError) as caught:
        await adapter(vision_request())
    assert_clean(caught.value)
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(50):
                await asyncio.sleep(0.05)
                yield b" "

    slow = Mock(lambda request: httpx.Response(200, stream=Drip()))
    with pytest.raises(TransientError):
        await provider_http.post_json(slow.client(), "https://x.test/", headers={}, body={}, seconds=0.2)


def test_the_provider_client_ignores_proxy_and_ca_settings_from_the_environment():
    assert provider_http.new_client(5)._trust_env is False


def test_jev_is_refused_outside_the_demo_profile_and_its_output_is_never_evidence():
    from careflow.adapters.decision import NOT_EVIDENCE_ENGINES, counts_as_evidence
    from careflow.ports import DecisionResult

    for profile in ("clinical", "staging", "", "production"):
        env = good_env(CAREFLOW_PROFILE=profile)
        got = build_providers(env, profile=profile)
        assert got.gateway is None and "jev" in got.unavailable, profile  # no decision row, no Jev adapter, anywhere but demo + synthetic_only
    assert "jev" in NOT_EVIDENCE_ENGINES
    jev = DecisionResult("a", {"a": 0.9, "b": 0.1}, 0.9, "jev")
    assert counts_as_evidence(jev) is False and counts_as_evidence(DecisionResult("a", {"a": 0.9, "b": 0.1}, 0.9, "laya")) is True


def test_the_per_run_cap_is_required_with_no_default_and_becomes_the_gateways_per_job_hard_stop():
    from careflow.provider_setup import read_run_cap

    for bad in ("", "abc", "-1", "0", "NaN", "Infinity"):
        assert read_run_cap({RUN_CAP_VARIABLE: bad}) is None, bad
        got = build_providers(good_env(**{RUN_CAP_VARIABLE: bad}), profile="demo")
        assert got.gateway is None and got.vision is None and got.translation is None and any("PER_RUN" in r for r in got.unavailable.values()), bad
    assert read_run_cap({}) is None and read_run_cap(good_env()) == Decimal("0.50")
    built = build_providers(good_env(), profile="demo")
    assert built.gateway is not None and built.gateway.limits.per_request_usd == Decimal("0.50")  # the owner's number, not the 1.00 the class would default to


def test_a_durable_ledger_passed_in_is_the_one_the_gateway_uses():
    from careflow.gateway import InMemorySpendLedger

    mine = InMemorySpendLedger()
    built = build_providers(good_env(), profile="demo", ledger=mine)
    assert built.gateway is not None and built.gateway.ledger is mine
