"""careflow.gateway, careflow.ports and the fake adapters (BUILD-SPEC.md §2, §5.1, §5.2, §6.1, §16.1). Offline: fake adapters, an injected
clock and sleep, no network and no model."""

import ast
import asyncio
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from careflow.adapters.fake import (
    FakeDecision,
    FakeEmail,
    FakeEmbedding,
    FakeRerank,
    FakeSms,
    FakeTranslation,
)
from careflow.budget import BudgetExceeded, CallClass, RunBudget, TickClock
from careflow.gateway import (
    AllRoutesFailed,
    CircuitBreaker,
    Gateway,
    GatewayError,
    InMemorySpendLedger,
    ModelRow,
    NoApprovedRoute,
    SpendCeilingReached,
    SpendEntry,
    SpendLimits,
)
from careflow.ports import (
    AdapterResult,
    DataClass,
    DecisionRequest,
    PermanentError,
    RerankRequest,
    TransientError,
    TranslationRequest,
    Usage,
)

ROOT = Path(__file__).resolve().parent.parent
CLINIC = uuid.uuid4()
D = Decimal


def row(role="decision", provider="p1", model="m-1", policy="synthetic_only", priority=1, price=("0", "0"), shadow=("0", "0"), **kw):
    return ModelRow(role, provider, model, policy, priority, D(price[0]), D(price[1]), D(shadow[0]), D(shadow[1]), **kw)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, rows, adapters, daily="10.00", per_request="1.00"):
        self.clock, self.sleeps = Clock(), []
        self.ledger = InMemorySpendLedger(today=lambda: date(2030, 1, 1))

        async def sleep(s):
            self.sleeps.append(s)

        self.gw = Gateway(
            rows, adapters, self.ledger, SpendLimits(D(daily), D(per_request)), origin="test", clock=self.clock,
            sleep=sleep, jitter=lambda: 0.5,
        )

    async def spend(self, clinic, usd):
        """Pre-load the ledger with spend already made today."""
        await self.ledger.record(SpendEntry(clinic, "decision", "p", "m", "ordinary", D(usd), D(0), "test", f"pre-{uuid.uuid4()}"))

    async def decide(self, **kw):
        req = DecisionRequest("q", "some text", ("a", "b"), DataClass.SYNTHETIC)
        return await self.gw.call("decision", req, data_class=kw.pop("data_class", DataClass.SYNTHETIC), clinic_id=kw.pop("clinic_id", CLINIC), **kw)


def adapters_for(*pairs):
    return {(r.provider, r.model_id): a.decide for r, a in pairs}


# ---- ports and registry rows -----------------------------------------------------------------------------------------------------------


def test_a_decision_call_takes_between_two_and_twenty_distinct_options():
    DecisionRequest("q", "t", tuple(f"o{i}" for i in range(20)), DataClass.SYNTHETIC)
    for bad in (("only",), tuple(f"o{i}" for i in range(21)), ("a", "a")):
        with pytest.raises(ValueError):
            DecisionRequest("q", "t", bad, DataClass.SYNTHETIC)
    with pytest.raises(ValueError):
        DecisionRequest(" ", "t", ("a", "b"), DataClass.SYNTHETIC)


def test_patient_text_never_appears_in_a_request_repr():
    secret = "SENTINEL-PATIENT-TEXT-5521"
    for obj in (DecisionRequest("q", secret, ("a", "b"), DataClass.PHI), RerankRequest(secret, (secret,)), TranslationRequest(secret, "ta", "en")):
        assert secret not in repr(obj)


@pytest.mark.parametrize("model", ["latest", "gpt-x-latest", "vendor/model@latest", "m:stable", "default", "m-1 ", " m-1", "m-latest\n", "m\x01-1"])
def test_an_unpinned_model_id_is_refused(model):
    with pytest.raises(ValueError):
        row(model=model)


def test_a_pinned_model_id_is_accepted_even_if_it_contains_the_word():
    assert row(model="modernbert-large-2025-06-01").model_id == "modernbert-large-2025-06-01"
    assert row(model="latest-news-classifier-v2")


def test_the_registry_row_validates_role_policy_priority_unit_and_prices():
    for kw in ({"role": "nope"}, {"policy": "maybe"}, {"priority": 0}, {"priority": True}, {"price": ("-1", "0")}, {"provider": " "}, {"timeout_seconds": 0}):
        with pytest.raises(ValueError):
            row(**kw)
    with pytest.raises(ValueError):
        row(role="decision", unit="audio_tokens")  # only voice_live is priced per audio token
    with pytest.raises(ValueError):
        row(role="voice_live")  # and voice_live must be
    assert row(role="voice_live", unit="audio_tokens").unit == "audio_tokens"


def test_a_free_tier_row_has_an_actual_cost_of_zero_and_a_shadow_price():
    free = row(price=("0", "0"), shadow=("0.003", "0.015"))
    actual, shadow = free.cost(Usage(2000, 1000))
    assert actual == 0 and shadow == D("0.021")


def test_voice_is_priced_per_audio_token_in_its_own_unit():
    voice = row(role="voice_live", unit="audio_tokens", price=("0.1", "0.2"))
    assert voice.cost(Usage(1000, 500, "audio_tokens"))[0] == D("0.2")
    with pytest.raises(GatewayError):
        voice.cost(Usage(1000, 500, "text_tokens"))  # a text-unit usage cannot be priced against an audio row


# ---- the fakes -------------------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fakes_are_deterministic_and_can_be_told_to_fail():
    d = FakeDecision("x")
    req = DecisionRequest("q", "same text", ("a", "b", "c"), DataClass.SYNTHETIC)
    first, again = (await d.decide(req)).value, (await d.decide(req)).value
    assert first == again and first.choice in req.options and abs(sum(first.probabilities.values()) - 1) < 1e-9 and first.engine == "x"
    e = FakeEmbedding()
    v = (await e.embed(["alpha", "beta"])).value
    assert v.vectors == (await e.embed(["alpha", "beta"])).value.vectors and len(v.vectors[0]) == v.dimensions
    r = (await FakeRerank().rerank(RerankRequest("heart failure", ("knee pain", "heart failure clinic")))).value
    assert r.order[0] == 1
    t = (await FakeTranslation().translate(TranslationRequest("hello", "en", "ta"))).value
    assert t.text.startswith("[en->ta]")
    d.fail_next(2)
    for _ in range(2):
        with pytest.raises(TransientError):
            await d.decide(req)
    assert (await d.decide(req)).value.choice == first.choice


@pytest.mark.asyncio
async def test_the_email_and_sms_sinks_are_idempotent_and_demand_a_key():
    for sink, send in ((FakeEmail(), lambda s, k: s.send("a@b.c", "subject", "body", idempotency_key=k)), (FakeSms(), lambda s, k: s.send("+910000000000", "body", idempotency_key=k))):
        one = await send(sink, "k1")
        again = await send(sink, "k1")
        assert (one.replayed, again.replayed, again.provider_id) == (False, True, one.provider_id) and len(sink.sent) == 1
        await send(sink, "k2")
        assert len(sink.sent) == 2
        with pytest.raises(PermanentError):
            await send(sink, " ")


def test_nothing_in_the_ports_gateway_or_fakes_imports_a_network_client():
    banned = {"httpx", "requests", "aiohttp", "urllib3", "socket", "smtplib", "websockets", "openai", "anthropic"}
    for rel in ("careflow/ports.py", "careflow/gateway.py", "careflow/budget.py", "careflow/adapters/fake.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        roots = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        roots |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert not roots & banned, (rel, roots & banned)


# ---- routing and fallback --------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_best_priority_enabled_row_answers_and_the_result_names_the_engine():
    a, b, c = FakeDecision("a"), FakeDecision("b"), FakeDecision("c")
    ra, rb, rc = row(provider="a", priority=2), row(provider="b", priority=1), row(provider="c", priority=1, model="m-2", enabled=False)
    rig = Rig([ra, rb, rc], adapters_for((ra, a), (rb, b), (rc, c)))
    res = await rig.decide()
    assert (res.provider, res.value.engine) == ("b", "b") and (a.calls, b.calls, c.calls) == (0, 1, 0)
    assert [x.outcome for x in res.attempts] == ["ok"]


@pytest.mark.asyncio
async def test_a_failing_route_falls_back_to_the_next_and_the_attempts_are_recorded():
    a, b = FakeDecision("a"), FakeDecision("b")
    ra, rb = row(provider="a", priority=1), row(provider="b", priority=2)
    a.fail_next(10, PermanentError("refused"))
    rig = Rig([ra, rb], adapters_for((ra, a), (rb, b)))
    res = await rig.decide()
    assert res.provider == "b" and [x.outcome for x in res.attempts] == ["failed_permanent", "ok"] and a.calls == 1  # permanent: no retry


@pytest.mark.asyncio
async def test_a_transient_failure_is_retried_up_to_three_times_with_backoff_and_jitter():
    a = FakeDecision("a")
    ra = row(provider="a")
    a.fail_next(2)
    rig = Rig([ra], adapters_for((ra, a)))
    res = await rig.decide()
    assert res.attempts[0].calls == 3 and a.calls == 3
    assert rig.sleeps == [pytest.approx(0.2), pytest.approx(0.4)]  # base 0.2 s, doubled, times (0.5 + jitter 0.5)


@pytest.mark.asyncio
async def test_three_transient_failures_move_on_and_all_failing_raises_with_the_attempts():
    a, b = FakeDecision("a"), FakeDecision("b")
    ra, rb = row(provider="a", priority=1), row(provider="b", priority=2)
    a.fail_next(3)
    b.fail_next(3)
    rig = Rig([ra, rb], adapters_for((ra, a), (rb, b)))
    with pytest.raises(AllRoutesFailed) as exc:
        await rig.decide()
    assert [x.outcome for x in exc.value.attempts] == ["failed_transient", "failed_transient"] and (a.calls, b.calls) == (3, 3)


@pytest.mark.asyncio
async def test_a_slow_adapter_times_out_and_the_next_route_answers():
    slow, fast = FakeDecision("slow", latency=5), FakeDecision("fast")
    rs, rf = row(provider="slow", priority=1, timeout_seconds=0.05), row(provider="fast", priority=2)
    rig = Rig([rs, rf], adapters_for((rs, slow), (rf, fast)))
    res = await rig.decide()
    assert res.provider == "fast" and res.attempts[0].outcome == "timeout"


@pytest.mark.asyncio
async def test_no_enabled_row_for_the_role_is_no_approved_route():
    rig = Rig([row(role="embed")], {})
    with pytest.raises(NoApprovedRoute):
        await rig.decide()


# ---- the data policy: a PHI request only ever reaches a phi_approved row ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_phi_goes_only_to_a_phi_approved_row_and_never_falls_back_to_a_synthetic_only_one():
    local, hosted = FakeDecision("local"), FakeDecision("hosted")
    rl, rh = row(provider="local", policy="phi_approved", priority=2), row(provider="hosted", policy="synthetic_only", priority=1)
    rig = Rig([rl, rh], adapters_for((rl, local), (rh, hosted)))
    res = await rig.decide(data_class=DataClass.PHI)
    assert res.provider == "local" and hosted.calls == 0  # the better-priority synthetic_only row was never offered the patient text
    local.fail_next(10, PermanentError("down"))
    with pytest.raises(AllRoutesFailed):
        await rig.decide(data_class=DataClass.PHI)
    assert hosted.calls == 0  # even with the approved route down, no fallback to an unapproved one


@pytest.mark.asyncio
async def test_with_no_phi_approved_row_the_call_degrades_visibly_whatever_the_call_class():
    hosted = FakeDecision("hosted")
    rh = row(provider="hosted", policy="synthetic_only")
    rig = Rig([rh], adapters_for((rh, hosted)))
    for call_class in (CallClass.ORDINARY, CallClass.SAFETY):  # the safety class does not bypass the data policy
        with pytest.raises(NoApprovedRoute) as exc:
            await rig.decide(data_class=DataClass.PHI, call_class=call_class)
        assert (exc.value.role, exc.value.data_class) == ("decision", DataClass.PHI)
    assert hosted.calls == 0
    assert (await rig.decide(data_class=DataClass.SYNTHETIC)).provider == "hosted"  # synthetic data may use it


# ---- circuit breakers ------------------------------------------------------------------------------------------------------------------


def test_the_breaker_opens_after_ten_consecutive_failures_and_allows_one_probe_after_sixty_seconds():
    clock = Clock()
    b = CircuitBreaker(clock=clock)
    for _ in range(9):
        b.failure()
    assert b.state == "closed" and b.allow()
    b.success()  # a success resets the count
    for _ in range(9):
        b.failure()
    assert b.allow()
    b.failure()
    assert b.state == "open" and not b.allow()
    clock.t += 59.9
    assert not b.allow()
    clock.t += 0.2
    assert b.allow() and b.state == "half_open" and not b.allow()  # exactly one probe at a time
    b.failure()  # the probe fails: open again for another 60 s
    assert not b.allow()
    clock.t += 61
    assert b.allow()
    b.success()
    assert b.state == "closed" and b.allow()


@pytest.mark.asyncio
async def test_an_open_breaker_skips_the_engine_and_the_next_route_answers_until_the_probe_succeeds():
    a, b = FakeDecision("a"), FakeDecision("b")
    ra, rb = row(provider="a", priority=1), row(provider="b", priority=2)
    a.fail_next(100)
    rig = Rig([ra, rb], adapters_for((ra, a), (rb, b)))
    for _ in range(4):  # 3 attempts each, so ten consecutive failures are reached on the fourth call
        await rig.decide()
    calls_when_open = a.calls
    res = await rig.decide()
    assert a.calls == calls_when_open and res.attempts[0].outcome == "skipped_breaker_open" and res.provider == "b"
    rig.clock.t += 61
    a._failures.clear()
    res = await rig.decide()
    assert res.provider == "a" and a.calls == calls_when_open + 1  # the single probe went through and closed the breaker
    assert (await rig.decide()).provider == "a"


# ---- spend ceilings and the safety class ----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_is_recorded_per_clinic_and_day_with_the_shadow_cost_for_a_free_row():
    free = FakeDecision("free")
    rf = row(provider="free", price=("0", "0"), shadow=("0.01", "0.02"))
    rig = Rig([rf], adapters_for((rf, free)))
    res = await rig.decide()
    assert res.cost_usd == 0 and res.shadow_cost_usd > 0 and await rig.ledger.spent_today(CLINIC, "test") == 0


@pytest.mark.asyncio
async def test_an_ordinary_call_skips_a_paid_row_at_the_daily_ceiling_and_a_free_row_still_answers():
    paid, free = FakeDecision("paid"), FakeDecision("free")
    rp, rf = row(provider="paid", priority=1, price=("1", "1")), row(provider="free", priority=2)
    rig = Rig([rp, rf], adapters_for((rp, paid), (rf, free)), daily="5.00")
    await rig.spend(CLINIC, "5.00")
    res = await rig.decide()
    assert res.provider == "free" and paid.calls == 0 and res.attempts[0].outcome == "skipped_spend"


@pytest.mark.asyncio
async def test_only_a_paid_row_at_the_ceiling_is_a_visible_spend_error_and_another_clinic_is_unaffected():
    paid = FakeDecision("paid")
    rp = row(provider="paid", price=("1", "1"))
    rig = Rig([rp], adapters_for((rp, paid)), daily="5.00")
    await rig.spend(CLINIC, "5.00")
    with pytest.raises(SpendCeilingReached):
        await rig.decide()
    assert (await rig.decide(clinic_id=uuid.uuid4())).provider == "paid"  # the ceiling is per clinic


@pytest.mark.asyncio
async def test_an_estimate_over_the_per_request_ceiling_is_refused_before_the_call():
    paid = FakeDecision("paid")
    rp = row(provider="paid", price=("1", "1"))
    rig = Rig([rp], adapters_for((rp, paid)), per_request="1.00")
    with pytest.raises(SpendCeilingReached):
        await rig.decide(estimate=Usage(2000, 0))  # est $2 > $1
    assert paid.calls == 0
    assert (await rig.decide(estimate=Usage(500, 0))).provider == "paid"


@pytest.mark.asyncio
async def test_the_safety_class_ignores_the_spend_ceilings_and_is_not_refused_by_an_exhausted_budget():
    paid = FakeDecision("paid")
    rp = row(provider="paid", price=("1", "1"))
    rig = Rig([rp], adapters_for((rp, paid)), daily="1.00", per_request="0.50")
    await rig.spend(CLINIC, "99")  # far over the daily ceiling
    with pytest.raises(SpendCeilingReached):
        await rig.decide()
    budget = RunBudget(TickClock(clock=rig.clock))
    for _ in range(12):
        budget.model_call()  # every ordinary model call is used up
    with pytest.raises(BudgetExceeded):
        await rig.decide(budget=budget, clinic_id=uuid.uuid4())  # another clinic, so the spend ceiling is not what stops it: the budget is
    res = await rig.decide(budget=budget, call_class=CallClass.SAFETY, estimate=Usage(5000, 5000))
    assert res.provider == "paid" and budget.safety_model_calls == 1 and budget.model_calls == 12


@pytest.mark.asyncio
async def test_every_attempt_counts_as_a_model_call_and_the_thirteenth_is_refused():
    a = FakeDecision("a")
    ra = row(provider="a")
    rig = Rig([ra], adapters_for((ra, a)))
    budget = RunBudget(TickClock(clock=rig.clock))
    a.fail_next(2)
    await rig.decide(budget=budget)
    assert budget.model_calls == 3  # two retried attempts and the success
    for _ in range(9):
        await rig.decide(budget=budget)
    assert budget.model_calls == 12
    with pytest.raises(BudgetExceeded):
        await rig.decide(budget=budget)
    assert a.calls == 12  # the refused call never reached the engine


@pytest.mark.asyncio
async def test_the_actual_cost_is_charged_to_the_runs_hard_stop():
    paid = FakeDecision("paid")
    rp = row(provider="paid", price=("1000", "1000"))  # $1 per token: absurd on purpose
    rig = Rig([rp], adapters_for((rp, paid)), daily="1000000", per_request="1000000")
    budget = RunBudget(TickClock(clock=rig.clock))
    with pytest.raises(BudgetExceeded) as exc:
        await rig.decide(budget=budget)
    assert exc.value.kind == "cost_usd"


@pytest.mark.asyncio
async def test_a_usage_in_the_wrong_unit_is_a_gateway_error_not_a_price():
    async def wrong(_payload):
        return AdapterResult("x", Usage(10, 10, "audio_tokens"))

    rp = row(provider="p")
    rig = Rig([rp], {rp.key: wrong})
    with pytest.raises(GatewayError):
        await rig.decide()


@pytest.mark.asyncio
async def test_the_gateway_does_not_sleep_for_real_in_tests_and_a_cancelled_call_propagates():
    a = FakeDecision("a", latency=30)
    ra = row(provider="a", timeout_seconds=60)
    rig = Rig([ra], adapters_for((ra, a)))
    task = asyncio.create_task(rig.decide())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---- applicability, and the persisted per-origin ledger --------------------------------------------------------------------------------


class Picky:
    """An adapter that declines some inputs BEFORE its model would be called (like Laya on an unsupported script)."""

    def __init__(self, inner, takes):
        self.inner, self.takes, self.calls = inner, takes, 0

    def accepts(self, payload):
        return self.takes(payload)

    async def __call__(self, payload):
        self.calls += 1
        return await self.inner.decide(payload)


@pytest.mark.asyncio
async def test_an_adapter_that_does_not_accept_the_input_is_skipped_without_a_budget_count_a_spend_check_or_a_breaker_failure():
    a, b = FakeDecision("a"), FakeDecision("b")
    ra, rb = row(provider="a", priority=1, price=("1", "1")), row(provider="b", priority=2)
    picky = Picky(a, takes=lambda p: False)
    rig = Rig([ra, rb], {ra.key: picky, rb.key: b.decide})
    budget = RunBudget(TickClock(clock=rig.clock))
    for _ in range(11):
        res = await rig.decide(budget=budget)
        assert res.provider == "b" and res.attempts[0].outcome == "skipped_not_applicable"
    assert picky.calls == 0 and budget.model_calls == 11  # only b's calls count
    assert rig.gw.breaker(ra.key).state == "closed"  # 11 declines never trip the breaker (the threshold is 10 failures)


@pytest.mark.asyncio
async def test_an_adapter_that_raises_not_applicable_is_not_a_failure_either():
    from careflow.ports import RouteNotApplicable

    class Declines:
        calls = 0

        async def __call__(self, payload):
            Declines.calls += 1
            raise RouteNotApplicable("unsupported input")

    ra, rb = row(provider="a", priority=1), row(provider="b", priority=2)
    rig = Rig([ra, rb], {ra.key: Declines(), rb.key: FakeDecision("b").decide})
    for _ in range(12):
        assert (await rig.decide()).provider == "b"
    assert rig.gw.breaker(ra.key).state == "closed"


@pytest.mark.asyncio
async def test_when_every_route_declines_the_call_fails_visibly_with_the_reason():
    ra = row(provider="a")
    rig = Rig([ra], {ra.key: Picky(FakeDecision("a"), takes=lambda p: False)})
    with pytest.raises(AllRoutesFailed) as exc:
        await rig.decide()
    assert [x.outcome for x in exc.value.attempts] == ["skipped_not_applicable"]


@pytest.mark.asyncio
async def test_every_call_is_recorded_in_the_ledger_with_ids_and_amounts_and_a_unique_key():
    paid = FakeDecision("paid")
    rp = row(provider="paid", price=("1", "2"), shadow=("3", "4"))
    rig = Rig([rp], adapters_for((rp, paid)), daily="100", per_request="100")
    await rig.decide()
    await rig.decide(call_class=CallClass.SAFETY)
    first, second = rig.ledger.entries
    assert (first.clinic_id, first.role, first.provider, first.model_id, first.call_class, first.origin) == (CLINIC, "decision", "paid", "m-1", "ordinary", "test")
    assert second.call_class == "safety" and first.idempotency_key != second.idempotency_key
    assert first.usd_actual > 0 and first.usd_shadow > first.usd_actual


@pytest.mark.asyncio
async def test_safety_spend_counts_toward_the_days_total_but_other_origins_and_clinics_do_not():
    paid = FakeDecision("paid")
    rp = row(provider="paid", price=("1", "1"))
    rig = Rig([rp], adapters_for((rp, paid)), daily="100", per_request="100")
    await rig.decide(call_class=CallClass.SAFETY)
    total = await rig.ledger.spent_today(CLINIC, "test")
    assert total > 0
    assert await rig.ledger.spent_today(CLINIC, "live") == 0  # another origin
    assert await rig.ledger.spent_today(uuid.uuid4(), "test") == 0  # another clinic
    rig2 = Rig([rp], adapters_for((rp, paid)), daily=str(total), per_request="100")
    rig2.ledger = rig.ledger
    rig2.gw.ledger = rig.ledger
    with pytest.raises(SpendCeilingReached):  # the safety call's spend now limits ORDINARY calls
        await rig2.decide(estimate=Usage(10, 10))


@pytest.mark.asyncio
async def test_the_postgres_ledger_asks_the_database_for_today_in_india_time_and_for_one_origin(monkeypatch):
    from contextlib import asynccontextmanager

    from careflow import spend as spend_module
    from careflow.spend import PostgresSpendLedger

    seen = []

    class Result:
        def scalar_one(self):
            return D("1.25")

    class Conn:
        async def execute(self, statement, params=None):
            seen.append((str(statement), params))
            return Result()

        def begin(self):
            @asynccontextmanager
            async def cm():
                yield self

            return cm()

    class Engine:
        def connect(self):
            @asynccontextmanager
            async def cm():
                yield Conn()

            return cm()

    async def fake_claims(conn, claims):
        seen.append(("claims", claims["sub"]))

    monkeypatch.setattr(spend_module, "set_request_claims", fake_claims)
    account = uuid.uuid4()
    ledger = PostgresSpendLedger(Engine(), account)  # type: ignore[arg-type]
    assert await ledger.spent_today(CLINIC, "live") == D("1.25")
    sql, params = next((s, p) for s, p in seen if "ops.spend_ledger" in str(s))
    assert "Asia/Kolkata" in sql and "origin = :o" in sql and params == {"c": CLINIC, "o": "live"}
    assert ("claims", str(account)) in seen
    seen.clear()
    await ledger.record(SpendEntry(CLINIC, "decision", "p", "m", "safety", D("0.5"), D("0.7"), "live", "k-1"))
    sql, params = next((s, p) for s, p in seen if "insert into ops.spend_ledger" in str(s))
    assert "day" not in sql and params["i"] == "k-1" and params["k"] == "safety"  # the day is never supplied by the caller
