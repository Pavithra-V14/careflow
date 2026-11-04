"""Gate evidence from JUnit XML (careflow/gate_evidence.py, scripts/gate_evidence.py) feeding the scorecard. Offline, on small invented XML files."""

import json
import subprocess
import sys
from pathlib import Path

import yaml

from careflow.gate_evidence import build_evidence
from careflow.gates import evaluate

ROOT = Path(__file__).resolve().parent.parent
MAPPING = {"idempotency_tests": ["tests/test_a_live.py", "tests/test_b_live.py"], "double_booking_tests": ["tests/test_c_live.py"]}


def junit(tmp_path: Path, name: str, cases: list[tuple[str, str, str]]) -> Path:
    body = "".join(
        f'<testcase classname="tests.{file}{"" if i % 2 else ".TestX"}" name="t{i}">' + {"passed": "", "failed": "<failure/>", "error": "<error/>", "skipped": "<skipped/>"}[outcome] + "</testcase>"
        for i, (file, _unused, outcome) in enumerate(cases)
    )
    path = tmp_path / name
    path.write_text(f"<testsuites><testsuite>{body}</testsuite></testsuites>", encoding="utf-8")
    return path


def test_all_passing_tests_from_the_mapped_files_make_the_gate_pass(tmp_path):
    xml = junit(tmp_path, "s1.xml", [("test_a_live", "", "passed"), ("test_b_live", "", "passed"), ("test_other", "", "failed")])
    ev = build_evidence(MAPPING, [xml], source="ci run 1")
    assert ev["idempotency_tests"]["passed"] is True and "2 tests" in ev["idempotency_tests"]["source"]
    assert "double_booking_tests" not in ev  # nothing from its file ran: NOT MEASURED, not passed


def test_one_failure_error_or_skip_in_the_mapped_files_means_the_gate_did_not_pass(tmp_path):
    for bad in ("failed", "error", "skipped"):
        xml = junit(tmp_path, f"{bad}.xml", [("test_a_live", "", "passed"), ("test_b_live", "", bad)])
        assert build_evidence(MAPPING, [xml], source="s")["idempotency_tests"]["passed"] is False, bad


def test_results_from_several_shards_are_combined(tmp_path):
    one = junit(tmp_path, "a.xml", [("test_a_live", "", "passed")])
    two = junit(tmp_path, "b.xml", [("test_b_live", "", "failed")])
    assert build_evidence(MAPPING, [one, two], source="s")["idempotency_tests"]["passed"] is False


def test_the_scorecard_reads_the_evidence_and_everything_else_stays_not_measured(tmp_path):
    xml = junit(tmp_path, "x.xml", [("test_a_live", "", "passed")])
    card = evaluate(build_evidence(MAPPING, [xml], source="ci run 1"))
    status = {r.gate.id: r.status for r in card.results}
    assert status["idempotency_tests"] == "MEASURED" and status["red_flag_recall"] == "NOT MEASURED" and not card.releasable


def test_the_real_mapping_names_only_gates_that_exist_and_files_that_exist():
    from careflow.gates import GATE_IDS

    mapping = yaml.safe_load((ROOT / "ops" / "gate-test-map.yaml").read_text(encoding="utf-8"))
    assert set(mapping) <= GATE_IDS and set(mapping) == {"clinic_isolation_tests", "double_booking_tests", "idempotency_tests"}
    for files in mapping.values():
        assert all((ROOT / f).is_file() for f in files), files


def test_the_script_writes_the_file(tmp_path):
    xml = junit(tmp_path, "s.xml", [("test_scheduling_live", "", "passed")])
    out = tmp_path / "evidence.json"
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "gate_evidence.py"), str(out), str(xml)], capture_output=True, text=True, cwd=ROOT, check=False)
    assert done.returncode == 0, done.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["double_booking_tests"]["passed"] is True


def test_the_workflow_keeps_each_shards_junit_and_feeds_the_report_only_scorecard_job():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    live_steps = yaml.safe_dump(workflow["jobs"]["live"]["steps"])
    assert "actions/upload-artifact@v4" in live_steps and "junit-shard-" in live_steps
    job = workflow["jobs"]["gate-scorecard"]
    assert job["continue-on-error"] is True and "live" in job["needs"] and "--report-only" in yaml.safe_dump(job)
    assert "gate-scorecard" not in workflow["jobs"]["test"]["needs"]  # report only: it cannot fail the required check
