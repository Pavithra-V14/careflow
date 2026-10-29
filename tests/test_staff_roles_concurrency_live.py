"""Migration 0024 under concurrency, against the real hosted project: two COMMITTED sessions race to give one account front_desk and queue_worker; exactly one may win (the trigger
takes a per-account advisory lock). Uses its own never-shared accounts and clinic (random ids, origin 'test'), commits them, and removes what it can afterwards (a killed run leaves
tagged test rows; nothing reads them). Two real connections, so this is the one place the lock itself is exercised."""

import asyncio
import os
import uuid
from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
URL = (MIGRATOR or "").replace("postgresql+asyncpg://", "postgresql://", 1)


async def connect():
    return await asyncpg.connect(URL, timeout=30, statement_cache_size=0)


async def grant(role: str, account, clinic) -> str:
    """Insert one grant on its own connection and commit; return 'ok' or the SQLSTATE."""
    conn = await connect()
    try:
        await conn.execute("insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, 'test')", account, clinic, role)
        return "ok"
    except asyncpg.PostgresError as exc:
        return exc.sqlstate or "?"
    finally:
        await conn.close()


async def test_the_trigger_is_enabled_always_and_two_concurrent_conflicting_grants_cannot_both_commit():
    clinic, accounts = uuid.uuid4(), [uuid.uuid4() for _ in range(8)]
    setup = await connect()
    try:
        assert await setup.fetchval("select tgenabled from pg_trigger where tgname = 'staff_roles_queue_worker_exclusive'") in ("A", b"A")  # ENABLE ALWAYS ("char" comes back as bytes from asyncpg)
        assert await setup.fetchval("select has_function_privilege('careflow_app', 'iam.keep_queue_worker_exclusive()', 'execute')") is False
        await setup.execute("insert into iam.clinics (id, name, origin) values ($1, 'Concurrency', 'test')", clinic)
        await setup.executemany("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', 'test')", [(a,) for a in accounts])
        results = []
        for account in accounts:  # a fresh account per race, both writers released together
            results.append(await asyncio.gather(grant("front_desk", account, clinic), grant("queue_worker", account, clinic)))
        for account, (first, second) in zip(accounts, results, strict=True):
            assert sorted([first, second]) == ["23514", "ok"], (first, second)  # exactly one commits, the other is refused by the trigger
            held = await setup.fetchval("select count(distinct role) from iam.staff_roles where account_id = $1 and revoked_at is null", account)
            assert held == 1, "both grants committed: the exclusivity invariant is broken"
    finally:
        try:
            await setup.execute("delete from iam.staff_roles where clinic_id = $1", clinic)
            await setup.execute("delete from iam.accounts where id = any($1::uuid[])", accounts)
            await setup.execute("delete from iam.clinics where id = $1", clinic)
        except asyncpg.PostgresError:
            pass  # tagged origin='test' rows are harmless; nothing reads them
        await setup.close()
