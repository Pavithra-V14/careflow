"""Gate evidence from JUnit XML (docs/design/remaining-work.md A11; careflow/gates.py). Pure apart from reading the files it is given. For each mapped gate: PASSED only if at least one test from its files
ran and none failed, errored or was skipped; any failure makes it FAILED; no matching test at all means NO evidence (the gate stays NOT MEASURED). Nothing is invented: counts come from the XML."""

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


def _cases(paths: Iterable[Path]) -> list[tuple[str, str]]:
    """(file, outcome) for every test case: outcome is passed, failed, skipped."""
    found: list[tuple[str, str]] = []
    for path in paths:
        for case in ET.parse(path).getroot().iter("testcase"):
            parts = (case.get("classname") or "").split(".")
            file = "tests/" + next((p for p in reversed(parts) if p.startswith("test_")), "") + ".py"  # tests.test_x or tests.test_x.TestClass -> tests/test_x.py
            outcome = "failed" if case.find("failure") is not None or case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
            found.append((file, outcome))
    return found


def build_evidence(gate_files: Mapping[str, list[str]], junit_paths: Iterable[Path], *, source: str) -> dict[str, Any]:
    cases = _cases(junit_paths)
    evidence: dict[str, Any] = {}
    for gate, files in gate_files.items():
        mine = [outcome for file, outcome in cases if file in set(files)]
        if not mine:
            continue  # nothing from these files ran: no evidence
        bad = [o for o in mine if o != "passed"]
        evidence[gate] = {"passed": not bad, "source": f"{source}: {len(mine)} tests from {len(set(files))} file(s), {len(bad)} not passed"[:200]}
    return evidence
