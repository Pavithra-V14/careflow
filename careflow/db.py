"""Connection settings for the restricted application role.

BUILD-SPEC.md §18.1: the app's runtime connection goes through Supabase's *transaction-mode* pooler
(one transaction per request, matching how `set_config('request.jwt.claims', $1, true)` is
transaction-local). A transaction-mode pooler can hand consecutive statements of one client to
different server connections, so driver-side prepared statements must never be relied on:

  * asyncpg's own cache is disabled (`statement_cache_size=0`),
  * SQLAlchemy's dialect-level cache is disabled (`prepared_statement_cache_size=0`),
  * any prepared statement that does get created gets a unique name, so two clients can never
    collide on asyncpg's default sequential names.

The migrator does NOT use this module: it needs a session-mode connection (advisory lock spans
statements) and is configured separately.
"""

import json
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine


def _unique_statement_name() -> str:
    return f"__asyncpg_{uuid4()}__"


def app_connect_args() -> dict:
    """asyncpg connect() arguments required behind a transaction-mode pooler."""
    return {
        "statement_cache_size": 0,
        "prepared_statement_name_func": _unique_statement_name,
    }


def app_engine_url(url: str | URL) -> URL:
    """Normalise a DATABASE_URL_APP value to the asyncpg driver with SQLAlchemy's cache disabled."""
    parsed = make_url(url) if isinstance(url, str) else url
    if parsed.drivername in ("postgresql", "postgres"):
        parsed = parsed.set(drivername="postgresql+asyncpg")
    return parsed.update_query_dict({"prepared_statement_cache_size": "0"})


def make_app_engine(url: str | URL, **engine_kwargs) -> AsyncEngine:
    """Async engine for the restricted app role (NOBYPASSRLS), transaction-pooler safe."""
    connect_args = {**app_connect_args(), **engine_kwargs.pop("connect_args", {})}
    return create_async_engine(app_engine_url(url), connect_args=connect_args, **engine_kwargs)


async def set_request_claims(conn: AsyncConnection, claims: dict) -> None:
    """Make auth.uid() resolve to the verified caller for the current transaction (BUILD-SPEC.md §18.1).

    `set_config(..., true)` with a BOUND parameter, never an interpolated `SET LOCAL`; the third argument
    makes it transaction-local, so a pooled connection carries no identity into the next transaction.
    Call it first inside the request's transaction, with claims that were already verified.
    """
    await conn.execute(text("select set_config('request.jwt.claims', :c, true)"), {"c": json.dumps(claims)})
