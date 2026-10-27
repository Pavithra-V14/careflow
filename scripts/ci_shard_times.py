"""Print the seconds each test FILE took in a CI shard, from pytest's JUnit XML (stdlib only; nothing is sent anywhere).

    uv run python scripts/ci_shard_times.py shard-times.xml [--label shard-2]

One line per file, slowest first, then a total, so the next rebalance of the shards (docs/design/ci-sharding.md) starts from measured per-file times instead of estimates:

    SHARD-TIMES label=shard-2 files=17 tests=259 total_seconds=2050.3
    SHARD-TIME  612.4s  89 tests  tests/test_rls_screenings_live.py

Never fails a shard: an unreadable file prints one line saying so and exits 0.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict


def per_file(path: str) -> dict[str, tuple[float, int]]:
    totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0])
    for case in ET.parse(path).getroot().iter("testcase"):
        name = (case.get("classname") or "").split(".")
        # classname is dotted module path plus an optional class: "tests.test_x_live" or "tests.test_x_live.TestY"
        module = next((".".join(name[: i + 1]) for i in range(len(name)) if name[i].startswith("test_")), ".".join(name))
        file = module.replace(".", "/") + ".py"
        totals[file][0] += float(case.get("time") or 0.0)
        totals[file][1] += 1
    return {f: (round(t, 1), int(n)) for f, (t, n) in totals.items()}


def render(files: dict[str, tuple[float, int]], label: str) -> list[str]:
    ordered = sorted(files.items(), key=lambda kv: -kv[1][0])
    head = f"SHARD-TIMES label={label} files={len(files)} tests={sum(n for _, n in files.values())} total_seconds={round(sum(t for t, _ in files.values()), 1)}"
    return [head, *(f"SHARD-TIME {t:8.1f}s {n:4d} tests  {name}" for name, (t, n) in ordered)]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xml")
    ap.add_argument("--label", default="shard")
    args = ap.parse_args(argv)
    try:
        lines = render(per_file(args.xml), args.label)
    except (OSError, ET.ParseError) as exc:
        print(f"SHARD-TIMES label={args.label} unreadable ({type(exc).__name__})")
        return 0
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
