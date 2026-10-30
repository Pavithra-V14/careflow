"""Root pytest hooks.

SPLITTING THE ROLE MATRIX ACROSS CI SHARDS. tests/test_api_role_matrix.py is the longest live file and grows with every route. CI runs it in two shards, each with
`CAREFLOW_MATRIX_PART=i/n`; this hook then keeps only the tests of that part, chosen by a stable hash of the test id, so the n parts together run every test of the file exactly once and no
test is in two parts. Each part is a separate pytest process, so it seeds its own clinics (the module fixtures do not collide). With the variable unset or empty (every local run, the offline
job) nothing is deselected. An invalid value fails the run loudly instead of silently running everything or nothing.
"""

import os
import zlib

MATRIX_FILE = "test_api_role_matrix.py"


def matrix_part_of(nodeid: str, parts: int) -> int:
    """1-based part number of a test id (stable across processes and runs: crc32, not Python's randomised hash)."""
    return zlib.crc32(nodeid.encode("utf-8")) % parts + 1


def parse_matrix_part(value: str) -> tuple[int, int] | None:
    if not value.strip():
        return None
    try:
        index, total = (int(piece) for piece in value.split("/"))
    except ValueError as exc:
        raise ValueError(f"CAREFLOW_MATRIX_PART must look like '1/2', got {value!r}") from exc
    if not 1 <= index <= total:
        raise ValueError(f"CAREFLOW_MATRIX_PART must have 1 <= i <= n, got {value!r}")
    return index, total


def pytest_collection_modifyitems(config, items):
    part = parse_matrix_part(os.environ.get("CAREFLOW_MATRIX_PART", ""))
    if part is None:
        return
    index, total = part
    keep, dropped = [], []
    for item in items:
        if item.nodeid.split("::")[0].endswith(MATRIX_FILE) and matrix_part_of(item.nodeid, total) != index:
            dropped.append(item)
        else:
            keep.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = keep
