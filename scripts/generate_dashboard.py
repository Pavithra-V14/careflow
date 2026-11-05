"""Generate ops/generated/dashboard.json and ops/generated/alert-rules.yaml from metrics-catalog.yaml (careflow/metrics_catalog.py).
Usage: python scripts/generate_dashboard.py [--check]. With --check nothing is written: exit 1 if a file is stale or the catalog has problems. No service is called."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow import metrics_catalog as mc

OUT = Path(__file__).resolve().parent.parent / "ops" / "generated"


def main(argv: list[str]) -> int:
    data = mc.load()
    issues = mc.problems(data)
    if issues:
        print("\n".join(issues))
        return 1
    files = {OUT / "dashboard.json": mc.render_dashboard(data), OUT / "alert-rules.yaml": mc.render_alert_rules(data)}
    if "--check" in argv:
        stale = [p.name for p, text in files.items() if not p.exists() or p.read_bytes().decode("utf-8").replace("\r\n", "\n") != text]
        if stale:
            print("stale (run `python scripts/generate_dashboard.py`):", ", ".join(stale))
            return 1
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for path, text in files.items():
        path.write_bytes(text.encode("utf-8"))
        print("wrote", path.relative_to(OUT.parent.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
