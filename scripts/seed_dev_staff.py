"""Create ONE staff account in the DEV project (a Supabase Auth user, its iam.accounts row, one iam.staff_roles grant). NOT run by anything; a person runs it by hand.

    python scripts/seed_dev_staff.py --email nurse@example.test --role nurse --clinic-id <uuid> [--env-file .env] [--dry-run]

Rules it enforces (owner decision 2026-10-28; docs/runbooks/staff-accounts.md):
  * it REFUSES to run unless the env file says CAREFLOW_ENV=dev AND CAREFLOW_PROFILE=demo (never ci, never clinical, never an unset profile);
  * it PROMPTS for the password (getpass, twice, at least 12 characters). The password is never an argument, never in the environment, never printed, never written to a file, and
    never logged; it goes only into the HTTPS request that creates the Auth user, and the variable is cleared afterwards;
  * it creates only the roles a dev staff account needs: front_desk, nurse, doctor, admin, auditor, business_owner, or queue_worker;
  * ONE role per run, and a `queue_worker` account never gets another role (and no other account gets queue_worker): checked here before any write, and by the database trigger of
    migration 0024 as the last line of defence;
  * the account is tagged origin='test' (dev data is synthetic) unless --origin says otherwise;
  * it prints the new account id and role, nothing secret. The Supabase secret key (SUPABASE_SECRET_KEY, one-time setup per the settled key system) is read from the env file and
    sent only as the admin API credential; it is never printed.
It adds no service: it calls the project's own Supabase Auth admin endpoint, which this project already depends on.
"""

import argparse
import asyncio
import getpass
import sys
import uuid
from pathlib import Path

import asyncpg
import httpx
from dotenv import dotenv_values

ROLES = ("front_desk", "nurse", "doctor", "admin", "auditor", "business_owner", "queue_worker")
MIN_PASSWORD_CHARS = 12


class Refused(Exception):
    """A rule of this script, stated in plain words. Never carries a secret."""


def refuse_unless_dev_demo(env: dict) -> None:
    if env.get("CAREFLOW_ENV") != "dev":
        raise Refused(f"refusing: CAREFLOW_ENV is {env.get('CAREFLOW_ENV')!r}, this script runs against the dev project only")
    if env.get("CAREFLOW_PROFILE") != "demo":
        raise Refused(f"refusing: CAREFLOW_PROFILE is {env.get('CAREFLOW_PROFILE')!r}, this script runs in the demo profile only")


def roles_after(existing: set[str], new: str) -> set[str]:
    """The account's active roles after the grant, or Refused. queue_worker never coexists with any other role."""
    if new not in ROLES:
        raise Refused(f"refusing: {new!r} is not a role a dev staff account needs ({', '.join(ROLES)})")
    if new == "queue_worker" and existing - {"queue_worker"}:
        raise Refused("refusing: this account already holds another role, and a queue_worker account holds no other role")
    if new != "queue_worker" and "queue_worker" in existing:
        raise Refused("refusing: this account is a queue_worker, and a queue_worker account holds no other role")
    return existing | {new}


def check_password(password: str, again: str) -> None:
    if password != again:
        raise Refused("refusing: the two passwords differ")
    if len(password) < MIN_PASSWORD_CHARS:
        raise Refused(f"refusing: the password must be at least {MIN_PASSWORD_CHARS} characters")


async def create_auth_user(supabase_url: str, secret_key: str, email: str, password: str) -> uuid.UUID:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{supabase_url.rstrip('/')}/auth/v1/admin/users",
            headers={"apikey": secret_key, "Authorization": f"Bearer {secret_key}"},
            json={"email": email, "password": password, "email_confirm": True},
        )
    if r.status_code not in (200, 201):
        raise Refused(f"the Auth admin endpoint refused the request (HTTP {r.status_code}); nothing was written to the database")
    return uuid.UUID(r.json()["id"])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--role", required=True, choices=ROLES)
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--origin", default="test", choices=["test", "eval", "canary", "live"])
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dry-run", action="store_true", help="check the rules and print the plan; ask for no password and call nothing")
    args = ap.parse_args()
    env = dict(dotenv_values(Path(args.env_file)))
    try:
        refuse_unless_dev_demo(env)
        migrator = (env.get("DATABASE_URL_MIGRATOR") or "").replace("postgresql+asyncpg://", "postgresql://", 1)
        if not migrator or not env.get("SUPABASE_URL") or not env.get("SUPABASE_SECRET_KEY"):
            raise Refused("refusing: DATABASE_URL_MIGRATOR, SUPABASE_URL and SUPABASE_SECRET_KEY must be set in the env file")
        conn = await asyncpg.connect(migrator, timeout=20, statement_cache_size=0)
        try:
            if not await conn.fetchval("select exists (select 1 from iam.clinics where id = $1)", args.clinic_id):
                raise Refused("refusing: that clinic does not exist in this project")
            account = await conn.fetchval(
                "select a.id from iam.accounts a where a.id in (select id from auth.users where lower(email) = lower($1))", args.email
            )
            existing = set(await conn.fetchval("select coalesce(array_agg(role), '{}') from iam.staff_roles where account_id = $1 and revoked_at is null", account) or []) if account else set()
            after = roles_after(existing, args.role)
            print(f"plan: {'existing account ' + str(account) if account else 'a new account'} will hold {sorted(after)} (clinic {args.clinic_id}, origin {args.origin})")
            if args.dry_run:
                return 0
            if account is None:
                password = getpass.getpass("Password for the new account (not shown): ")
                again = getpass.getpass("Again: ")
                try:
                    check_password(password, again)
                    account = await create_auth_user(env["SUPABASE_URL"], env["SUPABASE_SECRET_KEY"], args.email, password)
                finally:
                    password = again = ""  # not kept, not printed, not written anywhere
                await conn.execute("insert into iam.accounts (id, account_type, status, origin) values ($1, 'staff', 'active', $2)", account, args.origin)
            await conn.execute(
                "insert into iam.staff_roles (account_id, clinic_id, role, origin) values ($1, $2, $3, $4) on conflict do nothing", account, args.clinic_id, args.role, args.origin
            )
            print(f"done: account {account} holds {args.role} in clinic {args.clinic_id}")
        finally:
            await conn.close()
    except Refused as exc:
        print(str(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
