"""Provider-outage and database-outage drill. FAKES ONLY: it calls no provider, opens no connection, reads no key and spends nothing. A person runs it by hand (or CI runs it through its test); nothing else does.

    uv run python scripts/drill_outages.py

It runs only in the synthetic demo (CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only) and refuses anywhere else. It prints one line per check, `PASS <id>: <what was proved>` or `FAIL <id>: <what failed>`, in fixed words (a check
that raises is a FAIL naming the exception CLASS only), then `drill result: PASS` or `drill result: FAIL`. The exit code is 0 only when every check passed.

What it proves (careflow/outage_drill.py has the checks; tests/test_outage_drills.py runs the same ones and a negative control for each kind): a failure or timeout of the vision, decision, translation, embedding or database edge ends in a
human-review outcome or a fixed-words refusal, never a guess; the rules run before any model and a model outage never blocks a red-flag nurse task; no side effect comes from a stage that failed or paused; a retry with the same idempotency key
repeats no effect; spend is recorded once per answered call and the ceiling stops further calls; a restart mid-run resumes from its checkpoint without repeating a step.
"""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def refuse_unless_synthetic_demo(environ: "os._Environ[str] | dict[str, str]") -> str | None:
    """The fixed-words refusal, or None when the environment is the synthetic demo. Read from the process environment only; no file, no key."""
    if (environ.get("CAREFLOW_PROFILE") or "").strip().lower() != "demo":
        return "refusing: the drill runs in the demo profile only (CAREFLOW_PROFILE=demo)"
    if (environ.get("CAREFLOW_DATA_POLICY") or "").strip().lower() != "synthetic_only":
        return "refusing: the drill runs with CAREFLOW_DATA_POLICY=synthetic_only only"
    return None


async def main() -> int:
    why = refuse_unless_synthetic_demo(os.environ)
    if why is not None:
        print(why)
        return 1
    from careflow import outage_drill

    print("outage drill: fakes only, no network, no database, no key read, spent nothing")
    results = await outage_drill.run_all()
    for check in results:
        print(check.line())
    ok = all(c.passed for c in results)
    print(f"drill result: {'PASS' if ok else 'FAIL'} ({sum(c.passed for c in results)} of {len(results)} checks held)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
