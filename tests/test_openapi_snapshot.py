"""The checked-in OpenAPI snapshot must match the live app (BUILD-SPEC.md §21). Pure: no database, no network.

A route or schema change that is not accompanied by a regenerated `openapi.snapshot.json` fails here, in CI.
Regenerate with `python scripts/export_openapi.py`.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
export_openapi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(export_openapi)


def test_snapshot_matches_the_live_app():
    path = ROOT / "openapi.snapshot.json"
    assert path.exists(), "openapi.snapshot.json is missing: run `python scripts/export_openapi.py`"
    committed = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    assert committed == export_openapi.render(), "openapi.snapshot.json is stale: run `python scripts/export_openapi.py` and commit it"


def test_the_snapshot_lists_every_served_route():
    import json

    served = set(json.loads((ROOT / "openapi.snapshot.json").read_text(encoding="utf-8"))["paths"])
    assert {"/v1/iam/me", "/v1/staff/scheduling/slots"} <= served
