"""One-time setup: create the restricted application role and write DATABASE_URL_APP.

BUILD-SPEC.md §18.1 / §21, Phase 0 task 2. Run with the migrator credentials against ONE project:

    python scripts/create_app_role.py --env dev  [--env-file .env]
    python scripts/create_app_role.py --env ci   --env-file .env.cli

The role is deliberately NOT created by a migration: a migration is committed, and the role's
password must never be. Grants live in migrations (they hold no secret).

Never prints a password or connection string. Refuses to run if the env file's CAREFLOW_ENV does
not equal --env, so a dev run can't be pointed at the CI project by mistake.
"""

import argparse
import asyncio
import re
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
from dotenv import dotenv_values

ROLE = "careflow_app"
TX_POOLER_PORT = 6543


def die(msg: str) -> None:
    print(f"ERROR: {msg}")
    sys.exit(1)


def set_env_line(path: Path, key: str, value: str) -> None:
    """Replace `KEY=...` in place, preserving every other line byte for byte."""
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    idx = [i for i, ln in enumerate(lines) if re.match(rf"^{key}=", ln)]
    if len(idx) != 1:
        die(f"expected exactly one `{key}=` line in {path.name}, found {len(idx)}")
    lines[idx[0]] = f"{key}={value}"
    path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, choices=["dev", "ci"])
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--rotate", action="store_true", help="reset the password of an existing role")
    args = ap.parse_args()

    path = Path(args.env_file)
    env = dotenv_values(path)
    if env.get("CAREFLOW_ENV") != args.env:
        die(f"{path.name} has CAREFLOW_ENV={env.get('CAREFLOW_ENV')!r}, expected {args.env!r}")

    migrator = env.get("DATABASE_URL_MIGRATOR") or ""
    if not migrator:
        die("DATABASE_URL_MIGRATOR is blank")
    p = urlparse(migrator.replace("postgresql+asyncpg://", "postgresql://", 1))
    if p.port == TX_POOLER_PORT:
        die("migrator URL is on the transaction-mode port; it must be the session pooler (5432)")
    username = p.username or ""
    if "." not in username:
        die("migrator username is not in the pooler form postgres.<project-ref>")
    project_ref = username.split(".", 1)[1]

    current_app_url = env.get("DATABASE_URL_APP") or ""
    conn = await asyncpg.connect(
        migrator.replace("postgresql+asyncpg://", "postgresql://", 1),
        timeout=20,
        statement_cache_size=0,
    )
    try:
        exists = await conn.fetchval("select exists(select 1 from pg_roles where rolname = $1)", ROLE)
        if exists and not args.rotate:
            print(f"Role {ROLE} already exists on {args.env}; nothing changed. Use --rotate to reset its password.")
            return
        if current_app_url and not args.rotate:
            die("DATABASE_URL_APP is already set; refusing to overwrite (use --rotate)")

        password = secrets.token_urlsafe(32)  # [A-Za-z0-9_-] only: URL-safe, no encoding needed
        verb = "alter role" if exists else "create role"
        # DDL cannot take bind parameters: let the server quote the identifier and the literal.
        ddl = await conn.fetchval(
            f"select format('{verb} %I login nosuperuser nobypassrls nocreatedb nocreaterole "
            f"noreplication password %L', $1::text, $2::text)",
            ROLE,
            password,
        )
        await conn.execute(ddl)
        print(f"{'Reset password of' if exists else 'Created'} role {ROLE} on {args.env} "
              "(LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION).")
    finally:
        await conn.close()

    app_url = (
        f"postgresql+asyncpg://{ROLE}.{project_ref}:{password}@{p.hostname}:{TX_POOLER_PORT}"
        f"{p.path or '/postgres'}"
    )
    set_env_line(path, "DATABASE_URL_APP", app_url)
    print(f"Wrote DATABASE_URL_APP to {path.name} (transaction-mode pooler, port {TX_POOLER_PORT}); value not shown.")


asyncio.run(main())
