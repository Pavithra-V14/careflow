"""Drill records become pre-deployment evidence only when they actually show the drill (careflow/drills.py). Offline; nothing is executed. Records here are synthetic."""

from datetime import UTC, date, datetime, timedelta

import pytest
import yaml

from careflow import drills, predeploy
from careflow.drills import (
    RESTORE_CHECKS,
    ROLLBACK_STEPS,
    RestoreRecord,
    RollbackRecord,
    Step,
)

T0 = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
DAY = date(2026, 11, 6)


def steps(gap=0, each=40, ok=True, names=ROLLBACK_STEPS):
    out, t = [], T0
    for name in names:
        out.append(Step(name, t, t + timedelta(seconds=each), ok))
        t += timedelta(seconds=each + gap)
    return tuple(out)


def rb(**kw):
    return RollbackRecord(kw.pop("steps", steps()), kw.pop("executed_by", "DEMO engineer"), kw.pop("evidence", "drill log DEMO-1"), DAY)


def test_a_complete_ordered_drill_under_five_minutes_becomes_the_evidence_claim():
    claim, problems = drills.rollback_evidence(rb())
    assert problems == [] and claim == {"end_to_end": True, "seconds": 160, "evidence": "drill log DEMO-1", "verified_on": "2026-11-06"}


@pytest.mark.parametrize(
    ("record", "problem"),
    [
        (rb(steps=steps()[:-1]), "steps_missing_extra_or_out_of_order"),
        (rb(steps=tuple(reversed(steps()))), "steps_missing_extra_or_out_of_order"),
        (rb(steps=steps(names=(*ROLLBACK_STEPS, "extra"))), "steps_missing_extra_or_out_of_order"),
        (rb(steps=steps(ok=False)), "a_step_failed"),
        (rb(steps=steps(each=75)), "five_minutes_or_more"),  # 4 x 75 = exactly 300 seconds
        (rb(executed_by=" "), "no_executor_or_evidence"),
        (rb(evidence=""), "no_executor_or_evidence"),
    ],
)
def test_a_drill_that_is_incomplete_failed_or_too_slow_gives_no_claim(record, problem):
    claim, problems = drills.rollback_evidence(record)
    assert claim is None and problem in problems


def test_a_step_that_ends_before_it_starts_or_overlaps_the_next_is_not_a_drill():
    bad = list(steps())
    bad[1] = Step(bad[1].name, bad[1].started_at, bad[1].started_at - timedelta(seconds=5), True)
    assert "a_step_ends_before_it_starts" in drills.rollback_problems(rb(steps=tuple(bad)))
    overlap = list(steps())
    overlap[2] = Step(overlap[2].name, overlap[1].started_at, overlap[2].ended_at, True)
    assert "steps_overlap_or_are_not_in_time_order" in drills.rollback_problems(rb(steps=tuple(overlap)))


def rs(**changes):
    checks = {c: True for c in RESTORE_CHECKS} | changes
    return RestoreRecord(checks, "DEMO engineer", "restore log DEMO-2", DAY)


def test_a_restore_with_every_check_true_becomes_the_claim():
    assert drills.restore_evidence(rs()) == ({"met": True, "evidence": "restore log DEMO-2", "verified_on": "2026-11-06"}, [])


def test_the_append_only_trigger_check_is_in_the_list_because_a_restore_can_silently_disable_them():
    assert "append_only_triggers_fire" in RESTORE_CHECKS


@pytest.mark.parametrize("check", RESTORE_CHECKS)
def test_each_failed_or_missing_check_gives_no_claim(check):
    claim, problems = drills.restore_evidence(rs(**{check: False}))
    assert claim is None and f"check_failed:{check}" in problems
    missing = {c: True for c in RESTORE_CHECKS if c != check}
    claim, problems = drills.restore_evidence(RestoreRecord(missing, "x", "y", DAY))
    assert claim is None and f"check_missing:{check}" in problems


def test_a_non_boolean_or_unknown_check_does_not_count():
    assert "check_not_boolean:row_counts_match" in drills.restore_problems(rs(row_counts_match="yes"))
    assert "unknown_check:made_up" in drills.restore_problems(rs(made_up=True))


def test_the_claims_are_exactly_what_the_predeploy_check_accepts():
    claim, _ = drills.rollback_evidence(rb())
    evidence = {"rollback_drill": claim}
    lines = {ln.id: ln.status for ln in predeploy.evaluate_checks(evidence, predeploy.load_rubric())}
    assert lines["rollback_drill"] == "MET"
    restore_claim, _ = drills.restore_evidence(rs())
    scorecard = {"scorecard": {"restore_drilled": restore_claim}}
    assert predeploy.score(predeploy.load_rubric(), scorecard).by_category["operations"] == 5
    assert yaml.safe_dump(evidence)  # serialises into the evidence file's format
