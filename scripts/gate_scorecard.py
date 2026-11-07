"""Print the gate scorecard (careflow/gates.py). Usage: python scripts/gate_scorecard.py [evidence.json] [--report-only]

Exit codes: 0 releasable; 1 a gate FAILED; 2 nothing failed but a gate is NOT MEASURED (or evidence names no gate). With --report-only only a FAILED gate exits non-zero, which is what CI uses
until the gold sets exist (every gate reads NOT MEASURED today, and that is the truth, not an error). The evidence file is JSON: {"gate_id": {"passed": true, "source": "..."}, ...}."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow.gates import evaluate, render


def main(argv: list[str]) -> int:
    report_only = "--report-only" in argv
    paths = [a for a in argv if not a.startswith("--")]
    evidence = json.loads(Path(paths[0]).read_text(encoding="utf-8")) if paths else {}
    if not isinstance(evidence, dict):
        print("the evidence file must be a JSON object")
        return 2
    card = evaluate(evidence)
    print(render(card))
    if card.failed:
        return 1
    return 0 if (card.releasable or report_only) else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
