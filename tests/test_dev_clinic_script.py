"""scripts/dev_clinic.py `create` guard and `detach`, and the second-seeded-clinic guard of scripts/seed_dev_demo_data.py: offline (owner decision 2026-11-11).
The database is a recording fake: the tests pin which statements run, that a dry run and every refusal roll back, and that nothing is ever deleted."""

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


dev = _load("dev_clinic")
seed = _load("seed_dev_demo_data")
URL = "https://abcdefgh.supabase.co"
DB = "postgresql://postgres.abcdefgh:not-a-real-password@aws-0-example.pooler.supabase.com:5432/postgres"


def test_a_second_clinic_needs_the_flag():
    one = [uuid.uuid4()]
    assert dev.refuse_a_second_clinic([], False) is None
    assert "already exist" in dev.refuse_a_second_clinic(one, False)
    assert dev.refuse_a_second_clinic(one, True) is None


def test_a_second_seeded_clinic_needs_the_flag():
    seed.refuse_a_second_seeded_clinic([], False)
    with pytest.raises(seed.Refused, match="already hold demo data"):
        seed.refuse_a_second_seeded_clinic([uuid.uuid4()], False)
    seed.refuse_a_second_seeded_clinic([uuid.uuid4()], True)


def test_the_detach_plan_ends_every_live_role_but_never_an_admin_and_never_leaves_nobody_without_a_clinic():
    assert dev.plan_detach(["nurse", "front_desk"], True) == (["front_desk", "nurse"], None)
    assert dev.plan_detach([], False) == ([], None)  # nothing live: nothing to refuse, nothing to do
    assert dev.plan_detach(["nurse"], False)[1] and "no clinic" in dev.plan_detach(["nurse"], False)[1]
    assert dev.plan_detach(["admin", "nurse"], True)[0] == [] and "admin" in dev.plan_detach(["admin", "nurse"], True)[1]


def test_the_project_check_is_the_seed_scripts_rule():
    assert dev.refuse_unless_same_project(URL, DB, "abcdefgh") == "abcdefgh.supabase.co"
    for url, db, ref in ((URL, DB, None), (URL, DB, "zzzzzzzz"), (URL, DB.replace("abcdefgh", "other"), "abcdefgh"), ("", DB, "abcdefgh"), (URL, "postgresql://postgres:pw@host/db", "abcdefgh")):
        with pytest.raises(ValueError, match="refusing"):
            dev.refuse_unless_same_project(url, db, ref)


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.conn.outcome = "rolled back" if exc_type else "committed"
        return False  # never swallow: _Stop propagates to the function under test


class _Conn:
    def __init__(self, roles, keeps=1, origin="test", known=True):
        self.roles, self.keeps, self.origin, self.known = roles, keeps, origin, known  # roles: [(row id, role)]
        self.executed, self.outcome, self.closed = [], None, False

    def transaction(self):
        return _Tx(self)

    async def fetchval(self, sql, *args):
        if "select origin from iam.clinics" in sql:
            return self.origin
        if "select 1 from iam.clinics" in sql:
            return 1
        if "select 1 from iam.accounts" in sql:
            return 1 if self.known else None
        return self.keeps  # the count of live roles in the clinic to keep

    async def fetch(self, sql, *args):
        return [{"id": i, "role": r} for i, r in self.roles]

    async def execute(self, sql, *args):
        self.executed.append((sql, args))

    async def close(self):
        self.closed = True


async def _run(monkeypatch, conn, dry_run, accounts=None):
    async def connect(*a, **k):
        return conn

    monkeypatch.setattr(dev.asyncpg, "connect", connect)
    return await dev.detach("dsn", uuid.uuid4(), uuid.uuid4(), accounts or [uuid.uuid4()], dry_run)


@pytest.mark.asyncio(loop_scope="module")
async def test_detach_ends_each_role_with_one_audit_row_and_deletes_nothing(monkeypatch, capsys):
    conn = _Conn([(uuid.uuid4(), "front_desk"), (uuid.uuid4(), "nurse")])
    assert await _run(monkeypatch, conn, dry_run=False) == 0
    sql = [s for s, _ in conn.executed]
    assert sum(s.startswith("update iam.staff_roles set revoked_at") for s in sql) == 2 and sum("insert into audit.log" in s for s in sql) == 2
    assert not any("delete" in s.lower() for s in sql) and conn.outcome == "committed" and conn.closed
    assert all("staff.revoke_role" in s and "owner_script" in s for s in sql if "insert into audit.log" in s)
    assert "2 role(s) ended, one audit row each" in capsys.readouterr().out


@pytest.mark.asyncio(loop_scope="module")
async def test_a_dry_run_rolls_everything_back(monkeypatch, capsys):
    conn = _Conn([(uuid.uuid4(), "doctor")])
    assert await _run(monkeypatch, conn, dry_run=True) == 0
    assert conn.outcome == "rolled back" and "dry run: 1 role(s) would end" in capsys.readouterr().out


@pytest.mark.asyncio(loop_scope="module")
async def test_every_refusal_writes_nothing(monkeypatch, capsys):
    for conn in (_Conn([(uuid.uuid4(), "nurse")], keeps=0), _Conn([(uuid.uuid4(), "nurse")], known=False), _Conn([(uuid.uuid4(), "admin")]), _Conn([(uuid.uuid4(), "nurse")], origin="live")):
        assert await _run(monkeypatch, conn, dry_run=False) == 2
        assert conn.outcome == "rolled back" and conn.closed
    assert "refusing" in capsys.readouterr().out


@pytest.mark.asyncio(loop_scope="module")
async def test_an_account_with_no_live_role_is_a_no_op(monkeypatch, capsys):
    conn = _Conn([])
    assert await _run(monkeypatch, conn, dry_run=False) == 0 and conn.executed == []
    assert "nothing to do" in capsys.readouterr().out
