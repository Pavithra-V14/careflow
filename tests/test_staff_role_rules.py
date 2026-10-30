"""Worker accounts hold queue_worker only: migration 0024 as text, the read-only stop-gap query, and the (never-run-by-tests) dev seeding script's rules. No database, no network."""

import importlib.util
import re
from pathlib import Path

import pytest

from careflow.ops_checks import (
    WORKER_EXCLUSIVITY_QUERY,
    accounts_holding_worker_and_another_role,
)

ROOT = Path(__file__).resolve().parent.parent
SQL = (ROOT / "db" / "migrations" / "0024_worker_role_exclusive.sql").read_text(encoding="utf-8")
LOW = "\n".join(line for line in SQL.splitlines() if not line.strip().startswith("--")).lower()

_spec = importlib.util.spec_from_file_location("seed_dev_staff", ROOT / "scripts" / "seed_dev_staff.py")
seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed)


def test_the_trigger_refuses_both_directions_only_for_active_grants_and_is_a_pinned_reader():
    assert "before insert or update of account_id, role, revoked_at on iam.staff_roles" in LOW
    assert "security definer set search_path = ''" in LOW
    assert "if new.revoked_at is not null then" in LOW  # a revoked grant never conflicts
    assert "r.revoked_at is null and r.role <> 'queue_worker'" in LOW and "r.revoked_at is null and r.role = 'queue_worker'" in LOW
    assert LOW.count("errcode = '23514'") == 2
    assert not re.search(r"\b(insert into|delete from|update iam|grant )", LOW.replace("before insert or update", ""))  # reads and refuses; writes and grants nothing
    assert "revoke all on function iam.keep_queue_worker_exclusive() from public, careflow_app" in LOW


def test_the_trigger_takes_a_per_account_lock_fails_closed_without_rls_bypass_and_is_enabled_always():
    assert "perform pg_advisory_xact_lock(hashtextextended('iam.staff_roles.worker_exclusive:' || new.account_id::text, 0))" in LOW
    assert LOW.index("if new.revoked_at is not null") < LOW.index("pg_advisory_xact_lock")  # revoking takes no lock
    assert "rolsuper or rolbypassrls from pg_roles where rolname = current_user" in LOW and LOW.index("rolbypassrls") < LOW.index("create function iam.keep_queue_worker_exclusive")
    assert "alter table iam.staff_roles enable always trigger staff_roles_queue_worker_exclusive" in LOW
    assert LOW.count("constraint = 'staff_roles_queue_worker_exclusive'") == 2


def test_the_stop_gap_origin_filter_selects_accounts_not_grants():
    q = WORKER_EXCLUSIVITY_QUERY.lower()
    assert "account_id in (select id from iam.accounts where origin = $1)" in q
    assert "and origin = $1" not in q  # a live worker account with a test-origin front desk grant must still be found


def test_the_stop_gap_query_is_read_only_and_groups_by_account():
    q = WORKER_EXCLUSIVITY_QUERY.lower()
    assert q.startswith("select") and not re.search(r"\b(insert|update|delete|alter|drop)\b", q)
    assert "revoked_at is null" in q and "bool_or(role = 'queue_worker')" in q and "count(distinct role) > 1" in q


@pytest.mark.asyncio
async def test_the_check_reports_sorted_opaque_ids_and_passes_the_origin_filter_through():
    seen = []

    async def fetch(sql, *args):
        seen.append(args)
        return [{"account_id": "b"}, {"account_id": "a"}]

    assert await accounts_holding_worker_and_another_role(fetch, "live") == ["a", "b"]
    assert await accounts_holding_worker_and_another_role(fetch) == ["a", "b"] and seen == [("live",), (None,)]


def test_the_seed_script_refuses_anything_but_the_dev_project_in_the_demo_profile():
    seed.refuse_unless_dev_demo({"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "demo"})
    for env in ({}, {"CAREFLOW_ENV": "dev"}, {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "clinical"}, {"CAREFLOW_ENV": "ci", "CAREFLOW_PROFILE": "demo"},
                {"CAREFLOW_ENV": "prod", "CAREFLOW_PROFILE": "demo"}, {"CAREFLOW_ENV": "dev", "CAREFLOW_PROFILE": "Demo"}):
        with pytest.raises(seed.Refused):
            seed.refuse_unless_dev_demo(env)


def test_a_queue_worker_never_gets_another_role_and_nobody_gets_queue_worker_alongside_one():
    assert seed.roles_after(set(), "queue_worker") == {"queue_worker"}
    assert seed.roles_after({"queue_worker"}, "queue_worker") == {"queue_worker"}  # several clinics are fine
    assert seed.roles_after({"nurse"}, "doctor") == {"nurse", "doctor"}
    for existing, new in (({"queue_worker"}, "front_desk"), ({"nurse"}, "queue_worker"), ({"front_desk", "nurse"}, "queue_worker")):
        with pytest.raises(seed.Refused):
            seed.roles_after(existing, new)
    with pytest.raises(seed.Refused):
        seed.roles_after(set(), "superuser")


def test_the_password_rules_and_it_is_never_an_argument_printed_or_stored():
    seed.check_password("a-long-enough-pass", "a-long-enough-pass")
    for bad in (("short", "short"), ("a-long-enough-pass", "a-long-enough-pasS")):
        with pytest.raises(seed.Refused):
            seed.check_password(*bad)
    source = (ROOT / "scripts" / "seed_dev_staff.py").read_text(encoding="utf-8")
    assert "getpass.getpass" in source and '"--password"' not in source and "os.environ" not in source and "getenv" not in source
    printed = re.findall(r"print\((.*)\)", source)
    assert printed and not any("password" in p.lower() or "secret" in p.lower() for p in printed), printed
    assert "open(" not in source and "write_text" not in source  # nothing is written to a file
