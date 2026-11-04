"""The gate scorecard fails closed and invents no number (careflow/gates.py; BUILD-SPEC.md 14.3). Offline, pure."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from careflow.gates import GATE_IDS, GATES, Gate, evaluate, evaluate_gate, render

ROOT = Path(__file__).resolve().parent.parent
HARD = next(g for g in GATES if g.id == "idempotency_tests")
RECALL = next(g for g in GATES if g.id == "red_flag_recall")
THRESHOLD = next(g for g in GATES if g.id == "priority_accuracy")
REPORTED = next(g for g in GATES if g.id == "latency")
SRC = "ci run 1"


def test_with_no_evidence_every_gate_is_not_measured_and_nothing_is_releasable():
    card = evaluate()
    assert {r.status for r in card.results} == {"NOT MEASURED"} and not card.releasable and not card.failed
    assert len(card.results) == len(GATES) == len(GATE_IDS)  # ids are unique


def test_the_spec_gates_are_all_present_by_kind():
    kinds = {g.id: g.kind for g in GATES}
    assert sum(k == "hard" for k in kinds.values()) == 10 and sum(k == "threshold" for k in kinds.values()) == 3 and sum(k == "reported" for k in kinds.values()) == 3
    assert kinds["voice_safety_escalation_rate"] == "hard" and kinds["red_flag_recall"] == "hard"


@pytest.mark.parametrize(
    ("gate", "entry", "status"),
    [
        (HARD, {"passed": True, "source": SRC}, "MEASURED"),
        (HARD, {"passed": False, "source": SRC}, "FAILED"),
        (HARD, {"passed": "yes", "source": SRC}, "NOT MEASURED"),  # not a bool: unknown is not a value
        (HARD, {"passed": True}, "NOT MEASURED"),  # no source
        (HARD, {"passed": True, "source": ""}, "NOT MEASURED"),
        (HARD, "passed", "NOT MEASURED"),
        (RECALL, {"value": 1.0, "source": SRC}, "MEASURED"),
        (RECALL, {"value": 1, "source": SRC}, "MEASURED"),
        (RECALL, {"value": 0.999, "source": SRC}, "FAILED"),  # 100% means 100%
        (RECALL, {"value": float("nan"), "source": SRC}, "NOT MEASURED"),
        (RECALL, {"value": True, "source": SRC}, "NOT MEASURED"),  # a bool is not a number
        (RECALL, {"value": "1.0", "source": SRC}, "NOT MEASURED"),
        (THRESHOLD, {"value": 0.9, "bar": 0.8, "source": SRC}, "MEASURED"),
        (THRESHOLD, {"value": 0.7, "bar": 0.8, "source": SRC}, "FAILED"),
        (THRESHOLD, {"value": 0.9, "source": SRC}, "NOT MEASURED"),  # no bar: no clinician number, no verdict
        (THRESHOLD, {"value": 0.9, "bar": None, "source": SRC}, "NOT MEASURED"),
        (REPORTED, {"value": 120, "source": SRC}, "MEASURED"),
        (REPORTED, {"value": None, "source": SRC}, "NOT MEASURED"),
    ],
)
def test_one_gate_per_kind(gate: Gate, entry, status):
    assert evaluate_gate(gate, entry).status == status


def test_a_reported_gate_can_never_fail_or_block():
    entries = {g.id: {"passed": True, "value": 1.0, "bar": 0.5, "source": SRC} for g in GATES if g.kind != "reported"}
    card = evaluate(entries)
    assert card.releasable and all(r.status == "NOT MEASURED" for r in card.results if r.gate.kind == "reported")  # unmeasured reported gates do not block


def test_everything_measured_and_passing_is_releasable_but_one_failure_or_one_gap_is_not():
    full = {g.id: {"passed": True, "value": 1.0, "bar": 0.5, "source": SRC} for g in GATES}
    assert evaluate(full).releasable
    assert not evaluate({**full, "idempotency_tests": {"passed": False, "source": SRC}}).releasable
    missing_one = {k: v for k, v in full.items() if k != "double_booking_tests"}
    assert not evaluate(missing_one).releasable and not evaluate(missing_one).failed


def test_an_evidence_key_that_names_no_gate_blocks_release_and_is_shown():
    full = {g.id: {"passed": True, "value": 1.0, "bar": 0.5, "source": SRC} for g in GATES}
    card = evaluate({**full, "made_up_gate": {"passed": True, "source": SRC}})
    assert card.unknown_evidence == ("made_up_gate",) and not card.releasable and "made_up_gate" in render(card)


def test_no_threshold_number_is_written_in_the_module():
    source = (ROOT / "careflow" / "gates.py").read_text(encoding="utf-8")
    for starting_value in ("0.80", "0.90", "0.95"):  # the spec's starting values are a clinician's to set, so they must not appear as code
        assert starting_value not in source.replace("# CLINICAL-PLACEHOLDER", "")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "gate_scorecard.py"), *args], capture_output=True, text=True, cwd=ROOT, check=False)


def test_the_script_exit_codes(tmp_path):
    assert run().returncode == 2  # nothing measured: not releasable
    assert run("--report-only").returncode == 0  # CI mode: only a FAILED gate fails
    failing = tmp_path / "e.json"
    failing.write_text(json.dumps({"idempotency_tests": {"passed": False, "source": SRC}}), encoding="utf-8")
    assert run(str(failing)).returncode == 1 and run(str(failing), "--report-only").returncode == 1
    out = run("--report-only").stdout
    assert "NOT MEASURED" in out and "releasable: NO" in out
