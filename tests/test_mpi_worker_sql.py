"""Migration 0019 read as TEXT: no database. The live checks are in tests/test_rls_mpi_worker_live.py."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0019_mpi_queue_worker_ingestion.sql").read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--"))
POLICIES = re.findall(r"create policy (\w+) on (mpi\.\w+) for (\w+) to careflow_app\s+(using|with check) \((.*?)\);", CODE, flags=re.IGNORECASE | re.DOTALL)


def test_exactly_the_seven_expected_policies_exist_and_all_are_queue_worker_only():
    got = sorted((table, kind) for _, table, kind, _, _ in POLICIES)
    assert got == [
        ("mpi.identity_tokens", "insert"), ("mpi.identity_tokens", "select"), ("mpi.match_candidates", "insert"), ("mpi.match_candidates", "select"),
        ("mpi.merges", "select"), ("mpi.patients", "insert"), ("mpi.patients", "select"),
    ]
    for _, _, _, _, body in POLICIES:
        assert "iam.has_clinic_role(clinic_id, array['queue_worker'])" in body
        assert not re.search(r"front_desk|nurse|doctor|admin|business_owner|auditor", body)


def test_there_is_no_update_or_delete_policy_no_policy_on_merges_for_writing_and_no_grant_no_alter_no_drop():
    assert not [p for p in POLICIES if p[2].lower() in ("update", "delete", "all")]
    assert not [p for p in POLICIES if p[1] == "mpi.merges" and p[2].lower() != "select"]
    low = CODE.lower()
    assert "grant " not in low and "revoke " not in low and "alter table" not in low and "drop " not in low and "disable row level security" not in low
    assert not re.search(r"insert\s+into|update\s+mpi|delete\s+from", low)
