"""The dependency audit job is in CI, pinned, and a GATE since 2026-11-18 (it fails on a finding with a fix unless the reviewed list names it; scripts/audit_gate.py). Offline: reads the workflow."""

from pathlib import Path

import yaml

WORKFLOW = yaml.safe_load((Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def test_the_audit_job_exists_pinned_and_is_a_gate():
    job = WORKFLOW["jobs"]["dependency-audit"]
    text = yaml.safe_dump(job)
    assert "continue-on-error" not in job and "pip-audit==" in text and "--frozen" in text
    assert "secrets." not in text  # it needs no secret: only package names and versions leave the runner


def test_the_required_check_does_not_depend_on_it_yet():
    assert "dependency-audit" not in WORKFLOW["jobs"]["test"]["needs"]
