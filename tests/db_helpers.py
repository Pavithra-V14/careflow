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


def aal2_claims(sub, **extra) -> dict:
    """Claims of a staff session with a verified authenticator-app factor (what iam.aal2_session() reads, migration 0047)."""
    import time

    return {"sub": str(sub), "role": "authenticated", "aal": "aal2", "amr": [{"method": "totp", "timestamp": int(time.time()) - 60}], **extra}


def without_scope(details) -> dict:
    """An audit row's `details` without `role_scope`, which every audit writer now adds (migration 0028: 'active' when the request carried a verified X-CareFlow-Active-Role, else 'all_grants').
    The tests that compare the rest exactly keep doing so; this asserts the key is there and well formed."""
    import json

    d = dict(json.loads(details) if isinstance(details, str) else details)
    assert d.pop("role_scope") in ("all_grants", "active"), d
    return d
