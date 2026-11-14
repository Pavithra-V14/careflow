"""The small policy pieces (careflow/spend_policy.py). Offline."""

from decimal import Decimal

import pytest

from careflow import spend_policy as sp

D = Decimal


def test_below_eighty_percent_nothing_changes_at_eighty_the_decorative_features_go_in_order_at_the_cap_non_urgent_jobs_queue():
    assert sp.soft_threshold_plan(D("79"), D("100")) == sp.SpendPlan(True, (), False)
    soft = sp.soft_threshold_plan(D("80"), D("100"))
    assert soft.disable == ("card_shortlist_ensemble", "reranker", "judge_sampling") and not soft.queue_non_urgent
    hard = sp.soft_threshold_plan(D("100"), D("100"))
    assert hard.queue_non_urgent and hard.disable == soft.disable


def test_the_safety_path_is_never_budget_limited_whatever_the_numbers_say():
    assert all(sp.soft_threshold_plan(D(s), D("10")).safety_limited is False for s in ("0", "8", "10", "1000"))


@pytest.mark.parametrize(("spent", "cap"), [(None, D("10")), (D("1"), None), (D("1"), D("0")), (D("-1"), D("10"))])
def test_an_unknown_or_unusable_number_changes_nothing_and_says_so(spent, cap):
    assert sp.soft_threshold_plan(spent, cap) == sp.SpendPlan(False, (), False)


def test_the_failure_multiplier_and_its_unknowns():
    assert sp.failure_multiplier(D("3"), 2, D("0.5"), 6) == D("3")  # 1.5 per success over 0.5 nominal
    assert sp.failure_multiplier(D("3"), 0, D("0.5"), 6) is None  # no success: no value, not zero and not infinity
    assert sp.failure_multiplier(D("3"), 2, D("0"), 6) is None and sp.failure_multiplier(D("3"), 2, D("0.5"), 0) is None


def test_the_tail_review_is_the_costliest_five_percent_by_id_and_cost_only():
    jobs = [(f"j{i}", D(i)) for i in range(1, 41)]
    assert sp.tail_review(jobs) == [("j40", D(40)), ("j39", D(39))]  # 5% of 40 is 2
    assert sp.tail_review([("only", D("1"))]) == [("only", D("1"))] and sp.tail_review([]) == []


def test_the_grounding_rate_has_no_value_when_nothing_was_checked():
    assert sp.grounding_rate(3, 4) == D("0.75") and sp.grounding_rate(0, 0) is None and sp.grounding_rate(5, 4) is None


def test_a_vanished_model_is_reported_and_an_unreadable_listing_is_unverifiable_never_vanished():
    pinned = {"a": frozenset({"m1", "m2"}), "b": frozenset({"x"}), "c": frozenset({"y"})}
    rep = sp.check_pinned_models(pinned, {"a": frozenset({"m1"}), "b": None, "c": frozenset({"y", "z"})})
    assert rep.vanished == {"a": frozenset({"m2"})} and rep.unverifiable == frozenset({"b"})
    assert sp.check_pinned_models(pinned, {}).unverifiable == frozenset({"a", "b", "c"})  # no listing at all: nothing is concluded


@pytest.mark.asyncio
async def test_a_read_with_the_same_arguments_inside_one_run_is_computed_once_and_a_new_run_starts_empty():
    calls = []

    async def read():
        calls.append(1)
        return {"n": len(calls)}

    memo = sp.RunMemo()
    first = await memo.get("get_recent_results", {"patient": "p"}, read)
    again = await memo.get("get_recent_results", {"patient": "p"}, read)
    other = await memo.get("get_recent_results", {"patient": "q"}, read)
    assert first is again and other is not first and len(calls) == 2 and memo.hits == 1
    assert await sp.RunMemo().get("get_recent_results", {"patient": "p"}, read) is not first  # a new run does not see the old one


def test_latency_is_reported_in_three_parts_never_blended():
    from datetime import UTC, datetime, timedelta

    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    parts = sp.latency_parts([("machine", t0, t0 + timedelta(seconds=5)), ("human_wait", t0, t0 + timedelta(hours=2)), ("external_wait", t0, t0 + timedelta(days=3)), ("machine", t0, t0 + timedelta(seconds=7))])
    assert parts == {"machine": timedelta(seconds=12), "human_wait": timedelta(hours=2), "external_wait": timedelta(days=3)}
    with pytest.raises(ValueError):
        sp.latency_parts([("blended", t0, t0)])
    with pytest.raises(ValueError):
        sp.latency_parts([("machine", t0, t0 - timedelta(seconds=1))])


@pytest.mark.asyncio
async def test_the_gateway_reports_the_feature_plan_from_the_ledger_and_an_unusable_cap_changes_nothing():
    import uuid

    from careflow.gateway import Gateway, InMemorySpendLedger, SpendEntry, SpendLimits

    ledger, clinic = InMemorySpendLedger(), uuid.uuid4()
    gw = Gateway([], {}, ledger, SpendLimits(clinic_daily_usd=D("10")), origin="test")
    assert (await gw.feature_plan(clinic)).disable == ()
    await ledger.record(SpendEntry(clinic, "decision", "p", "m", "ordinary", D("8"), D("8"), "test", "k1"))
    soft = await gw.feature_plan(clinic)
    assert soft.disable == ("card_shortlist_ensemble", "reranker", "judge_sampling") and not soft.queue_non_urgent and soft.safety_limited is False
    await ledger.record(SpendEntry(clinic, "decision", "p", "m", "ordinary", D("2"), D("2"), "test", "k2"))
    assert (await gw.feature_plan(clinic)).queue_non_urgent
