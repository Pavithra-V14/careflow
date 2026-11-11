"""Show, or create, the clinic of the DEV project. A person runs it by hand; nothing runs it.

    python scripts/dev_clinic.py show   [--env-file .env]
    python scripts/dev_clinic.py create --name "Demo Clinic" [--env-file .env] [--dry-run]
    python scripts/dev_clinic.py publish --clinic-id <uuid> [--env-file .env] [--dry-run]
    python scripts/dev_clinic.py detach --from-clinic <uuid> --keep-clinic <uuid> --account-id <uuid> [--account-id <uuid> ...] [--env-file .env] [--dry-run]

`create` REFUSES when any clinic already exists, unless `--allow-second-clinic` is passed: two clinics that look alike in one dev project is how slots of one end up next to patients of the other.

`detach` (owner-run, dev only) ends the live roles of the named accounts in `--from-clinic`. Rules: it needs CAREFLOW_DEV_PROJECT_REF and makes you TYPE THE PROJECT HOST like seed_dev_demo_data.py; every
account must hold a live role in `--keep-clinic` (nobody is left with no clinic); it never ends an `admin` role (use the admin route, which keeps a clinic's last administrator); `--dry-run` runs everything and
rolls it back. WHY NOT THE ADMIN ROUTE: iam.revoke_staff_role needs a signed-in administrator of that clinic with a second factor, and this script is not one (and must not pretend to be). It writes the same
revocation (`revoked_at`, never a delete) and the same audit action `staff.revoke_role`, with no actor and the role `owner_script`, one audit row per ended role, in the same transaction.

`publish` marks ONE clinic as the public face of the deployment (migration 0070): its id and name are then served, without sign-in, by GET /public/clinic for the forms a patient fills in before signing in.
Publishing another clinic replaces the first; nothing else changes.

`show` is read-only: it prints every clinic (id, name, origin) and, for each, the staff grants as opaque account ids with their role (no e-mail address, no name: those live in Auth). `create` adds ONE
clinic with origin `test` and prints its id. Both REFUSE unless the env file says CAREFLOW_ENV=dev AND CAREFLOW_PROFILE=demo (never ci, never clinical, never unset), like scripts/seed_dev_staff.py. The
migrator connection is used (the owner). No secret is ever printed.
"""

import argparse
import asyncio
import json
import sys
import uuid
from urllib.parse import unquote, urlsplit

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


def refuse_a_second_clinic(existing: list, allow: bool) -> str | None:
    if existing and not allow:
        return f"refusing: {len(existing)} clinic(s) already exist ({', '.join(str(c) for c in existing)}); pass --allow-second-clinic only if you really want another"
    return None


async def create(dsn: str, name: str, dry_run: bool, allow_second: bool = False) -> int:
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        refusal = refuse_a_second_clinic([r["id"] for r in await conn.fetch("select id from iam.clinics order by id")], allow_second)
        if refusal:
            print(refusal)
            return 2
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


# ---- detach: end a person's roles in one clinic (owner-run, dev only) ---------------------------------------------------------------------------------------------------------------

def project_host(supabase_url: str | None) -> str:
    host = urlsplit(supabase_url or "").hostname or ""
    if not host:
        raise ValueError("refusing: SUPABASE_URL is not set to a project URL")
    return host


def refuse_unless_same_project(supabase_url: str | None, database_url: str | None, dev_ref: str | None) -> str:
    """The project host, once SUPABASE_URL, the migrator URL (user `postgres.<ref>`) and CAREFLOW_DEV_PROJECT_REF all name the same project ref. Same rule as seed_dev_demo_data.py."""
    host = project_host(supabase_url)
    try:
        user = unquote(urlsplit((database_url or "").replace("postgresql+asyncpg://", "postgresql://", 1)).username or "")
    except ValueError:
        raise ValueError("refusing: DATABASE_URL_MIGRATOR could not be read") from None
    ref = user.split(".", 1)[1] if "." in user else None
    if ref is None or host.split(".", 1)[0] != ref:
        raise ValueError("refusing: the migrator URL does not point at the project named by SUPABASE_URL (or carries no project ref)")
    if not dev_ref or dev_ref.strip() != ref:
        raise ValueError("refusing: CAREFLOW_DEV_PROJECT_REF must be set in the env file to the DEV project's ref, and it is not the project this env file points at")
    return host


def plan_detach(live_roles: list[str], keeps_a_live_role: bool) -> tuple[list[str], str | None]:
    """(the roles to end, or a refusal). Nobody is left with no clinic, and an admin role is never ended here."""
    if not live_roles:
        return [], None  # nothing live to end: a repeat run does nothing
    if "admin" in live_roles:
        return [], "refusing: this account is an admin of the clinic; end an admin role through the admin route, which keeps the clinic's last administrator"
    if not keeps_a_live_role:
        return [], "refusing: this account holds no live role in the clinic it is to keep, so detaching it would leave it with no clinic"
    return sorted(live_roles), None


class _Stop(Exception):
    """Rolls the transaction back (a refusal or a dry run); carries the exit code."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


async def detach(dsn: str, from_clinic: uuid.UUID, keep_clinic: uuid.UUID, accounts: list[uuid.UUID], dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        try:
            async with conn.transaction():
                origin = await conn.fetchval("select origin from iam.clinics where id = $1", from_clinic)
                if origin is None or await conn.fetchval("select 1 from iam.clinics where id = $1", keep_clinic) is None:
                    print("refusing: one of the two clinics does not exist (nothing was written)")
                    raise _Stop(2)
                if origin != "test":
                    print("refusing: the clinic to detach from is not a test clinic (nothing was written)")
                    raise _Stop(2)
                ended = 0
                for account in accounts:
                    if await conn.fetchval("select 1 from iam.accounts where id = $1", account) is None:
                        print(f"account {account}: no such account (a typing mistake?)  (nothing was written)")
                        raise _Stop(2)
                    rows = await conn.fetch("select id, role from iam.staff_roles where account_id = $1 and clinic_id = $2 and revoked_at is null order by role for update", account, from_clinic)
                    keeps = await conn.fetchval("select count(*) from iam.staff_roles where account_id = $1 and clinic_id = $2 and revoked_at is null", account, keep_clinic) > 0
                    roles, refusal = plan_detach([r["role"] for r in rows], keeps)
                    if refusal:
                        print(f"account {account}: {refusal}  (nothing was written)")
                        raise _Stop(2)
                    if not roles:
                        print(f"account {account}: no live role in clinic {from_clinic}, nothing to do")
                        continue
                    for r in rows:
                        await conn.execute("update iam.staff_roles set revoked_at = clock_timestamp() where id = $1 and revoked_at is null", r["id"])
                        await conn.execute(
                            "insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key) "
                            "values ($1, $2, null, 'owner_script', 'staff.revoke_role', 'account', $3, $4::jsonb, $5)",
                            origin, from_clinic, str(account), json.dumps({"role": r["role"], "by": "dev_clinic.py detach"}), f"script-detach:{r['id']}",
                        )
                        ended += 1
                        print(f"account {account}: ends role {r['role']} in clinic {from_clinic} (stays in {keep_clinic})")
                if dry_run:
                    print(f"dry run: {ended} role(s) would end; everything is rolled back, nothing was kept")
                    raise _Stop(0)
                print(f"{ended} role(s) ended, one audit row each; committing")
        except _Stop as stop:
            return stop.code
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("show", "create", "publish", "detach"))
    ap.add_argument("--from-clinic", type=uuid.UUID)
    ap.add_argument("--keep-clinic", type=uuid.UUID)
    ap.add_argument("--account-id", type=uuid.UUID, action="append", default=[])
    ap.add_argument("--allow-second-clinic", action="store_true")
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
    if args.command == "detach":
        if args.from_clinic is None or args.keep_clinic is None or not args.account_id or args.from_clinic == args.keep_clinic:
            print("detach needs --from-clinic, a different --keep-clinic, and at least one --account-id")
            return 2
        try:
            host = refuse_unless_same_project(env.get("SUPABASE_URL"), env.get("DATABASE_URL_MIGRATOR"), env.get("CAREFLOW_DEV_PROJECT_REF"))
        except ValueError as exc:
            print(exc)
            return 2
        print(f"target project host: {host}")
        try:
            typed = input("Type that host exactly to confirm (anything else stops here): ")
        except EOFError:
            print("refusing: no host was typed")
            return 2
        if typed.strip() != host:
            print("refusing: the host you typed is not the project host shown")
            return 2
        return asyncio.run(detach(dsn, args.from_clinic, args.keep_clinic, list(dict.fromkeys(args.account_id)), args.dry_run))
    return asyncio.run(show(dsn) if args.command == "show" else create(dsn, args.name, args.dry_run, args.allow_second_clinic))


if __name__ == "__main__":
    sys.exit(main())
