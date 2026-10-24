"""Migration 0021 read as TEXT: no database. The live checks are in tests/test_rls_checkpoints_live.py."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0021_agents_graph_checkpoints.sql").read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--"))
LOW = CODE.lower()
TABLES = ("agents.graph_checkpoints", "agents.graph_checkpoint_blobs", "agents.graph_checkpoint_writes")


def test_all_three_tables_have_forced_rls_clinic_origin_and_an_idempotency_key_in_the_creating_migration():
    for table in TABLES:
        assert f"alter table {table} force row level security" in LOW and f"alter table {table} enable row level security" in LOW
        block = CODE.split(f"create table {table}")[1].split(");")[0]
        assert "clinic_id" in block and "references iam.clinics (id)" in block
        assert "origin" in block and "check (origin in ('live', 'test', 'eval', 'canary'))" in block and "default" not in block.split("origin")[1].split(",")[0]
        assert "idempotency_key" in block and "clinic_id" in block.split("primary key")[1].split(")")[0]


def test_history_and_blobs_are_append_only_and_writes_cannot_be_deleted():
    for name in ("graph_checkpoints_no_update_delete", "graph_checkpoints_no_truncate", "graph_checkpoint_blobs_no_update_delete", "graph_checkpoint_blobs_no_truncate",
                 "graph_checkpoint_writes_no_delete", "graph_checkpoint_writes_no_truncate", "graph_checkpoint_writes_regular_are_final"):
        assert name in LOW
    assert "before update or delete on agents.graph_checkpoints" in LOW and "before update or delete on agents.graph_checkpoint_blobs" in LOW
    assert "before delete on agents.graph_checkpoint_writes" in LOW


def test_the_app_role_gets_select_and_insert_and_only_four_update_columns_on_writes_and_never_delete():
    grants = [g.strip().lower() for g in CODE.split(";") if g.strip().lower().startswith("grant")]
    assert len(grants) == 2
    assert grants[0].startswith("grant select, insert on agents.graph_checkpoints, agents.graph_checkpoint_blobs, agents.graph_checkpoint_writes to careflow_app")
    assert grants[1] == "grant update (channel, write_type, write_blob, task_path) on agents.graph_checkpoint_writes to careflow_app"
    assert not [g for g in grants if "delete" in g or "truncate" in g]


def test_exactly_seven_policies_all_limited_to_worker_nurse_doctor_and_the_only_update_is_for_the_special_writes():
    policies = re.findall(r"create policy (\w+) on (agents\.\w+) for (\w+) to careflow_app\s+(.*?);", CODE, flags=re.IGNORECASE | re.DOTALL)
    assert len(re.findall(r"create\s+policy", LOW)) == 7 and len(policies) == 7
    assert sorted((t, k) for _, t, k, _ in policies) == [
        ("agents.graph_checkpoint_blobs", "insert"), ("agents.graph_checkpoint_blobs", "select"), ("agents.graph_checkpoint_writes", "insert"),
        ("agents.graph_checkpoint_writes", "select"), ("agents.graph_checkpoint_writes", "update"), ("agents.graph_checkpoints", "insert"),
        ("agents.graph_checkpoints", "select"),
    ]
    for _, _, _, body in policies:
        assert "array['queue_worker', 'nurse', 'doctor']" in body and not re.search(r"front_desk|admin|business_owner|auditor", body)
    update = next(b for n, _, _, b in policies if n == "graph_checkpoint_writes_update")
    assert update.count("idx < 0") == 2  # using and with check


def test_nothing_else_is_created_no_function_no_definer_no_seed_rows_and_checkpoint_ids_sort_with_the_c_collation():
    assert "create function" not in LOW and "security definer" not in LOW and not re.search(r"insert\s+into\s+agents\.graph", LOW)
    assert 'checkpoint_id        text collate "c" not null' in LOW or 'checkpoint_id text collate "c" not null' in re.sub(r"\s+", " ", LOW)


def test_a_regular_write_can_never_be_updated_by_anyone_and_the_header_states_the_owner_caveat():
    assert "before update on agents.graph_checkpoint_writes" in LOW and "when (old.idx >= 0)" in LOW
    assert "disable the triggers" in SQL.lower() and "cross-clinic" in SQL.lower()
