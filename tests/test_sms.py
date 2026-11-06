"""SMS template registration status and the send guard offline (careflow/sms.py). No provider is called; the sandbox sink records a masked recipient only. Template ids and values are synthetic."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from careflow import sms
from careflow.sms import (
    SandboxSink,
    SmsError,
    SmsLimits,
    SmsTemplate,
    TemplatedSms,
    check_value,
    load_templates,
    template_problems,
)

pytestmark = pytest.mark.asyncio
T0 = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
GOOD = ["Demo Hospital", "2026-11-07T10:30:00", "https://example.org/a/x1"]
LIMITS = SmsLimits(2, 3600, 10.0, 1.0)


def tpl(status="registered", kinds=("brand", "slot_time", "link"), language="en", tid="demo_offer_en"):
    return SmsTemplate(tid, language, tuple(kinds), status)


def make(template=None, limits=LIMITS, profile="clinical", clock=None):
    sink = SandboxSink()
    t = template or tpl()
    now = clock or (lambda: T0)
    return TemplatedSms({t.template_id: t}, limits, sink.deliver, profile=profile, now=now), sink


def test_the_committed_template_file_is_empty_and_loads():
    assert load_templates() == {}


def test_template_structure_rules():
    assert template_problems(tpl()) == []
    assert "too_many_variables" in template_problems(tpl(kinds=("brand",) * 6))
    assert "variable_kind" in template_problems(tpl(kinds=("brand", "free_text")))  # there is no free-text kind
    assert "no_brand_variable" in template_problems(tpl(kinds=("link",)))
    assert "dlt_status" in template_problems(tpl(status="approved"))
    assert "language" in template_problems(tpl(language="fr"))
    assert "template_id" in template_problems(tpl(tid="Bad Id"))


def test_a_malformed_or_repeated_template_in_the_file_is_refused(tmp_path):
    path = tmp_path / "t.yaml"
    path.write_text("templates:\n  - {id: a_one, language: en, variables: [brand, link], dlt_status: registered}\n  - {id: a_one, language: en, variables: [brand], dlt_status: registered}\n", encoding="utf-8")
    with pytest.raises(SmsError):
        load_templates(path)
    path.write_text("templates:\n  - {id: a_one, language: en, variables: [link], dlt_status: registered}\n", encoding="utf-8")
    with pytest.raises(SmsError):
        load_templates(path)


@pytest.mark.parametrize(
    ("kind", "value", "ok"),
    [
        ("link", "https://example.org/x", True), ("link", "http://example.org/x", False), ("link", "javascript:alert(1)", False), ("link", "https://exa mple.org", False),
        ("brand", "Demo Hospital", True), ("brand", "x", False), ("brand", "A" * 41, False), ("brand", "Demo <b>", False),
        ("slot_time", "2026-11-07T10:30:00", True), ("slot_time", "next tuesday", False),
        ("short_code", "AB12cd", True), ("short_code", "ab", False), ("short_code", "has space", False),
        ("link", None, False), ("anything_else", "x", False),
    ],
)
def test_each_variable_kind_is_checked_and_there_is_no_free_text(kind, value, ok):
    assert check_value(kind, value) is ok


async def test_a_registered_template_is_sent_through_the_sandbox_and_the_recipient_is_masked():
    s, sink = make()
    result = await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="k1")
    assert result.provider_id == "sandbox:k1" and sink.sent == [("demo_offer_en", "*********3210")]


@pytest.mark.parametrize("status", ["not_registered", "pending"])
async def test_an_unregistered_template_is_refused_in_clinical_because_the_carrier_would_drop_it_silently(status):
    s, sink = make(tpl(status=status))
    with pytest.raises(SmsError, match="not registered"):
        await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="k")
    assert sink.sent == []


async def test_demo_sends_an_unregistered_template_to_the_sandbox_only():
    s, sink = make(tpl(status="not_registered"), profile="demo")
    await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="k")
    assert len(sink.sent) == 1


@pytest.mark.parametrize("variables", [GOOD[:2], [*GOOD, "x"], ["Demo Hospital", "tomorrow", GOOD[2]], [GOOD[0], GOOD[1], "http://insecure.example"]])
async def test_variables_must_match_the_template_in_number_and_kind(variables):
    s, sink = make()
    with pytest.raises(SmsError, match="variables"):
        await s.send("demo_offer_en", "+919876543210", variables, idempotency_key="k")
    assert sink.sent == []


@pytest.mark.parametrize("to", ["9876543210", "+14155550100", "+91 98765 43210", "+91", "", "+9198765432101234567"])
async def test_only_well_formed_numbers_with_an_allowed_prefix_are_sent_to(to):
    s, sink = make()
    with pytest.raises(SmsError, match="recipient"):
        await s.send("demo_offer_en", to, GOOD, idempotency_key="k")
    assert sink.sent == []


async def test_an_unknown_template_is_refused():
    s, _ = make()
    with pytest.raises(SmsError, match="unknown"):
        await s.send("nope", "+919876543210", GOOD, idempotency_key="k")


@pytest.mark.parametrize("limits", [SmsLimits(None, 3600, 10.0, 1.0), SmsLimits(2, None, 10.0, 1.0), SmsLimits(2, 3600, None, 1.0), SmsLimits(2, 3600, 10.0, None)])
async def test_an_unset_limit_refuses_to_send_because_unknown_spend_is_not_a_value(limits):
    s, sink = make(limits=limits)
    with pytest.raises(SmsError, match="not configured"):
        await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="k")
    assert sink.sent == []


async def test_the_per_phone_limit_applies_within_the_window_and_resets_after_it():
    clock = {"now": T0}
    s, sink = make(clock=lambda: clock["now"])
    for i in range(2):
        await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key=f"k{i}")
    with pytest.raises(SmsError, match="per-phone"):
        await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="k2")
    await s.send("demo_offer_en", "+919876543211", GOOD, idempotency_key="other")  # another phone is unaffected
    clock["now"] = T0 + timedelta(seconds=3601)
    await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="k3")
    assert len(sink.sent) == 4


async def test_the_daily_spend_cap_stops_sending_and_a_new_day_starts_again():
    clock = {"now": T0}
    s, sink = make(limits=SmsLimits(100, 3600, 3.0, 1.0), clock=lambda: clock["now"])
    for i in range(3):
        await s.send("demo_offer_en", f"+9198765432{i:02d}", GOOD, idempotency_key=f"k{i}")
    with pytest.raises(SmsError, match="spend cap"):
        await s.send("demo_offer_en", "+919876543299", GOOD, idempotency_key="k9")
    clock["now"] = T0 + timedelta(days=1)
    await s.send("demo_offer_en", "+919876543299", GOOD, idempotency_key="k10")
    assert len(sink.sent) == 4


async def test_a_replayed_key_sends_nothing_more_and_costs_nothing():
    s, sink = make(limits=SmsLimits(1, 3600, 1.0, 1.0))
    first = await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="same")
    again = await s.send("demo_offer_en", "+919876543210", GOOD, idempotency_key="same")
    assert again.replayed and again.provider_id == first.provider_id and len(sink.sent) == 1


def test_the_module_calls_no_provider():
    text = Path(sms.__file__).read_text(encoding="utf-8")
    assert not any(word in text for word in ("import httpx", "import requests", "aiohttp", "twilio", "msg91"))
