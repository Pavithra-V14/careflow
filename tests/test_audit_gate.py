"""The dependency-audit gate (scripts/audit_gate.py): any finding with a fix fails unless a person listed it, an entry expires, nothing audited is not a pass, and the repository's own list is valid."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("audit_gate", ROOT / "scripts" / "audit_gate.py")
gate = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["audit_gate"] = gate
spec.loader.exec_module(gate)  # type: ignore[union-attr]

TODAY = date(2026, 11, 19)


def report(*deps):
    return {"dependencies": list(deps)}


CLEAN = {"name": "a", "version": "1", "vulns": []}
FIXABLE = {"name": "b", "version": "2", "vulns": [{"id": "GHSA-1", "aliases": ["CVE-1"], "fix_versions": ["2.1"], "description": "x"}]}
NOFIX = {"name": "c", "version": "3", "vulns": [{"id": "GHSA-2", "aliases": [], "fix_versions": []}]}


def test_clean_passes_and_a_fixable_finding_fails_whatever_its_severity_is_unknown():
    assert gate.evaluate(report(CLEAN), {}) == ([], [])
    failures, _ = gate.evaluate(report(CLEAN, FIXABLE), {})
    assert len(failures) == 1 and "GHSA-1" in failures[0] and "severity unknown" in failures[0]


def test_a_finding_with_no_fix_is_reported_not_failed_and_a_skipped_package_fails():
    assert gate.evaluate(report(NOFIX), {}) == ([], ["c 3: GHSA-2 has no fix version yet (reported, not failing)"])
    failures, _ = gate.evaluate(report({"name": "d", "version": "4", "skip_reason": "not on PyPI"}), {})
    assert failures and "not audited" in failures[0]


def test_nothing_audited_is_not_a_pass():
    for bad in ({}, {"dependencies": []}, [], {"dependencies": "x"}):
        with pytest.raises(gate.GateError):
            gate.evaluate(bad, {})


def test_an_ignore_entry_works_by_id_or_alias_until_it_expires(tmp_path):
    path = tmp_path / "i.yaml"
    path.write_text("ignore:\n  - id: GHSA-9\n    aliases: [CVE-1]\n    reason: moderate, not reachable here\n    reviewed: 2026-11-01\n    expires: 2027-01-01\n", encoding="utf-8")
    active = gate.load_ignores(path, TODAY)
    assert gate.evaluate(report(FIXABLE), active)[0] == []  # matched through the alias
    assert gate.load_ignores(path, date(2027, 1, 1)) == {}  # expired: it comes back for review
    assert gate.evaluate(report(FIXABLE), gate.load_ignores(path, date(2027, 1, 1)))[0]


@pytest.mark.parametrize(
    "body",
    [
        "ignore: [1]\n",
        "other: []\n",
        "ignore:\n  - id: X\n    reviewed: 2026-11-01\n    expires: 2027-01-01\n",  # no reason
        "ignore:\n  - id: X\n    reason: short\n    reviewed: 2026-11-01\n    expires: 2027-01-01\n",
        "ignore:\n  - id: X\n    reason: a long enough reason\n    reviewed: 2026-11-01\n    expires: 2026-10-01\n",  # expires before reviewed
        "ignore:\n  - id: X\n    reason: a long enough reason\n    reviewed: 2027-03-01\n    expires: 2027-06-01\n",  # reviewed in the future
        "ignore:\n  - id: X\n    reason: a long enough reason\n    reviewed: not-a-date\n    expires: 2027-06-01\n",
        "ignore:\n  - id: X\n    reason: a long enough reason\n    reviewed: 2026-11-01\n    expires: 2027-01-01\n    severity: low\n",  # an unknown field
    ],
)
def test_a_malformed_ignore_list_is_an_error_never_silently_skipped(tmp_path, body):
    path = tmp_path / "i.yaml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(gate.GateError):
        gate.load_ignores(path, TODAY)


def test_the_repositorys_own_list_is_valid_and_starts_empty():
    assert gate.load_ignores(ROOT / "ops" / "audit-ignore.yaml", TODAY) == {}


def test_the_ci_job_runs_the_gate_and_is_no_longer_report_only():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    job = ci[ci.index("  dependency-audit:") : ci.index("  # The gate scorecard fed by")]
    assert "scripts/audit_gate.py" in job and "-f json" in job
    assert "continue-on-error: true" not in job and "report only" not in job.lower().replace("report only for", "")
