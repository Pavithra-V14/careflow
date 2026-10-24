"""Triage routing policy (careflow/triage.py): the shape of the spec's rules with NO clinical value in it. Every number and label below is a TEST INPUT."""

import pytest

from careflow.triage import (
    ESCALATION_CHAIN,
    ApprovalMode,
    AutoApprovalPolicy,
    CardFacts,
    is_training_label_source,
    next_escalation,
    resolve_mode,
    route_proposal,
)

POLICY = AutoApprovalPolicy(low_risk_value="LOW", allowed_priorities=frozenset({"P1", "P2"}), min_confidence=0.9)  # test inputs
CARD = CardFacts("c1", 1, risk_class="LOW", priority="P1")


def route(**over):
    args = {
        "mode": ApprovalMode.ASSISTED, "policy": POLICY, "candidates": [CARD], "abstained": False, "identity_resolved": True,
        "identity_uncertain": False, "confidence": 0.95, "confidence_calibrated": True, "unverified_facts_read": [],
    }
    args.update(over)
    return route_proposal(**args)


def test_mode_unset_is_assisted_and_anything_unrecognised_is_the_strictest():
    assert resolve_mode(None) is ApprovalMode.ASSISTED and resolve_mode("  ") is ApprovalMode.ASSISTED
    assert resolve_mode("Assisted") is ApprovalMode.ASSISTED and resolve_mode("autonomous") is ApprovalMode.AUTONOMOUS
    for typo in ("autonomus", "auto", "yes", "assisted;autonomous"):
        assert resolve_mode(typo) is ApprovalMode.SHADOW


def test_every_condition_holding_is_the_only_way_to_be_eligible():
    assert route().auto_approval_eligible and route().reasons == ()


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"mode": ApprovalMode.SHADOW}, "mode_requires_human"),
        ({"mode": ApprovalMode.AUTONOMOUS}, "mode_requires_human"),
        ({"policy": None}, "no_auto_approval_policy"),
        ({"abstained": True}, "retrieval_abstained"),
        ({"identity_resolved": False}, "identity_not_certain"),
        ({"identity_uncertain": True}, "identity_not_certain"),
        ({"unverified_facts_read": ["ef"]}, "unverified_fact_read_by_card"),
        ({"unverified_facts_read": None}, "unverified_facts_unknown"),
        ({"confidence": 1.5}, "confidence_unknown_or_uncalibrated"),
        ({"confidence": -0.1}, "confidence_unknown_or_uncalibrated"),
        ({"candidates": []}, "not_exactly_one_candidate"),
        ({"candidates": [CARD, CardFacts("c2", 1, "LOW", "P1")]}, "not_exactly_one_candidate"),
        ({"confidence": None}, "confidence_unknown_or_uncalibrated"),
        ({"confidence": float("nan")}, "confidence_unknown_or_uncalibrated"),
        ({"confidence_calibrated": False}, "confidence_unknown_or_uncalibrated"),
        ({"confidence": 0.5}, "confidence_below_policy"),
        ({"candidates": [CardFacts("c1", 1, None, "P1")]}, "card_risk_or_priority_unknown"),
        ({"candidates": [CardFacts("c1", 1, "LOW", None)]}, "card_risk_or_priority_unknown"),
        ({"candidates": [CardFacts("c1", 1, "HIGH", "P1")]}, "card_not_low_risk"),
        ({"candidates": [CardFacts("c1", 1, "LOW", "P9")]}, "card_priority_not_allowed"),
    ],
)
def test_any_missing_or_failing_condition_routes_to_a_human_with_a_fixed_word_reason(over, reason):
    got = route(**over)
    assert not got.auto_approval_eligible and reason in got.reasons


def test_today_nothing_is_eligible_because_no_card_carries_a_risk_class_a_priority_or_a_calibrated_confidence():
    got = route(candidates=[CardFacts("c1", 1)], confidence=None, confidence_calibrated=False)
    assert not got.auto_approval_eligible and set(got.reasons) == {"card_risk_or_priority_unknown", "confidence_unknown_or_uncalibrated"}


def test_the_policy_has_no_defaults_and_refuses_nonsense():
    with pytest.raises(TypeError):
        AutoApprovalPolicy()  # type: ignore[call-arg]
    for bad in (("", frozenset({"P1"}), 0.9), ("LOW", frozenset(), 0.9), ("LOW", frozenset({"P1"}), 0.0), ("LOW", frozenset({"P1"}), 1.5)):
        with pytest.raises(ValueError):
            AutoApprovalPolicy(*bad)


def test_an_overdue_task_escalates_up_the_chain_and_nothing_is_ever_approved_by_timeout():
    assert ESCALATION_CHAIN == ("nurse", "doctor", "admin")
    assert next_escalation("nurse", 101, 100) == "doctor" and next_escalation("doctor", 101, 100) == "admin"
    assert next_escalation("admin", 10**9, 100) is None  # the top of the chain: it stays open, it does not close
    assert next_escalation("nurse", 100, 100) is None and next_escalation("nurse", 5, 100) is None
    assert next_escalation("front_desk", 10**9, 100) is None
    for deadline in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            next_escalation("nurse", 1, deadline)
    assert all(result in (None, *ESCALATION_CHAIN) for result in (next_escalation("nurse", 10**9, 1),))


def test_only_a_human_decision_can_be_a_training_label():
    assert is_training_label_source("human")
    for source in ("auto", "timeout", "", "Human", "canary"):
        assert not is_training_label_source(source)
