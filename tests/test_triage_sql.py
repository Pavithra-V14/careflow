"""Migration 0018 read as TEXT: no database. The live checks are in tests/test_rls_triage_live.py."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0018_agents_triage_proposals_and_decisions.sql").read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--"))
TABLES = ("agents.triage_proposals", "agents.review_decisions")


def test_both_tables_have_forced_rls_from_this_migration_and_append_only_triggers():
    low = CODE.lower()
    for table in TABLES:
        assert f"alter table {table} force row level security" in low
    for name in ("triage_proposals_no_update_delete", "triage_proposals_no_truncate", "review_decisions_no_update_delete", "review_decisions_no_truncate"):
        assert name in low


def test_the_app_role_has_select_and_column_limited_insert_only_and_never_update_or_delete():
    grants = [g.strip().lower() for g in CODE.split(";") if g.strip().lower().startswith("grant") and "agents." in g.lower()]
    for g in grants:
        assert re.match(r"grant (select|insert \()", g), g
        assert "update" not in g and "delete" not in g and "truncate" not in g, g
    inserts = " ".join(g for g in grants if g.startswith("grant insert"))
    assert "created_at" not in inserts


def test_a_decision_always_names_a_human_and_has_no_automatic_source():
    block = CODE.split("create table agents.review_decisions")[1].split(");")[0]
    assert "decided_by      uuid not null" in block
    assert "decision_source" not in CODE and "auto" not in block.lower()
    assert "decision in ('approve', 'edit', 'reject')" in block
    assert "review_decisions_one_per_proposal unique (proposal_id)" in block


def test_a_proposal_has_no_priority_or_content_column_and_pins_clinic_and_origin():
    block = CODE.split("create table agents.triage_proposals")[1].split(");")[0]
    columns = re.findall(r"^    (\w+)\s+(?:uuid|text|jsonb|boolean|timestamptz)\b", block, flags=re.MULTILINE)
    assert "candidates" in columns and len(columns) >= 10
    assert not [c for c in columns if re.search(r"priority|title|summary|note|content|body", c)]
    assert "references docs.documents (id, clinic_id)" in block
    assert "references mpi.patients (id, clinic_id, origin)" in block
    assert "references agents.review_tasks (id, clinic_id, origin)" in block
    assert "origin          text not null check (origin in ('live', 'test', 'eval', 'canary'))" in block


def test_exactly_the_expected_policies_exist_and_none_admits_front_desk_admin_owner_or_auditor():
    policies = re.findall(r"create policy (\w+) on (agents\.\w+) for (\w+)(.*?);", CODE, flags=re.IGNORECASE | re.DOTALL)
    assert sorted((t, kind) for _, t, kind, _ in policies) == [
        ("agents.review_decisions", "insert"), ("agents.review_decisions", "select"), ("agents.triage_proposals", "insert"), ("agents.triage_proposals", "select"),
    ]
    for _, _, _, body in policies:
        assert not re.search(r"front_desk|admin|business_owner|auditor", body)
    decision_insert = next(b for n, _, _, b in policies if n == "review_decisions_insert")
    assert "decided_by = auth.uid()" in decision_insert and "'nurse', 'doctor'" in decision_insert and "queue_worker" not in decision_insert


def test_no_seed_rows_and_the_review_task_pin_is_added():
    assert not re.search(r"insert\s+into\s+agents\.(triage_proposals|review_decisions)", CODE, flags=re.IGNORECASE)
    assert "alter table agents.review_tasks add constraint review_tasks_id_clinic_key unique (id, clinic_id)" in CODE
