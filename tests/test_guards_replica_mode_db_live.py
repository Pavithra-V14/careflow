"""Migration 0068 against the real database: every guard trigger that fires on UPDATE, DELETE or TRUNCATE only is ENABLE ALWAYS, so a session in replica mode (a restore, logical replication) cannot rewrite or empty
an append-only ledger. Read-only catalog checks, plus a behavioural check where the connection role may switch to replica mode. Inside one never-committed outer transaction.
"""

import os
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parent.parent
MIGRATOR = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(ROOT / ".env").get("DATABASE_URL_MIGRATOR")
pytestmark = [pytest.mark.asyncio(loop_scope="module"), pytest.mark.skipif(not MIGRATOR, reason="DATABASE_URL_MIGRATOR not configured")]
SCHEMAS = ("iam", "mpi", "sched", "clinical", "docs", "msg", "kb", "agents", "ai", "ops", "audit")
GUARDS = """
select c.relnamespace::regnamespace::text || '.' || c.relname as tbl, tg.tgname, tg.tgenabled::text as tgenabled
  from pg_trigger tg join pg_class c on c.oid = tg.tgrelid join pg_proc p on p.oid = tg.tgfoid
 where not tg.tgisinternal and c.relnamespace::regnamespace::text = any(:schemas)
   and (tg.tgtype & 4) = 0 and (tg.tgtype & (8 | 16 | 32)) <> 0 and p.proname not like '%touch%' and p.proname <> 'alerts_ack_once'
"""


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def _conn():
    engine = create_async_engine(make_url(MIGRATOR or "").set(drivername="postgresql+asyncpg"), connect_args={"statement_cache_size": 0})
    conn = await engine.connect()
    outer = await conn.begin()
    try:
        yield conn
    finally:
        await outer.rollback()
        await conn.close()
        await engine.dispose()


async def test_every_update_delete_or_truncate_guard_is_enable_always(_conn):
    rows = (await _conn.execute(text(GUARDS), {"schemas": list(SCHEMAS)})).all()
    assert len(rows) > 40, "the catalog query found too few guards: it is looking in the wrong place"
    loose = [f"{r.tbl}.{r.tgname}" for r in rows if r.tgenabled != "A"]
    assert not loose, f"guards still enabled the ordinary way: {loose}"


async def test_the_functional_triggers_that_fire_on_insert_are_left_alone(_conn):
    """Triggers that do work on INSERT (expiry dates, defaults, the review-task fan-out) must NOT replay in a restore."""
    rows = (
        await _conn.execute(
            text(
                "select tg.tgname, tg.tgenabled::text as tgenabled from pg_trigger tg join pg_class c on c.oid = tg.tgrelid "
                "where not tg.tgisinternal and c.relnamespace::regnamespace::text = any(:s) and tg.tgname in ('results_fan_out', 'voice_chunks_guard', 'results_infer_kind', 'voice_preferences_touch')"
            ),
            {"s": list(SCHEMAS)},
        )
    ).all()
    assert {r.tgname for r in rows} >= {"results_fan_out", "voice_chunks_guard", "voice_preferences_touch"}
    assert all(r.tgenabled == "O" for r in rows), [(r.tgname, r.tgenabled) for r in rows]


async def test_a_replica_mode_session_still_cannot_empty_or_rewrite_a_ledger(_conn):
    sp = await _conn.begin_nested()
    try:
        try:
            await _conn.exec_driver_sql("set local session_replication_role = replica")
        except DBAPIError:
            return  # this connection role may not switch to replica mode: the catalog checks above are the evidence here
        await _conn.exec_driver_sql("set local session_replication_role = replica")
        probe = uuid.uuid4()
        with pytest.raises(DBAPIError, match="append-only|never deleted|not permitted"):
            async with _conn.begin_nested():
                await _conn.execute(text("delete from audit.log where idempotency_key = :k"), {"k": f"no-such-{probe}"})
                await _conn.execute(text("truncate audit.log"))
    finally:
        await sp.rollback()
