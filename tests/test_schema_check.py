"""The startup schema check (careflow/schema_check.py). Offline."""

import re
from pathlib import Path

import pytest

from careflow import schema_check

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.asyncio


class _Result:
    def __init__(self, v):
        self.v = v

    def scalar_one(self):
        return self.v


class _Conn:
    def __init__(self, present):
        self.present = present

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, statement, params):
        return _Result(params["n"] in self.present)


class _Engine:
    def __init__(self, present):
        self.present = present

    def connect(self):
        return _Conn(self.present)


def test_every_required_object_is_created_by_the_migration_it_names():
    for prefix, name, kind in schema_check.REQUIRED:
        file = next(ROOT.joinpath("db", "migrations").glob(f"{prefix}_*.sql"))
        bare = name.split("(")[0]
        schema, obj = bare.split(".")
        sql = file.read_text(encoding="utf-8")
        what = "function" if kind == "proc" else "(table|unique index|index)"
        assert re.search(rf"create (or replace )?{what} ({schema}\.)?{obj}\b", sql), (prefix, name)


async def test_a_database_with_everything_starts():
    await schema_check.check(_Engine({n for _, n, _ in schema_check.REQUIRED}))


async def test_a_database_behind_by_a_migration_makes_the_app_refuse_to_start_and_names_it():
    have = {n for p, n, _ in schema_check.REQUIRED if p != "0076"}
    with pytest.raises(schema_check.SchemaOutOfDate, match="migration 0076"):
        await schema_check.check(_Engine(have))


def test_the_first_run_guide_says_migrate_before_starting():
    guide = (ROOT / "docs" / "first-run-guide.md").read_text(encoding="utf-8").lower()
    assert "refuses to start" in guide and "migrate" in guide
