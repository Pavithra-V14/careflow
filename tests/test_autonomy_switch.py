"""Offline: the effective policy with a stored `autonomous` (owner decision of 2026-11-08, item 7) and the owner-run consent seed plan (item 3)."""

from pathlib import Path

import pytest

from careflow.autonomy import (
    LOCKED_TO_AUTOMATIC,
    LOCKED_TO_HUMAN,
    MATRIX,
    Policy,
    effective_policy,
)
from careflow.consent_settings import NoticeDef, parse, plan_publication

OFF = {"autonomous_enabled": False, "profile": "demo", "clinical_signoff": False}


def test_a_stored_autonomous_is_ignored_unless_the_enabling_setting_is_on():
    assisted = Policy(mode="assisted")
    assert effective_policy(assisted, "autonomous", **OFF).mode == "assisted"  # unset = off
    assert effective_policy(assisted, "autonomous", autonomous_enabled=True, profile="demo", clinical_signoff=False).mode == "autonomous"


def test_in_the_clinical_profile_it_is_ignored_until_the_clinician_signoff_setting_is_present():
    shadow = Policy(mode="shadow")
    assert effective_policy(shadow, "autonomous", autonomous_enabled=True, profile="clinical", clinical_signoff=False).mode == "shadow"
    assert effective_policy(shadow, "autonomous", autonomous_enabled=True, profile="clinical", clinical_signoff=True).mode == "autonomous"
    assert effective_policy(shadow, "autonomous", autonomous_enabled=False, profile="clinical", clinical_signoff=True).mode == "shadow"  # the signoff alone is not enough


def test_the_other_stored_modes_still_only_tighten():
    assisted = Policy(mode="assisted")
    assert effective_policy(assisted, "shadow", **OFF).mode == "shadow"
    assert effective_policy(assisted, "assisted", **OFF) is assisted
    assert effective_policy(assisted, None, **OFF) is assisted
    assert effective_policy(Policy(mode="autonomous"), "shadow", **OFF).mode == "shadow"


@pytest.mark.parametrize("mode", ["shadow", "assisted", "autonomous"])
def test_the_locked_rows_of_the_approval_matrix_never_move_in_any_mode_even_with_every_opt_in(mode):
    policy = Policy(mode=mode, opt_in_records_release=True, opt_in_triage_approval=True, auto_approve_one_confident_low_risk_card=True)
    for row in LOCKED_TO_HUMAN:
        assert policy.decider(row) in {"human", "human_auto_escalate", "human_confirms"}, (mode, row)
        assert not policy.may_run_without_a_human_first(row), (mode, row)
    for row in LOCKED_TO_AUTOMATIC:
        assert policy.decider(row) == "automatic_ack", (mode, row)  # a red flag is always handled at once with a nurse acknowledgement
    assert set(LOCKED_TO_HUMAN) | set(LOCKED_TO_AUTOMATIC) <= set(MATRIX)


def test_the_opt_ins_carry_over_and_unknown_classes_stay_human():
    configured = Policy(mode="assisted", opt_in_records_release=True)
    promoted = effective_policy(configured, "autonomous", autonomous_enabled=True, profile="demo", clinical_signoff=False)
    assert promoted.opt_in_records_release is True and promoted.decider("an_unknown_class") == "human"


GOOD = '{"care_delivery": {"version": 2, "body": "DEMO ONLY text"}, "ai_processing": {"version": 1, "body": "DEMO ONLY other"}}'


def test_the_seed_plan_publishes_only_the_next_version_and_never_overwrites_or_skips():
    wanted = parse(GOOD)
    assert wanted is not None
    assert plan_publication({"care_delivery": [(1, "old")]}, wanted) == [("publish", "care_delivery", 2), ("publish", "ai_processing", 1)]
    assert plan_publication({"care_delivery": [(1, "old"), (2, "DEMO ONLY text")], "ai_processing": [(1, "DEMO ONLY other")]}, wanted) == [("exists", "care_delivery", 2), ("exists", "ai_processing", 1)]
    assert plan_publication({"care_delivery": [(1, "old"), (2, "DIFFERENT")]}, wanted)[0] == ("conflict", "care_delivery", 2)
    assert plan_publication({}, wanted)[0] == ("version_gap", "care_delivery", 2)  # version 2 with no version 1 published
    assert plan_publication({}, {"care_delivery": NoticeDef("care_delivery", 1, "x")}) == [("publish", "care_delivery", 1)]


def test_no_route_or_application_function_publishes_a_notice_any_more():
    root = Path(__file__).resolve().parent.parent
    assert "publish_consent_notice" not in (root / "careflow" / "app.py").read_text(encoding="utf-8")
    assert "publish_consent_notice" not in (root / "careflow" / "portal_sql.py").read_text(encoding="utf-8")
