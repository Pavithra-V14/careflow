"""Migration runner (BUILD-SPEC.md §17.1, Phase 0 task 3).

Plain `.sql` files in `db/migrations/`, named `NNNN_snake_case.sql`, applied in version order.

  * Checksummed ledger: `<ledger_schema>.ledger` records the sha256 of every applied file, computed
    over LF-normalised bytes (so a CRLF checkout cannot make an unedited file look edited). The
    bytes that are checksummed are exactly the bytes that are executed.
  * Append-only: before anything runs, every ledger row is checked against the file on disk. An
    edited file, a deleted file, or a new file numbered below the highest applied version aborts the
    run with nothing applied. Fix a mistake with a new migration, never by editing an old one.
  * One transaction per migration: the SQL and its ledger row commit or roll back together. A file
    that ends the transaction itself (COMMIT/ROLLBACK) is detected and fails loudly.
  * Session-level advisory lock: held on one connection across the whole run so two runners cannot
    interleave. That needs the SESSION-mode pooler (port 5432); a transaction-mode pooler (6543) can
    hand the connection to someone else mid-lock without erroring, so the runner refuses it (§18.1).
  * The ledger lives in a schema the application role has no privileges on; the runner revokes
    them explicitly rather than relying on defaults.

The ledger DDL is created by the runner itself (it cannot be a numbered migration: the runner needs
it to decide which migrations to run). Run with:  python -m careflow.migrate [--check]
"""

import argparse
import asyncio
import hashlib
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import dotenv_values

DEFAULT_LEDGER_SCHEMA = "migrations"
TX_POOLER_PORT = 6543
MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
# Roles that must never read or write the ledger. Revoked only where the role exists.
_DENIED_ROLES = ("anon", "authenticated", "service_role", "careflow_app")


class MigrationError(Exception):
    """Base class: the run stopped; nothing after the failing step was applied."""


class MigrationNameError(MigrationError):
    """A file in the migrations directory is badly named, or two files share a version."""


class MigrationChecksumMismatch(MigrationError):
    """An applied migration was edited after it ran."""


class MigrationFileMissing(MigrationError):
    """The ledger records a migration whose file is gone (or renamed)."""


class MigrationOutOfOrder(MigrationError):
    """A not-yet-applied file is numbered below the highest applied version."""


class MigrationLockTimeout(MigrationError):
    """Another runner holds the advisory lock."""


class MigrationApplyError(MigrationError):
    """A migration's SQL failed; its transaction was rolled back."""


class MigrationTransactionError(MigrationError):
    """A migration ended the transaction itself, so it can no longer be atomic."""


@dataclass(frozen=True)
class Migration:
    version: int
    filename: str
    checksum: str
    sql: str


def normalized_checksum(data: bytes) -> str:
    """sha256 hex over the bytes with CRLF folded to LF."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def discover(directory: Path) -> list[Migration]:
    """Read every migration file, sorted by version. Rejects stray or duplicate-version files."""
    found: list[Migration] = []
    seen: dict[int, str] = {}
    for path in sorted(directory.iterdir()):
        if path.name == ".gitkeep" or path.is_dir():
            continue
        m = MIGRATION_NAME.match(path.name)
        if not m:
            raise MigrationNameError(f"{path.name}: not of the form NNNN_snake_case.sql")
        version = int(m.group(1))
        if version in seen:
            raise MigrationNameError(f"version {version:04d} is used by both {seen[version]} and {path.name}")
        seen[version] = path.name
        data = path.read_bytes().replace(b"\r\n", b"\n")
        found.append(
            Migration(version, path.name, hashlib.sha256(data).hexdigest(), data.decode("utf-8"))
        )
    return sorted(found, key=lambda x: x.version)


def verify_ledger(applied: dict[str, str], migrations: list[Migration]) -> list[Migration]:
    """Check the ledger against the files; return the pending migrations in order.

    `applied` maps filename -> recorded checksum. Raises before anything is applied if history was
    rewritten in any way.
    """
    by_name = {m.filename: m for m in migrations}
    for filename, recorded in sorted(applied.items()):
        current = by_name.get(filename)
        if current is None:
            raise MigrationFileMissing(f"{filename} is in the ledger but not on disk")
        if current.checksum != recorded:
            raise MigrationChecksumMismatch(
                f"{filename} was edited after it was applied (ledger {recorded[:12]}…, "
                f"file {current.checksum[:12]}…). Migrations are append-only: revert the edit and "
                "fix forward with a new migration."
            )
    pending = [m for m in migrations if m.filename not in applied]
    if applied and pending:
        highest = max(int(MIGRATION_NAME.match(f).group(1)) for f in applied)  # type: ignore[union-attr]
        for m in pending:
            if m.version < highest:
                raise MigrationOutOfOrder(
                    f"{m.filename} is new but numbered below the highest applied version {highest:04d}"
                )
    return pending


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"unsafe ledger schema name: {name!r}")
    return name


def _lock_key(ledger_schema: str) -> int:
    digest = hashlib.sha256(f"careflow-migrate:{ledger_schema}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _dsn(url: str) -> str:
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    if urlparse(dsn).port == TX_POOLER_PORT:
        raise MigrationError(
            "migrator URL is on the transaction-mode pooler (6543); the advisory lock needs the "
            "session-mode pooler (5432) — BUILD-SPEC.md §18.1"
        )
    return dsn


async def _bootstrap(conn: asyncpg.Connection, schema: str) -> None:
    await conn.execute(
        f"""
        create schema if not exists {schema};
        create table if not exists {schema}.ledger (
            filename   text primary key,
            version    integer not null unique,
            checksum   text not null check (checksum ~ '^[0-9a-f]{{64}}$'),
            applied_at timestamptz not null default now(),
            applied_by text not null default current_user
        );
        revoke all on schema {schema} from public;
        revoke all on table {schema}.ledger from public;
        do $$
        declare r text;
        begin
            foreach r in array array{list(_DENIED_ROLES)!r}::text[] loop
                if exists (select 1 from pg_roles where rolname = r) then
                    execute format('revoke all on schema %I from %I', '{schema}', r);
                    execute format('revoke all on table %I.ledger from %I', '{schema}', r);
                end if;
            end loop;
        end $$;
        """
    )


async def _acquire_lock(conn: asyncpg.Connection, key: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not await conn.fetchval("select pg_try_advisory_lock($1)", key):
        if time.monotonic() >= deadline:
            raise MigrationLockTimeout(f"another migration run holds the advisory lock (waited {timeout}s)")
        await asyncio.sleep(0.25)


async def _read_ledger(conn: asyncpg.Connection, schema: str) -> dict[str, str]:
    if await conn.fetchval("select to_regclass($1) is not null", f"{schema}.ledger") is False:
        return {}
    rows = await conn.fetch(f"select filename, checksum from {schema}.ledger")
    return {r["filename"]: r["checksum"] for r in rows}


async def run(
    url: str,
    migrations_dir: Path,
    *,
    ledger_schema: str = DEFAULT_LEDGER_SCHEMA,
    lock_timeout: float = 30.0,
    check_only: bool = False,
) -> list[str]:
    """Verify history, then apply pending migrations. Returns the filenames applied (or, with
    `check_only`, the filenames that would be applied; nothing is written)."""
    schema = _ident(ledger_schema)
    migrations = discover(migrations_dir)
    conn = await asyncpg.connect(_dsn(url), timeout=20, statement_cache_size=0)
    try:
        await _acquire_lock(conn, _lock_key(schema), lock_timeout)
        try:
            if not check_only:
                async with conn.transaction():
                    await _bootstrap(conn, schema)
            pending = verify_ledger(await _read_ledger(conn, schema), migrations)
            if check_only:
                return [m.filename for m in pending]
            applied: list[str] = []
            for m in pending:
                try:
                    async with conn.transaction():
                        before = await conn.fetchval("select pg_current_xact_id()::text")
                        await conn.execute(m.sql)
                        if await conn.fetchval("select pg_current_xact_id()::text") != before:
                            raise MigrationTransactionError(
                                f"{m.filename} ended the transaction itself (COMMIT/ROLLBACK); "
                                "migrations must not manage their own transactions"
                            )
                        await conn.execute(
                            f"insert into {schema}.ledger (filename, version, checksum) values ($1, $2, $3)",
                            m.filename,
                            m.version,
                            m.checksum,
                        )
                except MigrationError:
                    raise
                except Exception as exc:
                    raise MigrationApplyError(f"{m.filename} failed and was rolled back: {exc}") from exc
                applied.append(m.filename)
            return applied
        finally:
            await conn.execute("select pg_advisory_unlock_all()")
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply CareFlow SQL migrations.")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dir", default="db/migrations")
    ap.add_argument("--check", action="store_true", help="verify the ledger against the files; apply nothing")
    args = ap.parse_args()

    url = dotenv_values(args.env_file).get("DATABASE_URL_MIGRATOR") or os.environ.get("DATABASE_URL_MIGRATOR")
    if not url:
        sys.exit("ERROR: DATABASE_URL_MIGRATOR is not set")
    try:
        done = asyncio.run(run(url, Path(args.dir), check_only=args.check))
    except MigrationError as exc:
        sys.exit(f"ERROR: {exc}")
    verb = "pending" if args.check else "applied"
    print(f"{len(done)} migration(s) {verb}" + ("".join(f"\n  {f}" for f in done)))


if __name__ == "__main__":
    main()
