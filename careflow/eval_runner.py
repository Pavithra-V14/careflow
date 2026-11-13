"""The gold-set runner for the deterministic red-flag rules, and the 100% recall gate it feeds (BUILD-SPEC.md 5.2, 14.1, 14.3; docs/design/phase5-evaluation.md). MECHANISM ONLY.

# CLINICAL-PLACEHOLDER: needs clinician sign-off

NOTHING CLINICAL IS WRITTEN HERE. The cases come from `eval/gold/*.yaml` (clinician-reviewed), the rules from a file the clinician's team supplies, the bars from required settings. This module runs the REAL rule
engine (careflow/rules.py, no model, no network, no database) over the gold cases and turns the result into gate evidence:

  * Only a REVIEWED case in the FROZEN held-out split gates (goldset.GoldCase.gates). If the held-out lock does not match the cases, NO evidence is produced: a retuned set must not score.
  * The rules are chosen by the profile exactly as in production: `clinical` (the default) counts APPROVED versions only, so a draft rule scores nothing; `demo` also counts drafts and is labelled in the evidence.
  * A case the rules could not assess (no usable rule, or a rule that failed to compile) is a MISS for red-flag recall, never a pass: unknown is not a value.
  * Only gates this runner really measures are written (`red_flag_recall`). Every other gate stays NOT MEASURED. `red_flag_recall` passes only at exactly 100% (careflow/gates.py).
  * Clinician-owned values are REQUIRED SETTINGS with no defaults: `CAREFLOW_EVAL_RULES_FILE`; the bars `CAREFLOW_EVAL_BAR_<GATE>` and baselines `CAREFLOW_EVAL_BASELINE_<GATE>` (three comma-separated runs) for the threshold gates
    the runner does not measure yet are read and validated here so a typo is reported, not ignored. A set but invalid value is a reported problem, never a silent default.
"""

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import yaml

from careflow import evalmetrics, goldset, rules
from careflow.evalmetrics import Outcome
from careflow.goldset import GoldCase

MEASURED_GATES: Final = ("red_flag_recall",)
THRESHOLD_GATES: Final = ("card_recall_at_3", "correct_abstention_rate", "priority_accuracy")
RULES_FILE_SETTING: Final = "CAREFLOW_EVAL_RULES_FILE"
_NAMESPACE: Final = uuid.UUID("6f1c2d0e-8a6b-4c55-9a53-2d6f6f0e1a11")


@dataclass(frozen=True)
class EvalSettings:
    rules_file: Path | None
    bars: dict[str, float]
    baselines: dict[str, tuple[float, ...]]
    problems: tuple[str, ...] = ()


def read_settings(environ: Mapping[str, str]) -> EvalSettings:
    """No defaults. A value that is set but not usable is a PROBLEM (listed), and is left out."""
    problems: list[str] = []
    raw_file = (environ.get(RULES_FILE_SETTING) or "").strip()
    bars: dict[str, float] = {}
    baselines: dict[str, tuple[float, ...]] = {}
    for gate in THRESHOLD_GATES:
        bar = (environ.get(f"CAREFLOW_EVAL_BAR_{gate.upper()}") or "").strip()
        if bar:
            try:
                value = float(bar)
                if not 0.0 <= value <= 1.0 or value != value:  # noqa: PLR0124
                    raise ValueError
                bars[gate] = value
            except ValueError:
                problems.append(f"CAREFLOW_EVAL_BAR_{gate.upper()} must be a number from 0 to 1")
        base = (environ.get(f"CAREFLOW_EVAL_BASELINE_{gate.upper()}") or "").strip()
        if base:
            try:
                runs = tuple(float(x) for x in base.split(","))
                if len(runs) != evalmetrics.BASELINE_RUNS or any(not 0.0 <= r <= 1.0 for r in runs):
                    raise ValueError
                baselines[gate] = runs
            except ValueError:
                problems.append(f"CAREFLOW_EVAL_BASELINE_{gate.upper()} must be exactly {evalmetrics.BASELINE_RUNS} comma-separated numbers from 0 to 1")
    return EvalSettings(Path(raw_file) if raw_file else None, bars, baselines, tuple(problems))


def load_rule_rows(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """Rule rows from a YAML file: `rules:` a list of {rule_code, version_id, status, phrases, patterns, clinic_id?}. Malformed rows are problems and left out. The id is derived from code and version
    so the rule-set hash is stable between runs."""
    problems: list[str] = []
    rows: list[dict[str, Any]] = []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = doc.get("rules") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return [], [f"{path.name}: needs a `rules` list"]
    for i, raw in enumerate(items):
        if not isinstance(raw, dict) or not isinstance(raw.get("rule_code"), str) or not isinstance(raw.get("version_id"), int) or raw.get("status") not in ("draft_unreviewed", "approved", "retired"):
            problems.append(f"{path.name}[{i}]: needs rule_code, an integer version_id and a status of draft_unreviewed, approved or retired")
            continue
        rows.append({
            "id": uuid.uuid5(_NAMESPACE, f"{raw.get('clinic_id')}:{raw['rule_code']}:{raw['version_id']}"), "clinic_id": raw.get("clinic_id"), "rule_code": raw["rule_code"], "version_id": raw["version_id"],
            "status": raw["status"], "phrases": list(raw.get("phrases") or []), "patterns": list(raw.get("patterns") or []), "token_sets": [list(t) for t in raw.get("token_sets") or []],
        })
    return rows, problems


def build_ruleset(rows: list[dict[str, Any]], profile: rules.Profile) -> rules.RuleSet:
    chosen, drafts_passed = rules.select_active_versions(rows, profile)
    compiled, problems = [], []
    for row in chosen:
        rule, problem = rules.compile_rule(row)
        if rule is not None:
            compiled.append(rule)
        elif problem:
            problems.append(problem)
    return rules.RuleSet(tuple(compiled), profile, drafts_passed, tuple(problems), rules.rule_set_hash(chosen))


def rules_pipeline(ruleset: rules.RuleSet):
    """The pipeline under test: the real engine, rules only. `unassessable` leaves red_flag_fired None (a miss for recall), never False."""

    def run(case: GoldCase) -> Outcome:
        evaluation = rules.evaluate_with(ruleset, case.text)
        fired: bool | None = True if evaluation.outcome == "red_flag" else (False if evaluation.outcome == "clear" else None)
        return Outcome(red_flag_fired=fired)

    return run


@dataclass
class RunReport:
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    profile: str = "clinical"
    rule_set_hash: str = rules.EMPTY_RULE_SET_HASH
    gating_cases: int = 0
    total_cases: int = 0
    unassessable_cases: int = 0
    drafts_excluded: int = 0
    refused: bool = False  # the evidence was withheld (a frozen case changed, or the inputs were unusable)


def run_gold(cases: list[GoldCase], lock: dict[str, str], ruleset: rules.RuleSet, *, settings: EvalSettings | None = None) -> RunReport:
    """Run the rules over the gold cases and build the evidence for the gates this runner measures. Withholds ALL evidence when the frozen held-out set no longer matches its lock."""
    report = RunReport(profile=ruleset.profile, rule_set_hash=ruleset.rule_set_hash, total_cases=len(cases), drafts_excluded=ruleset.drafts_excluded)
    tampered = goldset.held_out_problems(cases, lock)
    if tampered:
        report.problems += tampered
        report.refused = True
        return report
    pipeline = rules_pipeline(ruleset)
    pairs = evalmetrics.run(cases, pipeline)
    report.gating_cases = sum(1 for c, _ in pairs if c.gates)
    report.unassessable_cases = sum(1 for c, o in pairs if c.gates and o.red_flag_fired is None)
    label = f"gold run rules={ruleset.rule_set_hash[:12]} profile={ruleset.profile}"
    everything = evalmetrics.evidence_for_gates(pairs, source=label, bars=settings.bars if settings else None, baselines=settings.baselines if settings else None)
    report.evidence = {gate: entry for gate, entry in everything.items() if gate in MEASURED_GATES}
    if ruleset.profile != "clinical":
        report.evidence = {}  # drafts were counted: a demo run is never release evidence
        report.problems.append("profile demo: draft rules were counted, so no gate evidence is written")
    return report


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
