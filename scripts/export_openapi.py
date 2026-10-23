"""Write, or check, the checked-in OpenAPI snapshot (BUILD-SPEC.md §21).

    python scripts/export_openapi.py            # regenerate openapi.snapshot.json
    python scripts/export_openapi.py --check    # exit 1 if the snapshot no longer matches the live app

The snapshot is the contract the frontend client is tested against, so a route or schema change must show up in
the same diff as the snapshot. `tests/test_openapi_snapshot.py` runs the same comparison in CI. Building the schema
needs no database, no credentials and no network: settings are read only at startup, which this never triggers.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "openapi.snapshot.json"
sys.path.insert(0, str(ROOT))


def render() -> str:
    """The live app's schema as stable text: sorted keys, two-space indent, LF, one trailing newline."""
    from careflow.app import create_app

    return json.dumps(create_app().openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Write or check the OpenAPI snapshot.")
    ap.add_argument("--check", action="store_true", help="fail if the snapshot is missing or stale; write nothing")
    args = ap.parse_args()
    live = render()
    if args.check:
        current = SNAPSHOT.read_bytes().decode("utf-8").replace("\r\n", "\n") if SNAPSHOT.exists() else None
        if current != live:
            sys.exit("openapi.snapshot.json is missing or stale: run `python scripts/export_openapi.py` and commit it")
        print("openapi.snapshot.json matches the live app")
        return
    SNAPSHOT.write_bytes(live.encode("utf-8"))
    print(f"wrote {SNAPSHOT.name}")


if __name__ == "__main__":
    main()
