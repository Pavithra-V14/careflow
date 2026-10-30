"""Migration 0020 read as TEXT: no database. The live checks are in tests/test_rls_referral_hold_live.py."""

import re
from pathlib import Path

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0020_referral_hold_review_tasks.sql").read_text(encoding="utf-8")
CODE = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--"))


def test_exactly_the_select_and_update_policies_are_replaced_and_nothing_else_changes():
    low = CODE.lower()
    assert low.count("drop policy") == 2 and "drop policy review_tasks_select" in low and "drop policy review_tasks_update" in low
    assert len(re.findall(r"create\s+policy", low)) == 2
    assert "review_tasks_insert" not in low and "grant " not in low and "revoke " not in low and "alter table" not in low
    assert "create function" not in low and "security definer" not in low


def test_the_old_rules_are_kept_and_front_desk_gains_only_its_own_referral_hold_tasks():
    select = CODE.split("create policy review_tasks_select")[1].split(";")[0]
    assert "array['nurse', 'doctor', 'queue_worker']" in select
    assert "array['front_desk', 'admin']) and kind = 'unmerge_review'" in select
    assert "array['front_desk']) and kind = 'referral_hold' and assigned_role = 'front_desk'" in select
    update = CODE.split("create policy review_tasks_update")[1]
    assert update.count("kind = 'referral_hold' and assigned_role = 'front_desk'") == 2  # using and with check
    assert "acknowledged_by is null or acknowledged_by = auth.uid()" in update
    for body in (select, update):
        assert "admin']) and kind = 'referral_hold'" not in body and "'auditor'" not in body and "business_owner" not in body
