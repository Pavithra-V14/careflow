"""The dependency audit job is in CI, pinned, and REPORT-ONLY for now (owner decision 2026-11-04): it must not gate the required `test` check yet. Offline: reads the workflow."""

from pathlib import Path

import yaml

WORKFLOW = yaml.safe_load((Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def test_the_audit_job_exists_pinned_and_report_only():
    job = WORKFLOW["jobs"]["dependency-audit"]
    text = yaml.safe_dump(job)
    assert job["continue-on-error"] is True and "pip-audit==" in text and "--frozen" in text
    assert "secrets." not in text  # it needs no secret: only package names and versions leave the runner


def test_the_required_check_does_not_depend_on_it_yet():
    assert "dependency-audit" not in WORKFLOW["jobs"]["test"]["needs"]
