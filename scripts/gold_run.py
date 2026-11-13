"""Run the gold set through the deterministic red-flag rules and write gate evidence (careflow/eval_runner.py). No model, no network, no database.

Usage: python scripts/gold_run.py OUT.json [--rules FILE] [--profile clinical|demo]
  The rules file comes from --rules or the required setting CAREFLOW_EVAL_RULES_FILE (no default). The profile defaults to `clinical`, where only APPROVED rule versions count.
Exit codes: 0 the red-flag recall gate passed (100%); 1 it failed; 2 not measured (no gating case) or the evidence was withheld. Feed OUT.json to scripts/gate_scorecard.py.
"""

import json
import os
import sys
from itertools import pairwise
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow import eval_runner, goldset, rules


def main(argv: list[str]) -> int:
    paths = [a for a in argv if not a.startswith("--")]
    flags = dict(pairwise(argv))
    if not paths:
        print("usage: gold_run.py OUT.json [--rules FILE] [--profile clinical|demo]")
        return 2
    settings = eval_runner.read_settings(os.environ)
    for problem in settings.problems:
        print(f"setting problem: {problem}")
    rules_file = Path(flags["--rules"]) if "--rules" in flags else settings.rules_file
    rows: list[dict] = []
    if rules_file is None:
        print("no rules file: the required setting CAREFLOW_EVAL_RULES_FILE (or --rules) is not set; every red-flag case will count as a miss")
    else:
        rows, load_problems = eval_runner.load_rule_rows(rules_file)
        for problem in load_problems:
            print(f"rules file problem: {problem}")
    profile = rules.resolve_profile(flags.get("--profile"))
    loaded = goldset.load()
    for problem in loaded.problems:
        print(f"gold set problem: {problem}")
    report = eval_runner.run_gold(loaded.cases, goldset.read_lock(), eval_runner.build_ruleset(rows, profile), settings=settings)
    for problem in report.problems:
        print(problem)
    print(f"cases {report.total_cases}, gating {report.gating_cases}, unassessable {report.unassessable_cases}, drafts excluded {report.drafts_excluded}, rules {report.rule_set_hash[:12]}, profile {report.profile}")
    Path(paths[0]).write_text(json.dumps(report.evidence, indent=2), encoding="utf-8")
    entry = report.evidence.get("red_flag_recall")
    if report.refused or entry is None:
        print("red_flag_recall: NOT MEASURED")
        return 2
    passed = entry["value"] == 1.0
    print(f"red_flag_recall: {'PASS' if passed else 'FAIL'} ({entry['source']})")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
