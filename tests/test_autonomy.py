"""The approval matrix as data (careflow/autonomy.py; BUILD-SPEC.md 12.1, 12.5): the table, the locked rows, the opt-ins and the unset default. Offline, pure."""

import pytest

from careflow.autonomy import (
    LOCKED_TO_AUTOMATIC,
    LOCKED_TO_HUMAN,
    MATRIX,
    MODES,
    AutonomyChange,
    Policy,
    effective_mode,
    policy_from,
)


@pytest.mark.parametrize("value", [None, "", "Assisted ", "auto", 3, "SHADOW", object()])
def test_an_unset_or_unrecognised_mode_is_shadow_the_most_human_one(value):
    assert effective_mode(value) == "shadow"


@pytest.mark.parametrize("mode", MODES)
def test_an_exact_mode_is_kept(mode):
    assert effective_mode(mode) == mode


def test_bookings_are_human_in_shadow_and_automatic_in_assisted_and_autonomous():
    assert [Policy(m).decider("booking") for m in MODES] == ["human", "automatic", "automatic"]


@pytest.mark.parametrize("action", sorted(LOCKED_TO_HUMAN))
@pytest.mark.parametrize("mode", MODES)
def test_the_rows_locked_toward_a_human_are_never_automatic_in_any_mode_or_with_any_option(action, mode):
    policy = Policy(mode, opt_in_records_release=True, opt_in_triage_approval=True, auto_approve_one_confident_low_risk_card=True)
    assert not policy.may_run_without_a_human_first(action), (action, mode)


@pytest.mark.parametrize("action", sorted(LOCKED_TO_AUTOMATIC))
@pytest.mark.parametrize("mode", MODES)
def test_a_red_flag_and_the_emergency_template_are_always_automatic_with_a_nurse_acknowledgement(action, mode):
    assert Policy(mode).decider(action) == "automatic_ack"


def test_an_out_of_range_readiness_answer_escalates_automatically_but_is_still_decided_by_a_human():
    assert Policy("assisted").decider("readiness_out_of_range") == "human_auto_escalate" and Policy("shadow").decider("readiness_out_of_range") == "human"


def test_the_clinic_opt_ins_default_off_apply_only_in_autonomous_mode_and_only_to_the_rows_the_spec_allows():
    assert Policy("autonomous").decider("records_release") == "human" and Policy("autonomous").decider("triage_approval") == "human"
    assert Policy("autonomous", opt_in_records_release=True).decider("records_release") == "automatic"
    assert Policy("assisted", opt_in_records_release=True).decider("records_release") == "human"  # an opt-in does nothing outside autonomous
    assert Policy("autonomous", opt_in_records_release=True).decider("booking") == "automatic"  # unrelated rows are unaffected
    assert Policy("autonomous", opt_in_triage_approval=True).decider("patient_merge") == "human"  # an opt-in cannot unlock a locked row


def test_the_single_assisted_exception_for_triage_is_off_unless_a_clinician_enables_it_and_only_in_assisted():
    assert Policy("assisted").decider("triage_approval") == "human"
    assert Policy("assisted", auto_approve_one_confident_low_risk_card=True).decider("triage_approval") == "automatic"
    assert Policy("shadow", auto_approve_one_confident_low_risk_card=True).decider("triage_approval") == "human"


def test_an_unknown_action_class_is_decided_by_a_human():
    assert Policy("autonomous").decider("something_new") == "human" and not Policy("autonomous").may_run_without_a_human_first("something_new")


def test_every_row_has_one_entry_per_mode_and_no_locked_row_says_automatic_where_it_must_not():
    assert all(len(row) == 3 for row in MATRIX.values())
    for action in LOCKED_TO_HUMAN:
        assert "automatic" not in MATRIX[action]


def test_switching_to_autonomous_needs_an_admin_and_a_doctor_who_are_different_people():
    assert AutonomyChange("a1", "d1").allowed()
    assert not AutonomyChange("a1", "a1").allowed() and not AutonomyChange("a1", None).allowed() and not AutonomyChange(None, "d1").allowed()


def test_an_unknown_policy_option_is_refused():
    assert policy_from("assisted").mode == "assisted"
    with pytest.raises(ValueError):
        policy_from("assisted", opt_in_booking=True)
