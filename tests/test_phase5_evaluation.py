"""Phase 5 mechanism, offline: the gold-set loader and its review rules, the metrics, the variance band, the judge rule and the pre-deployment go/no-go (BUILD-SPEC.md 14). The fixture cases here
are MECHANISM FIXTURES with made-up labels (DEMO-*): they say nothing clinical and are never the gold set (which lives in eval/gold/ and is authored and reviewed by a clinician)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from careflow import evalmetrics, goldset, predeploy
from careflow.evalmetrics import Outcome
from careflow.gates import GATES, evaluate, evaluate_gate
from careflow.goldset import GoldCase

ROOT = Path(__file__).resolve().parent.parent
REVIEW = {"by": "Dr Example", "role": "clinician", "on": "2026-11-06"}


def case(cid="c1", *, split="held_out", kind="referral", language="en", expected=None, review=REVIEW, authored_by="llm"):
    raw = {"id": cid, "kind": kind, "split": split, "language": language, "text": "DEMO synthetic text", "expected": expected or {}, "authored_by": authored_by}
    if review is not None:
        raw["review"] = review
    problems: list[str] = []
    made = goldset._case(raw, "t", problems)
    assert made is not None and not problems, problems
    return made


# ---- the case format and the review rule ------------------------------------------------------------------------------------------------------------------


def test_only_a_clinician_reviewed_held_out_case_can_gate():
    assert case().gates
    assert not case(split="tuning").gates  # tuning never gates
    assert not case(review=None).gates  # unreviewed
    assert not case(review={**REVIEW, "role": "engineer"}).gates
    assert not case(review={"by": "Dr Example", "role": "clinician"}).gates  # no date
    assert not case(review={**REVIEW, "by": "llm"}, authored_by="llm").gates  # the author cannot review their own case


@pytest.mark.parametrize(
    "mutate",
    [lambda r: r.pop("expected"), lambda r: r.update(kind="other"), lambda r: r.update(split="all"), lambda r: r.update(language="fr"), lambda r: r.update(text="  "), lambda r: r.update(id="")],
)
def test_a_malformed_case_is_a_problem_and_left_out(mutate):
    raw = {"id": "x", "kind": "message", "split": "tuning", "language": "en", "text": "t", "expected": {}, "authored_by": "llm"}
    mutate(raw)
    problems: list[str] = []
    assert goldset._case(raw, "f", problems) is None and problems


def test_load_reports_duplicates_and_bad_files_and_a_missing_folder_is_empty(tmp_path):
    assert goldset.load(tmp_path / "nowhere").cases == []
    good = {"id": "a", "kind": "message", "split": "tuning", "language": "ta", "text": "t", "expected": {}, "authored_by": "llm"}
    (tmp_path / "one.yaml").write_text(yaml.safe_dump({"cases": [good, good]}), encoding="utf-8")
    (tmp_path / "two.yaml").write_text("not: cases", encoding="utf-8")
    loaded = goldset.load(tmp_path)
    assert [c.id for c in loaded.cases] == ["a"] and any("duplicate" in p for p in loaded.problems) and any("needs a `cases` list" in p for p in loaded.problems)


def test_the_repository_gold_set_is_empty_until_a_clinician_reviews_one():
    loaded = goldset.load()
    assert loaded.problems == []
    assert not [c for c in loaded.cases if c.gates and c.authored_by == c.reviewed_by]
    assert goldset.held_out_problems(loaded.cases, goldset.read_lock()) == []


def test_a_frozen_held_out_case_cannot_be_edited_or_removed_quietly():
    a, b = case("a"), case("b")
    lock = goldset.freeze_manifest([a, b, case("t", split="tuning")])
    assert set(lock) == {"a", "b"}  # tuning is never locked
    assert goldset.held_out_problems([a, b], lock) == []
    edited = GoldCase(**{**a.__dict__, "text": "edited"})
    assert any("a changed" in p for p in goldset.held_out_problems([edited, b], lock))
    assert any("b was removed" in p for p in goldset.held_out_problems([a], lock))
    assert any("c is not in the lock" in p for p in goldset.held_out_problems([a, b, case("c")], lock))


def test_the_report_counts_tamil_apart_and_the_target_only_as_a_report():
    cases = [case("a", language="ta"), case("b", language="en"), case("c", language="tanglish", review=None), case("d", kind="message", split="tuning")]
    rep = goldset.report(cases)
    assert rep["tamil"] == {"total": 2, "gating": 1} and rep["total"] == 4 and rep["gating"] == 2 and rep["unreviewed"] == 1
    assert rep["against_starting_size"]["referral"] == "3 of 60"


# ---- metrics ------------------------------------------------------------------------------------------------------------------------------------------------


def test_a_metric_with_no_relevant_case_has_no_value_not_zero_and_not_one():
    pairs = [(case(expected={}), Outcome())]
    for fn in (evalmetrics.red_flag_recall, evalmetrics.urgent_case_recall, evalmetrics.card_recall_at_3, evalmetrics.correct_abstention_rate, evalmetrics.priority_accuracy):
        assert fn(pairs).value is None


def test_recall_counts_an_unknown_outcome_as_a_miss():
    pairs = [(case("a", expected={"red_flag": True}), Outcome(red_flag_fired=True)), (case("b", expected={"red_flag": True}), Outcome())]
    m = evalmetrics.red_flag_recall(pairs)
    assert (m.value, m.n, m.hits) == (0.5, 2, 1)


def test_card_recall_is_the_share_of_expected_cards_in_the_top_three():
    pairs = [(case("a", expected={"card_ids": ["x", "y"]}), Outcome(card_ids=("x", "q", "r", "y")))]  # y is fourth: not counted
    assert evalmetrics.card_recall_at_3(pairs).value == 0.5


def test_injection_and_wrong_merge_count_unknown_as_bad():
    inj = [(case("a", kind="adversarial", expected={"injection": True}), Outcome())]
    assert evalmetrics.injection_undetected(inj).hits == 1
    inj_ok = [(case("a", kind="adversarial", expected={"injection": True}), Outcome(injection_effect_undetected=False))]
    assert evalmetrics.injection_undetected(inj_ok).hits == 0
    merge = [(case("p", expected={"identity_outcome": "separate"}), Outcome(identity_outcome="merge")), (case("q", expected={"identity_outcome": "merge"}), Outcome(identity_outcome="merge"))]
    m = evalmetrics.wrong_merges(merge)
    assert (m.hits, m.n) == (1, 1)  # a pair that SHOULD merge is not a wrong merge


def test_variance_band_needs_exactly_three_runs_and_is_mean_minus_two_sd():
    band = evalmetrics.variance_band([0.90, 0.92, 0.94])
    assert band is not None and round(band.mean, 4) == 0.92 and round(band.low, 4) == 0.88
    for bad in ([0.9], [0.9, 0.9], [0.9, 0.9, 0.9, 0.9], [0.9, float("nan"), 0.9], [0.9, True, 0.9]):
        assert evalmetrics.variance_band(bad) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "ok"),
    [
        ({"judge_model": "j", "generator_model": "g", "agreement": 0.9, "cases": 30, "bar": 0.8}, True),
        ({"judge_model": "g", "generator_model": "g", "agreement": 0.9, "cases": 30, "bar": 0.8}, False),  # the judge is the generator
        ({"judge_model": "j", "generator_model": "g", "agreement": 0.9, "cases": 29, "bar": 0.8}, False),  # too few cases
        ({"judge_model": "j", "generator_model": "g", "agreement": 0.7, "cases": 40, "bar": 0.8}, False),
        ({"judge_model": "j", "generator_model": "g", "agreement": 0.9, "cases": 40, "bar": None}, False),  # no bar set
        ({"judge_model": None, "generator_model": "g", "agreement": 0.9, "cases": 40, "bar": 0.8}, False),
    ],
)
def test_a_judge_gates_only_when_different_measured_and_above_a_set_bar(kwargs, ok):
    assert evalmetrics.judge_may_gate(**kwargs)[0] is ok


# ---- evidence into the scorecard ----------------------------------------------------------------------------------------------------------------------------


def test_only_gating_cases_make_evidence_and_the_scorecard_reads_it():
    gating = case("g", expected={"red_flag": True, "priority": "DEMO-A"})
    loose = case("l", split="tuning", expected={"red_flag": True})
    pairs = [(gating, Outcome(red_flag_fired=True, priority="DEMO-A")), (loose, Outcome(red_flag_fired=False))]  # the tuning miss is ignored
    ev = evalmetrics.evidence_for_gates(pairs, source="run 1", bars={"priority_accuracy": 0.5}, baselines={"priority_accuracy": [0.9, 0.92, 0.94]})
    assert ev["red_flag_recall"]["value"] == 1.0 and "urgent_case_recall" not in ev  # no urgent case: no entry, so NOT MEASURED
    card = evaluate(ev)
    by = {r.gate.id: r.status for r in card.results}
    assert by["red_flag_recall"] == "MEASURED" and by["priority_accuracy"] == "MEASURED" and by["urgent_case_recall"] == "NOT MEASURED" and not card.releasable


def test_a_threshold_gate_uses_the_variance_band_and_a_baseline_below_the_bar_fails():
    gate = next(g for g in GATES if g.id == "card_recall_at_3")
    base = {"source": "r", "bar": 0.8, "baseline_mean": 0.9, "band_low": 0.86}
    assert evaluate_gate(gate, {**base, "value": 0.87}).status == "MEASURED"  # above the band though below the baseline
    assert evaluate_gate(gate, {**base, "value": 0.85}).status == "FAILED"
    assert evaluate_gate(gate, {**base, "baseline_mean": 0.7, "value": 0.99}).status == "FAILED"  # the baseline itself must reach the bar
    assert evaluate_gate(gate, {"source": "r", "bar": 0.8, "baseline_mean": 0.9, "value": 0.99}).status == "NOT MEASURED"  # a half-given band is malformed
    assert evaluate_gate(gate, {"source": "r", "value": 0.99}).status == "NOT MEASURED"  # no bar set


def test_no_threshold_number_is_in_the_evaluation_code():
    for name in ("goldset.py", "evalmetrics.py", "predeploy.py"):
        text = (ROOT / "careflow" / name).read_text(encoding="utf-8")
        assert "0.80" not in text and "0.95" not in text and "0.90" not in text


# ---- the pre-deployment go/no-go ----------------------------------------------------------------------------------------------------------------------------


def rubric():
    return predeploy.load_rubric()


def claim(**kw):
    return {"evidence": "ci run 1", "verified_on": "2026-11-06", **kw}


def full_scorecard(skip=()):
    return {i["id"]: claim(met=True) for items in rubric().values() for i in items if i["id"] not in skip}


def full_evidence():
    return {
        "scorecard": full_scorecard(),
        "load_test": claim(met=True),
        "security_scan": claim(critical_open=0, high_open=0),
        "red_team": claim(pass_rate=1.0),
        "compliance": claim(pass_rate=1.0),
        "rollback_drill": claim(end_to_end=True, seconds=240),
        "cost_projection": claim(projected=110, actual=100),
        "on_call_runbooks": claim(met=True),
        "golden_set_and_band": claim(met=True),
        "residency_access_audit": claim(met=True),
        "harness_contract_chaos": claim(pass_rate=1.0),
        "business_owner_signoff": claim(met=True),
    }


def test_the_rubric_is_five_categories_of_twenty_points_and_the_twelve_checks_are_all_there():
    r = rubric()
    assert list(r) == list(predeploy.CATEGORIES) and all(sum(i["points"] for i in v) == 20 for v in r.values())
    assert len(predeploy.CHECKS) == 12 and len(set(predeploy.CHECK_IDS)) == 12


def test_with_no_evidence_the_answer_is_no_go_and_nothing_is_met():
    lines = predeploy.evaluate_checks({}, rubric())
    assert not predeploy.go(lines) and {ln.status for ln in lines} == {"NOT MEASURED"}
    repo = yaml.safe_load(predeploy.EVIDENCE.read_text(encoding="utf-8"))
    assert not predeploy.go(predeploy.evaluate_checks(repo, rubric()))  # the committed evidence file claims nothing


def test_full_evidence_is_go():
    lines = predeploy.evaluate_checks(full_evidence(), rubric())
    assert predeploy.go(lines), [(ln.id, ln.status) for ln in lines]


def test_a_claim_without_a_pointer_or_a_date_counts_for_nothing():
    for broken in ({"met": True}, {"met": True, "evidence": "x"}, {"met": True, "evidence": "x", "verified_on": "yesterday"}, {"met": True, "evidence": " ", "verified_on": "2026-11-06"}):
        ev = full_evidence()
        ev["on_call_runbooks"] = broken
        status = {ln.id: ln.status for ln in predeploy.evaluate_checks(ev, rubric())}
        assert status["on_call_runbooks"] == "NOT MEASURED"
    ev = full_evidence()
    ev["scorecard"]["rls_forced_everywhere"] = {"met": True}
    assert predeploy.score(rubric(), ev).total == 95


@pytest.mark.parametrize(
    ("check", "bad"),
    [
        ("security_scan", claim(critical_open=0, high_open=1)),
        ("red_team", claim(pass_rate=0.99)),
        ("compliance", claim(pass_rate=0.5)),
        ("harness_contract_chaos", claim(pass_rate=0.999)),
        ("rollback_drill", claim(end_to_end=True, seconds=300)),
        ("rollback_drill", claim(end_to_end=False, seconds=10)),
        ("cost_projection", claim(projected=121, actual=100)),
        ("cost_projection", claim(projected=79, actual=100)),
        ("load_test", claim(met=False)),
        ("business_owner_signoff", claim(met=False)),
    ],
)
def test_each_check_fails_closed(check, bad):
    ev = full_evidence()
    ev[check] = bad
    assert not predeploy.go(predeploy.evaluate_checks(ev, rubric()))


def test_the_scorecard_needs_seventy_in_total_and_twelve_in_every_category():
    r = rubric()
    arch = [i["id"] for i in r["architecture"]]
    sc = predeploy.score(r, {"scorecard": full_scorecard(skip=arch[:3])})  # architecture 5 of 20, the rest full: 85 in total
    assert sc.total == 85 and not sc.passes  # a category below 12 fails whatever the total
    sc2 = predeploy.score(r, {"scorecard": full_scorecard(skip=arch[:2])})  # architecture 10, still below the floor
    assert not sc2.passes
    sc3 = predeploy.score(r, {"scorecard": full_scorecard(skip=arch[:1])})  # architecture 15
    assert sc3.passes and sc3.total == 95


def test_the_scripts_print_no_go_today_and_fail_a_non_report_run():
    for script in ("predeploy_check.py",):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], capture_output=True, text=True, check=False)
        assert done.returncode == 1 and "NO-GO" in done.stdout
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--report-only"], capture_output=True, text=True, check=False)
        assert done.returncode == 0
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "gold_freeze.py")], capture_output=True, text=True, check=False)
    assert done.returncode == 0 and json.loads(done.stdout.splitlines()[-1])["total"] == 0
