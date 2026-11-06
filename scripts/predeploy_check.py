"""Print the pre-deployment go/no-go (careflow/predeploy.py). Usage: python scripts/predeploy_check.py [evidence.yaml] [--report-only]. Exit 0 only on GO; 1 on NO-GO; with --report-only
always 0 (CI uses this until first real traffic is near: the answer today is NO-GO, and that is the truth, not a failure)."""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow import predeploy


def main(argv: list[str]) -> int:
    paths = [a for a in argv if not a.startswith("--")]
    path = Path(paths[0]) if paths else predeploy.EVIDENCE
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    evidence = loaded if isinstance(loaded, dict) else {}
    rubric = predeploy.load_rubric()
    lines = predeploy.evaluate_checks(evidence, rubric)
    print(predeploy.render(lines, predeploy.score(rubric, evidence)))
    return 0 if predeploy.go(lines) or "--report-only" in argv else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
