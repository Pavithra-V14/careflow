"""The dependency-audit gate (BUILD-SPEC.md 21; owner decision 2026-11-18). Reads pip-audit's JSON on stdin and the reviewed ignore list `ops/audit-ignore.yaml`; exits 1 on a finding that is not ignored.

    uvx --from pip-audit==2.9.0 pip-audit -r audit-requirements.txt --no-deps --disable-pip -f json | python scripts/audit_gate.py

SEVERITY. pip-audit reports NO severity, and looking one up would be a new external service (CLAUDE.md rule 13), so this gate does not guess one: a vulnerability whose severity is unknown is treated as NOT low
(unknown is not a value). Every finding with a fix version FAILS unless it is on the ignore list, where a person records the advisory id, a reason (for example "moderate, not reachable: we do not call it"), the
date reviewed and an expiry date. An entry past its expiry stops ignoring. A finding with no fix version is reported and does not fail (nothing can be done yet), unless it is a skipped dependency: a package that
could not be audited is not clean, so it fails too. Prints only package names, versions and advisory ids; never a value from the environment.
"""

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
IGNORE_FILE = ROOT / "ops" / "audit-ignore.yaml"


class GateError(Exception):
    """The ignore list or the audit output is not in the shape this gate understands (it then fails closed)."""


def load_ignores(path: Path, today: date) -> dict[str, str]:
    """Advisory id (and every alias a person listed) -> reason, for entries not yet expired. A malformed entry is a GateError, never silently skipped."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    if data is None:
        return {}
    if not isinstance(data, dict) or set(data) != {"ignore"} or not isinstance(data["ignore"], list):
        raise GateError("the ignore list must be a mapping with one key, `ignore`, holding a list")
    active: dict[str, str] = {}
    for entry in data["ignore"]:
        if not isinstance(entry, dict) or set(entry) - {"id", "aliases", "package", "reason", "reviewed", "expires"} or not {"id", "reason", "reviewed", "expires"} <= set(entry):
            raise GateError("every ignore entry needs id, reason, reviewed and expires (and may name package and aliases)")
        reason = entry["reason"]
        if not isinstance(reason, str) or len(reason.strip()) < 10:
            raise GateError("an ignore entry needs a real reason")
        try:
            reviewed, expires = (v if isinstance(v, date) else date.fromisoformat(str(v)) for v in (entry["reviewed"], entry["expires"]))
        except ValueError:
            raise GateError("reviewed and expires must be dates") from None
        if expires <= reviewed or reviewed > today:
            raise GateError("an ignore entry must expire after it was reviewed, and not be reviewed in the future")
        if expires > today:
            for ident in (entry["id"], *entry.get("aliases", [])):
                active[str(ident)] = reason
    return active


def evaluate(report: dict[str, Any], ignores: dict[str, str]) -> tuple[list[str], list[str]]:
    """(failures, notes), as printable lines of names, versions and advisory ids."""
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list) or not report["dependencies"]:
        raise GateError("the audit output has no dependencies: nothing was audited, which is not a pass")
    failures: list[str] = []
    notes: list[str] = []
    for dep in report["dependencies"]:
        name, version = dep.get("name", "?"), dep.get("version", "?")
        if "skip_reason" in dep:
            failures.append(f"{name} {version}: not audited ({dep['skip_reason']})")
            continue
        for vuln in dep.get("vulns", []):
            ids = [vuln.get("id", "?"), *vuln.get("aliases", [])]
            ignored = next((i for i in ids if i in ignores), None)
            fixes = vuln.get("fix_versions") or []
            if ignored:
                notes.append(f"{name} {version}: {ids[0]} ignored by the reviewed list")
            elif fixes:
                failures.append(f"{name} {version}: {ids[0]} (fixed in {', '.join(map(str, fixes))}; severity unknown, treated as not low)")
            else:
                notes.append(f"{name} {version}: {ids[0]} has no fix version yet (reported, not failing)")
    return failures, notes


def main() -> int:
    raw = sys.stdin.read()
    try:
        report = json.loads(raw[raw.index("{") :])
        failures, notes = evaluate(report, load_ignores(IGNORE_FILE, datetime.now(UTC).date()))
    except (ValueError, GateError) as exc:
        print(f"audit gate: cannot decide ({exc}); failing closed")
        return 1
    for line in notes:
        print("note:", line)
    for line in failures:
        print("FAIL:", line)
    print(f"audit gate: {len(report['dependencies'])} packages, {len(failures)} failing, {len(notes)} noted")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
