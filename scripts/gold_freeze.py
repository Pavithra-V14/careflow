"""Freeze (or check) the held-out gold split (careflow/goldset.py). Usage: python scripts/gold_freeze.py [--freeze-new]. Always checks: exits 1 if a case is malformed, a frozen held-out case
changed or vanished, or a held-out case is not locked. --freeze-new first adds ONLY newly present held-out cases to eval/gold/held-out.lock; it never rewrites an existing entry (a changed case
is a problem to look at, not to overwrite)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow import goldset


def main(argv: list[str]) -> int:
    loaded = goldset.load()
    for problem in loaded.problems:
        print(problem)
    lock = goldset.read_lock()
    if "--freeze-new" in argv:
        current = goldset.freeze_manifest(loaded.cases)
        merged = {**{k: v for k, v in current.items() if k not in lock}, **lock}
        (goldset.GOLD_DIR / goldset.LOCK_FILE).write_text(json.dumps(dict(sorted(merged.items())), indent=2), encoding="utf-8")
        print(f"locked {len(merged) - len(lock)} new held-out case(s)")
        lock = merged
    problems = goldset.held_out_problems(loaded.cases, lock)
    for problem in problems:
        print(problem)
    print(json.dumps(goldset.report(loaded.cases)))
    return 1 if problems or loaded.problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
