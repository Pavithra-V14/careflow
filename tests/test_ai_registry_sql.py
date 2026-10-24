"""Migration 0015 (ai.model_registry), read as TEXT: no database. The live checks are in tests/test_rls_ai_registry_live.py."""

import re
from pathlib import Path

from careflow.ports import ROLES

SQL = (Path(__file__).resolve().parent.parent / "db" / "migrations" / "0015_ai_model_registry.sql").read_text(encoding="utf-8")


def test_the_sql_role_list_equals_the_python_role_list():
    listed = re.search(r"check \(role in \(([^)]*)\)\)", SQL)
    assert listed
    assert sorted(re.findall(r"'([a-z_]+)'", listed.group(1))) == sorted(ROLES)


def test_the_registry_has_forced_rls_grants_select_only_and_seeds_nothing():
    assert "force row level security" in SQL.lower()
    assert re.findall(r"grant\s+(\w+)\s+on\s+ai\.model_registry", SQL, flags=re.IGNORECASE) == ["select"]
    assert not re.search(r"insert\s+into\s+ai\.model_registry", SQL, flags=re.IGNORECASE), "the registry must start empty (CLAUDE.md rule 13)"
    assert not re.search(r"\b(begin|commit|rollback)\s*;", SQL, flags=re.IGNORECASE), "a migration must not manage its own transaction"


def test_no_policy_lets_the_application_write():
    policies = re.findall(r"create policy \w+ on ai\.model_registry for (\w+)", SQL, flags=re.IGNORECASE)
    assert policies == ["select"]
