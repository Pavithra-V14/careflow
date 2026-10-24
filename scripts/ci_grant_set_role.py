"""Commit, once, the role membership the live tests need (`careflow_app` SET-able by the migrator), so no test has to grant it inside its own
transaction (see tests/db_helpers.py for why that collided across CI shards). Idempotent; touches only the CI project, only through the
migrator URL. Prints one line and nothing secret."""

import asyncio
import os
import sys

import asyncpg


async def main() -> int:
    dsn = (os.environ.get("DATABASE_URL_MIGRATOR") or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    if not dsn:
        print("DATABASE_URL_MIGRATOR is not set", file=sys.stderr)
        return 1
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        if await conn.fetchval("select pg_has_role(current_user, 'careflow_app', 'SET')"):
            print("careflow_app is already SET-able by the migrator")
        else:
            await conn.execute("grant careflow_app to current_user with set true")
            print("granted careflow_app to the migrator with SET")
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
