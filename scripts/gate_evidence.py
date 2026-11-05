"""Build the gate evidence file from CI JUnit XML. Usage: python scripts/gate_evidence.py OUT.json JUNIT.xml [JUNIT.xml ...]  (source label from $GITHUB_RUN_ID). Feed OUT.json to scripts/gate_scorecard.py."""

import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from careflow.gate_evidence import build_evidence


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: gate_evidence.py OUT.json JUNIT.xml [...]")
        return 2
    out, junit = Path(argv[0]), [Path(p) for p in argv[1:]]
    mapping = yaml.safe_load((ROOT / "ops" / "gate-test-map.yaml").read_text(encoding="utf-8"))
    run = os.environ.get("GITHUB_RUN_ID", "local")
    evidence = build_evidence(mapping, junit, source=f"ci run {run}")
    out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"wrote {out} ({len(evidence)} gate(s) with evidence)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
