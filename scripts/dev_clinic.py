"""Show, or create, the clinic of the DEV project. A person runs it by hand; nothing runs it.

    python scripts/dev_clinic.py show   [--env-file .env]
    python scripts/dev_clinic.py create --name "Demo Clinic" [--env-file .env] [--dry-run]
    python scripts/dev_clinic.py publish --clinic-id <uuid> [--env-file .env] [--dry-run]

`publish` marks ONE clinic as the public face of the deployment (migration 0070): its id and name are then served, without sign-in, by GET /public/clinic for the forms a patient fills in before signing in.
Publishing another clinic replaces the first; nothing else changes.

`show` is read-only: it prints every clinic (id, name, origin) and, for each, the staff grants as opaque account ids with their role (no e-mail address, no name: those live in Auth). `create` adds ONE
clinic with origin `test` and prints its id. Both REFUSE unless the env file says CAREFLOW_ENV=dev AND CAREFLOW_PROFILE=demo (never ci, never clinical, never unset), like scripts/seed_dev_staff.py. The
migrator connection is used (the owner). No secret is ever printed.
"""

import argparse
import asyncio
import sys
import uuid

import asyncpg
from dotenv import dotenv_values


def refuse_unless_dev_demo(env: dict) -> str | None:
    if (env.get("CAREFLOW_ENV") or "").strip().lower() != "dev" or (env.get("CAREFLOW_PROFILE") or "").strip().lower() != "demo":
        return "refusing: the env file must say CAREFLOW_ENV=dev and CAREFLOW_PROFILE=demo"
    if not (env.get("DATABASE_URL_MIGRATOR") or "").strip():
        return "refusing: DATABASE_URL_MIGRATOR is not set"
    return None


async def show(dsn: str) -> int:
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        clinics = await conn.fetch("select id, name, origin from iam.clinics order by name, id")
        if not clinics:
            print("no clinic yet: run `python scripts/dev_clinic.py create --name \"Demo Clinic\"`")
            return 0
        for c in clinics:
            print(f"clinic {c['id']}  name={c['name']!r}  origin={c['origin']}")
            for r in await conn.fetch("select account_id, role from iam.staff_roles where clinic_id = $1 and revoked_at is null order by role, account_id", c["id"]):
                print(f"    {r['role']:<15} account {r['account_id']}")
        return 0
    finally:
        await conn.close()


async def create(dsn: str, name: str, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        existing = await conn.fetch("select id from iam.clinics where name = $1 and origin = 'test'", name)
        if existing:
            print(f"a clinic with that name already exists: {existing[0]['id']} (nothing was written)")
            return 0
        if dry_run:
            print("dry run: a clinic would be created (nothing was written)")
            return 0
        clinic = uuid.uuid4()
        await conn.execute("insert into iam.clinics (id, name, origin) values ($1, $2, 'test')", clinic, name)
        print(f"created clinic {clinic}")
        return 0
    finally:
        await conn.close()


async def publish(dsn: str, clinic_id: uuid.UUID, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        name = await conn.fetchval("select name from iam.clinics where id = $1", clinic_id)
        if name is None:
            print("no such clinic (nothing was written)")
            return 2
        if dry_run:
            print(f"dry run: clinic {clinic_id} ({name!r}) would be published (nothing was written)")
            return 0
        async with conn.transaction():
            await conn.execute("delete from iam.public_clinics")
            await conn.execute("insert into iam.public_clinics (clinic_id) values ($1)", clinic_id)
        print(f"published clinic {clinic_id} ({name!r}) as the public clinic")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("show", "create", "publish"))
    ap.add_argument("--clinic-id", type=uuid.UUID)
    ap.add_argument("--name", default="Demo Clinic")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    env = {**dotenv_values(args.env_file)}
    problem = refuse_unless_dev_demo(env)
    if problem:
        print(problem)
        return 2
    dsn = str(env["DATABASE_URL_MIGRATOR"]).replace("postgresql+asyncpg://", "postgresql://", 1)
    if args.command == "publish":
        if args.clinic_id is None:
            print("publish needs --clinic-id")
            return 2
        return asyncio.run(publish(dsn, args.clinic_id, args.dry_run))
    return asyncio.run(show(dsn) if args.command == "show" else create(dsn, args.name, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
