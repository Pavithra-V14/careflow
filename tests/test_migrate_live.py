"""Migration runner against a real hosted project (dev or CI). BUILD-SPEC.md §17.1, §18.1.

Skipped only when DATABASE_URL_MIGRATOR is not configured. Every test uses its own scratch ledger
schema and scratch schema, both dropped afterwards, so the project's real ledger is never touched.
"""

import os
import uuid
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
from dotenv import dotenv_values

from careflow import migrate

_ENV = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or _ENV.get("DATABASE_URL_MIGRATOR")
APP = os.environ.get("DATABASE_URL_APP") or _ENV.get("DATABASE_URL_APP")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured"),
]


def _connect(url: str):
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return asyncpg.connect(dsn, statement_cache_size=0, timeout=20)


@pytest_asyncio.fixture
async def scratch(tmp_path):
    """(ledger_schema, work_schema, migrations_dir); both schemas dropped on teardown."""
    tag = uuid.uuid4().hex[:10]
    ledger, work = f"_mt_ledger_{tag}", f"_mt_work_{tag}"
    yield ledger, work, tmp_path
    conn = await _connect(MIGRATOR)
    try:
        await conn.execute(f"drop schema if exists {ledger} cascade; drop schema if exists {work} cascade;")
    finally:
        await conn.close()


async def _run(scratch, **kw):
    ledger, _, d = scratch
    return await migrate.run(MIGRATOR, d, ledger_schema=ledger, lock_timeout=kw.pop("lock_timeout", 5), **kw)


async def _ledger_rows(ledger: str) -> list[str]:
    conn = await _connect(MIGRATOR)
    try:
        rows = await conn.fetch(f"select filename from {ledger}.ledger order by version")
        return [r["filename"] for r in rows]
    finally:
        await conn.close()


async def test_applies_in_order_records_checksums_and_is_idempotent(scratch):
    ledger, work, d = scratch
    (d / "0001_create.sql").write_text(f"create schema {work};\ncreate table {work}.t (id int);\n")
    (d / "0002_insert.sql").write_text(f"insert into {work}.t values (1);\n")
    assert await _run(scratch) == ["0001_create.sql", "0002_insert.sql"]
    assert await _run(scratch) == []
    conn = await _connect(MIGRATOR)
    try:
        row = await conn.fetchrow(f"select checksum, applied_by from {ledger}.ledger where version = 1")
    finally:
        await conn.close()
    assert row["checksum"] == migrate.discover(d)[0].checksum
    assert row["applied_by"]


async def test_tampering_with_an_applied_file_fails_and_applies_nothing_new(scratch):
    ledger, work, d = scratch
    first = d / "0001_create.sql"
    first.write_text(f"create schema {work};\ncreate table {work}.t (id int);\n")
    await _run(scratch)
    first.write_text(f"create schema {work};\ncreate table {work}.t (id int, sneaky text);\n")
    (d / "0002_next.sql").write_text(f"insert into {work}.t (id) values (1);\n")
    with pytest.raises(migrate.MigrationChecksumMismatch):
        await _run(scratch)
    with pytest.raises(migrate.MigrationChecksumMismatch):
        await _run(scratch, check_only=True)
    assert await _ledger_rows(ledger) == ["0001_create.sql"]  # 0002 was not applied behind the tamper


async def test_deleted_applied_file_fails(scratch):
    _, work, d = scratch
    f = d / "0001_create.sql"
    f.write_text(f"create schema {work};\n")
    await _run(scratch)
    f.unlink()
    with pytest.raises(migrate.MigrationFileMissing):
        await _run(scratch)


async def test_failing_migration_rolls_back_its_own_statements_and_ledger_row(scratch):
    ledger, work, d = scratch
    (d / "0001_ok.sql").write_text(f"create schema {work};\n")
    (d / "0002_bad.sql").write_text(f"create table {work}.t (id int);\nselect 1/0;\n")
    (d / "0003_never.sql").write_text(f"create table {work}.never (id int);\n")
    with pytest.raises(migrate.MigrationApplyError):
        await _run(scratch)
    assert await _ledger_rows(ledger) == ["0001_ok.sql"]  # earlier success kept, failure absent, no later run
    conn = await _connect(MIGRATOR)
    try:
        tables = await conn.fetch("select 1 from pg_tables where schemaname = $1", work)
    finally:
        await conn.close()
    assert tables == []  # the first statement of the failed migration was rolled back too


async def test_migration_that_commits_itself_is_detected(scratch):
    ledger, work, d = scratch
    (d / "0001_sneaky.sql").write_text(f"create schema {work};\ncommit;\ncreate table {work}.t (id int);\n")
    with pytest.raises(migrate.MigrationError):
        await _run(scratch)
    assert await _ledger_rows(ledger) == []


async def test_second_runner_cannot_take_the_held_lock(scratch):
    ledger, _, _ = scratch
    holder = await _connect(MIGRATOR)
    try:
        assert await holder.fetchval("select pg_try_advisory_lock($1)", migrate._lock_key(ledger))
        with pytest.raises(migrate.MigrationLockTimeout):
            await _run(scratch, lock_timeout=1)
    finally:
        await holder.close()
    assert await _run(scratch) == []  # lock released with the holder's session


@pytest.mark.skipif(not APP, reason="DATABASE_URL_APP not configured")
async def test_application_role_has_no_access_to_the_ledger(scratch):
    ledger, work, d = scratch
    (d / "0001_create.sql").write_text(f"create schema {work};\n")
    await _run(scratch)
    conn = await _connect(APP)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.fetch(f"select * from {ledger}.ledger")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"delete from {ledger}.ledger")
    finally:
        await conn.close()
