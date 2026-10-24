"""Shared live-test helpers.

`ensure_app_role` replaces the per-test `grant careflow_app to current_user with set true`. That grant, run inside each test's transaction, takes a
row lock on the role membership that is held until the (always rolled-back) transaction ends. With three CI shards running at once, one shard's
open transaction made another shard's fixture wait until the statement timeout (2 minutes), failing 72 + 4 tests in the first sharded run
(PR #2, 2026-10-24). The CI `migrate` job now commits the grant once (scripts/ci_grant_set_role.py); a fixture grants inside its own transaction
only when the membership is genuinely missing (a fresh developer project), which is the old behaviour and rolls back with the test.
"""

CHECK = "select pg_has_role(current_user, 'careflow_app', 'SET')"
GRANT = "grant careflow_app to current_user with set true"


async def ensure_app_role(conn) -> None:
    """asyncpg connection."""
    if not await conn.fetchval(CHECK):
        await conn.execute(GRANT)


async def ensure_app_role_sa(conn) -> None:
    """SQLAlchemy AsyncConnection."""
    if not (await conn.exec_driver_sql(CHECK)).scalar():
        await conn.exec_driver_sql(GRANT)
