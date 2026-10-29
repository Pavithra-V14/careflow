"""Verify the restricted application role against a live project. Never prints secrets.

    python scripts/verify_app_role.py --env dev [--env-file .env]

Checks (BUILD-SPEC.md §18.1, Phase 0 task 2):
  1. connects as careflow_app through the transaction-mode pooler using careflow.db.make_app_engine
  2. cannot bypass RLS (role attributes) and the pooler-safety settings are actually applied on the
     live connection (not merely present in code)
  3. auth.uid() resolves from `set_config('request.jwt.claims', $1, true)` (bound parameter), the
     claim does not leak into later transactions, and a hostile claim value cannot inject SQL
  4. end to end: an RLS policy written with auth.uid() isolates rows on a throwaway table

Hosted-Supabase fact this script encodes: careflow_app has no USAGE on schema auth and the
`postgres` role cannot grant it (schema auth is owned by supabase_admin), so `select auth.uid()` by
name is denied. RLS policies still evaluate auth.uid() for the role. Check 3 therefore reads
auth.uid() through a SECURITY DEFINER probe in a throwaway schema, not by name.

The throwaway schema is created and dropped with the migrator credentials; nothing is left behind.
"""

# ruff: noqa: BLE001  (a verification script must report every failure, so it catches broadly on purpose)
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import dotenv_values
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from careflow.db import make_app_engine
from careflow.ops_checks import accounts_holding_worker_and_another_role

SPIKE = "_careflow_spike"
failures = 0


def result(ok: bool, msg: str) -> None:
    global failures
    failures += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  {msg}")


def info(msg: str) -> None:
    print(f"  info  {msg}")


def claims(sub: str, **extra) -> str:
    return json.dumps({"sub": sub, "role": "authenticated", **extra})


async def set_claims(conn, payload: str) -> None:
    # The normative mechanism (§18.1): bound parameter, third argument true = transaction-local.
    await conn.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": payload})


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, choices=["dev", "ci"])
    ap.add_argument("--env-file", default=".env")
    args = ap.parse_args()

    env = dotenv_values(args.env_file)
    if env.get("CAREFLOW_ENV") != args.env:
        sys.exit(f"ERROR: {args.env_file} has CAREFLOW_ENV={env.get('CAREFLOW_ENV')!r}, expected {args.env!r}")
    app_url, migrator = env.get("DATABASE_URL_APP") or "", env.get("DATABASE_URL_MIGRATOR") or ""
    if not app_url or not migrator:
        sys.exit("ERROR: DATABASE_URL_APP and DATABASE_URL_MIGRATOR must both be set")
    parsed = urlparse(app_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    info(f"app role user={parsed.username} host={parsed.hostname} port={parsed.port}")

    engine = make_app_engine(app_url)
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())

    print("\n[1] connects as careflow_app (transaction-mode pooler)")
    try:
        async with engine.connect() as c:
            cur, sess = (await c.execute(text("select current_user, session_user"))).one()
        result(cur == "careflow_app", f"current_user={cur} session_user={sess}")
        result(parsed.port == 6543, f"port is the transaction-mode port (6543): {parsed.port}")
    except Exception as e:
        result(False, f"connect failed: {type(e).__name__}: {str(e)[:200]}")
        await engine.dispose()
        sys.exit(1)

    print("\n[2] cannot bypass RLS; pooler-safety settings applied on the live connection")
    async with engine.connect() as c:
        row = (await c.execute(text(
            "select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication "
            "from pg_roles where rolname = current_user"))).one()
        result(tuple(row) == (False,) * 5,
               f"super={row[0]} bypassrls={row[1]} createrole={row[2]} createdb={row[3]} replication={row[4]}")
        raw = await c.get_raw_connection()
        cache = getattr(getattr(raw.driver_connection, "_config", None), "statement_cache_size", "unknown")
        result(cache == 0, f"asyncpg statement_cache_size on the live connection = {cache}")
        sa_cache = getattr(raw.dbapi_connection, "_prepared_statement_cache", "unknown")
        result(sa_cache is None, "SQLAlchemy prepared-statement cache is disabled on the live connection")
    for _ in range(30):  # same statement repeatedly, each in its own transaction
        async with engine.begin() as c:
            await c.execute(text("select 1"))
    async with engine.begin() as c:
        names = [r[0] for r in (await c.execute(text("select name from pg_prepared_statements"))).all()]
    default_style = [n for n in names if n.startswith("__asyncpg_stmt_")]
    unique_style = [n for n in names if n.startswith("__asyncpg_") and not n.startswith("__asyncpg_stmt_")]
    result(not default_style, f"no collision-prone sequential statement names (__asyncpg_stmt_N__): {len(default_style)}")
    result(len(unique_style) >= 1 and all(len(n) == 48 for n in unique_style),
           f"server-side statements carry unique uuid names: {len(unique_style)} seen")
    # Known behaviour, reported not asserted: a statement that ERRORED can linger on a pooled backend.
    info(f"named statements currently visible on this pooled backend: {len(names)}")

    mig = await asyncpg.connect(migrator.replace("postgresql+asyncpg://", "postgresql://", 1),
                                timeout=20, statement_cache_size=0)
    try:
        await mig.execute(f"drop schema if exists {SPIKE} cascade")
        await mig.execute(f"create schema {SPIKE}")
        await mig.execute(f"create table {SPIKE}.notes (owner uuid not null, body text not null)")
        await mig.execute(f"alter table {SPIKE}.notes enable row level security")
        await mig.execute(f"alter table {SPIKE}.notes force row level security")
        await mig.execute(f"create policy own on {SPIKE}.notes for all "
                          f"using (owner = auth.uid()) with check (owner = auth.uid())")
        await mig.execute(f"insert into {SPIKE}.notes values ($1::uuid,'a1'),($1::uuid,'a2'),($2::uuid,'b1')",
                          user_a, user_b)
        # Probe: SECURITY DEFINER runs as the migrator, which has USAGE on schema auth.
        await mig.execute(f"create function {SPIKE}.uid() returns uuid language sql stable "
                          f"security definer set search_path = '' as $$ select auth.uid() $$")
        await mig.execute(f"grant usage on schema {SPIKE} to careflow_app")
        await mig.execute(f"grant execute on function {SPIKE}.uid() to careflow_app")
        await mig.execute(f"grant select, insert on {SPIKE}.notes to careflow_app")

        print("\n[3] auth.uid() from set_config with a bound parameter")
        try:
            async with engine.begin() as c:
                await c.execute(text("select auth.uid()"))
            result(False, "unexpected: careflow_app can call auth.uid() by name")
        except Exception as e:
            info(f"select auth.uid() by name is denied to careflow_app, as expected: {str(e).split(chr(10))[0][-45:]}")
        async with engine.begin() as c:
            v = (await c.execute(text(f"select {SPIKE}.uid()"))).scalar()
        result(v is None, f"no claims -> auth.uid() is NULL (got {v})")
        async with engine.begin() as c:
            await set_claims(c, claims(user_a))
            v = (await c.execute(text(f"select {SPIKE}.uid()::text"))).scalar()
        result(v == user_a, "auth.uid() returns the sub set via set_config('request.jwt.claims', $1, true)")
        async with engine.begin() as c:
            await set_claims(c, claims(user_a, note="x'); drop table nothing; --"))
            v = (await c.execute(text(f"select {SPIKE}.uid()::text"))).scalar()
        result(v == user_a, "a claim value containing quote/semicolon is inert (bound parameter)")
        leaks = 0
        for _ in range(40):
            async with engine.begin() as c:
                await set_claims(c, claims(user_a))
            async with engine.begin() as c:
                if (await c.execute(text(f"select {SPIKE}.uid()"))).scalar() is not None:
                    leaks += 1
        result(leaks == 0, f"claims leaked into a later transaction {leaks}/40 times")

        print("\n[4] end to end: RLS policy using auth.uid() on a throwaway table")

        async def count(sub):
            async with engine.begin() as c:
                if sub:
                    await set_claims(c, claims(sub))
                return (await c.execute(text(f"select count(*) from {SPIKE}.notes"))).scalar()

        result(await count(user_a) == 2, "user A sees exactly their 2 rows")
        result(await count(user_b) == 1, "user B sees exactly their 1 row")
        result(await count(None) == 0, "no identity set -> 0 rows (deny by default)")
        try:
            async with engine.begin() as c:
                await set_claims(c, claims(user_a))
                await c.execute(text(f"insert into {SPIKE}.notes values (cast(:o as uuid), 'forged')"), {"o": user_b})
            result(False, "user A inserted a row owned by B (WITH CHECK not enforced)")
        except Exception as e:
            result("row-level security" in str(e), "user A cannot insert a row owned by user B (WITH CHECK)")
        print("\n[5] worker accounts hold queue_worker only (read-only stop-gap for migration 0024; docs/runbooks/staff-accounts.md)")
        bad = await accounts_holding_worker_and_another_role(mig.fetch)
        result(not bad, f"accounts holding queue_worker together with another role: {len(bad)}" + (f" ({', '.join(bad)})" if bad else ""))
    except Exception as e:
        result(False, f"setup/run failed: {type(e).__name__}: {str(e)[:200]}")
    finally:
        await mig.execute(f"drop schema if exists {SPIKE} cascade")
        gone = await mig.fetchval("select not exists(select 1 from pg_namespace where nspname=$1)", SPIKE)
        info(f"throwaway schema removed: {gone}")
        await mig.close()
        await engine.dispose()

    print(f"\n{'ALL CHECKS PASSED' if failures == 0 else f'{failures} CHECK(S) FAILED'} on {args.env}")
    sys.exit(0 if failures == 0 else 1)


asyncio.run(main())
