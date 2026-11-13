"""The gold-set runner and the 100% red-flag recall gate (careflow/eval_runner.py). Offline. The words are MEANINGLESS markers: no clinical content."""

import json
from pathlib import Path

from careflow import eval_runner, gates, goldset, rules
from careflow.goldset import GoldCase

OK = {"on": "2026-01-01", "role": "clinician", "by": "reviewer"}


def case(cid, text, red_flag, *, split="held_out", reviewed=True):
    return GoldCase(
        id=cid, kind="message", split=split, language="en", text=text, expected={"red_flag": red_flag}, authored_by="llm",
        reviewed_by=OK["by"] if reviewed else None, reviewer_role=OK["role"] if reviewed else None, reviewed_on=OK["on"] if reviewed else None,
    )


def rule_rows(status):
    return [{"id": "00000000-0000-0000-0000-000000000001", "clinic_id": None, "rule_code": "zz_marker", "version_id": 1, "status": status, "phrases": ["zzmarker"], "patterns": []}]


def lock_for(cases):
    return goldset.freeze_manifest([c for c in cases if c.split == "held_out"])


def run(cases, status="approved", profile="clinical"):
    return eval_runner.run_gold(cases, lock_for(cases), eval_runner.build_ruleset(rule_rows(status), profile))


def test_every_expected_red_flag_caught_passes_the_hard_gate():
    cases = [case("a", "text with zzmarker inside", True), case("b", "nothing here", False)]
    report = run(cases)
    assert report.evidence["red_flag_recall"]["value"] == 1.0
    assert not gates.evaluate(report.evidence).failed


def test_one_missed_red_flag_fails_the_gate_at_exactly_less_than_one():
    cases = [case("a", "text with zzmarker inside", True), case("b", "a different phrase", True)]
    entry = run(cases).evidence["red_flag_recall"]
    assert entry["value"] == 0.5 and gates.evaluate({"red_flag_recall": entry}).failed


def test_a_draft_rule_scores_nothing_in_the_clinical_profile_and_the_case_is_a_miss():
    cases = [case("a", "text with zzmarker inside", True)]
    report = run(cases, status="draft_unreviewed")
    assert report.evidence["red_flag_recall"]["value"] == 0.0 and report.unassessable_cases == 1 and report.drafts_excluded == 1


def test_a_demo_run_counts_drafts_but_writes_no_evidence():
    report = run([case("a", "text with zzmarker inside", True)], status="draft_unreviewed", profile="demo")
    assert report.evidence == {} and any("no gate evidence" in p for p in report.problems)


def test_unreviewed_and_tuning_cases_never_gate():
    cases = [case("a", "zzmarker", True, reviewed=False), case("b", "zzmarker", True, split="tuning")]
    report = run(cases)
    assert report.gating_cases == 0 and report.evidence == {}  # not measured, not 0 and not 1


def test_a_changed_held_out_case_withholds_all_evidence():
    cases = [case("a", "text with zzmarker inside", True)]
    lock = lock_for(cases)
    changed = [case("a", "text edited after the freeze zzmarker", True)]
    report = eval_runner.run_gold(changed, lock, eval_runner.build_ruleset(rule_rows("approved"), "clinical"))
    assert report.refused and report.evidence == {} and report.problems


def test_no_rules_at_all_is_a_miss_never_a_pass():
    report = eval_runner.run_gold([case("a", "zzmarker", True)], lock_for([case("a", "zzmarker", True)]), eval_runner.build_ruleset([], "clinical"))
    assert report.evidence["red_flag_recall"]["value"] == 0.0


def test_settings_have_no_defaults_and_a_bad_value_is_reported_not_ignored():
    assert eval_runner.read_settings({}) == eval_runner.EvalSettings(None, {}, {}, ())
    s = eval_runner.read_settings({"CAREFLOW_EVAL_BAR_PRIORITY_ACCURACY": "1.5", "CAREFLOW_EVAL_BASELINE_CARD_RECALL_AT_3": "0.9,0.9", "CAREFLOW_EVAL_BAR_CARD_RECALL_AT_3": "0.8"})
    assert s.bars == {"card_recall_at_3": 0.8} and len(s.problems) == 2 and s.baselines == {}


def test_the_rules_file_loader_reports_malformed_rows(tmp_path: Path):
    f = tmp_path / "r.yaml"
    f.write_text("rules:\n  - {rule_code: zz_a, version_id: 1, status: approved, phrases: [zzmarker]}\n  - {rule_code: bad}\n", encoding="utf-8")
    rows, problems = eval_runner.load_rule_rows(f)
    assert len(rows) == 1 and len(problems) == 1 and rows[0]["id"] == eval_runner.load_rule_rows(f)[0][0]["id"]  # a stable id


def test_the_cli_exit_codes(tmp_path: Path, monkeypatch, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("gold_run", Path(__file__).resolve().parent.parent / "scripts" / "gold_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.delenv("CAREFLOW_EVAL_RULES_FILE", raising=False)
    out = tmp_path / "out.json"
    assert mod.main([str(out)]) == 2  # the shipped gold set has no reviewed case: not measured
    assert json.loads(out.read_text()) == {}
    assert "NOT MEASURED" in capsys.readouterr().out
    assert rules.resolve_profile(None) in ("demo", "clinical")
