"""Fix the ORIGIN of ONE stale patient portal account in the DEV database, so it can claim the synthetic demo patient. A person runs it by hand; nothing runs it.

    uv run python scripts/reset_dev_portal_account.py --account-id <the auth user's id> [--env-file .env] [--dry-run]

When is it needed? A patient account's origin tag comes from the profile of the backend that registered it (demo means `test`, anything else `live`). If the account was registered while the backend ran WITHOUT
the demo profile (for example plain uvicorn), it carries origin `live` and can never claim a record of the `test` dev clinic: the claim answers 403 "origin mismatch". Registering again does not help (it answers
`exists` and keeps the old origin). This script sets that ONE account's origin to `test`. Find the id in the Supabase dashboard (Authentication, Users, "User UID"): the script never asks for an e-mail address
or a password and never prints one.

What it touches: ONE row of `iam.accounts` (its `origin`), and nothing else. It never writes a patient record, a consent, a claim, an audit row or anything append-only (the consent decisions already given stay as
they are and still count). It refuses, with fixed words, unless: the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; the env names the DEV project; YOU TYPE THE PROJECT
HOST; the id is a PATIENT account (not staff, no staff role at all); it is not yet bound to a patient record; and it has no claim request pending or resolved (a bound or claiming account is left alone). It prints
counts and the origin word only. `--dry-run` shows what it would do and rolls back.
"""

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import seed_dev_demo_data as seed
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

Refused = seed.Refused
TARGET_ORIGIN = "test"


def decide(account: dict | None, staff_roles: int, bound: int, claim_requests: int) -> tuple[str, str]:
    """(action, fixed words): action is `refuse`, `nothing` or `fix`. Pure, so the rules are tested without a database."""
    if account is None or account["account_type"] != "patient":
        return "refuse", "refusing: that id is not a patient account here"
    if staff_roles:
        return "refuse", "refusing: that account holds a staff role; this script is for patient accounts only"
    if bound:
        return "refuse", "refusing: that account is already bound to a patient record; it is left alone"
    if claim_requests:
        return "refuse", "refusing: that account already has a claim request; it is left alone"
    if account["origin"] == TARGET_ORIGIN:
        return "nothing", f"nothing to do: the account's origin is already {TARGET_ORIGIN}"
    return "fix", f"the account's origin is {account['origin']}; it will be set to {TARGET_ORIGIN}"


async def run(env: dict[str, str], account_id: uuid.UUID, *, dry_run: bool, say=print) -> int:
    engine = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                row = (await conn.execute(text("select account_type, status, origin from iam.accounts where id = :a"), {"a": account_id})).first()
                account = None if row is None else {"account_type": row.account_type, "status": row.status, "origin": row.origin}
                roles = (await conn.execute(text("select count(*) from iam.staff_roles where account_id = :a"), {"a": account_id})).scalar_one()
                bound = (await conn.execute(text("select count(*) from mpi.patients where account_id = :a"), {"a": account_id})).scalar_one()
                requests = (await conn.execute(text("select count(*) from mpi.claim_requests where account_id = :a"), {"a": account_id})).scalar_one()
                action, words = decide(account, roles, bound, requests)
                if action == "refuse":
                    raise Refused(words)
                say(words)
                if action == "fix":
                    changed = (await conn.execute(text("update iam.accounts set origin = :o where id = :a and account_type = 'patient' and origin <> :o"), {"o": TARGET_ORIGIN, "a": account_id})).rowcount
                    say(f"accounts changed: {changed}")
                if dry_run:
                    await tx.rollback()
                    say("dry run: everything was rolled back, nothing was kept")
                else:
                    await tx.commit()
                    say("done: committed")
            except BaseException:
                if tx.is_active:
                    await tx.rollback()
                raise
    finally:
        await engine.dispose()
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dry-run", action="store_true", help="do everything, then roll it back")
    args = ap.parse_args()
    env = {k: v for k, v in dotenv_values(Path(args.env_file)).items() if v is not None}
    try:
        seed.refuse_unless_dev_demo_synthetic(env)
        if not (env.get("DATABASE_URL_MIGRATOR") or "").strip():
            raise Refused("refusing: DATABASE_URL_MIGRATOR is not set in the env file")
        host = seed.refuse_unless_same_project(env.get("SUPABASE_URL"), env["DATABASE_URL_MIGRATOR"], env.get("CAREFLOW_DEV_PROJECT_REF"))
        print(f"target project host: {host}")
        try:
            typed = input("Type that host exactly to confirm (anything else stops here): ")
        except EOFError:
            raise Refused("refusing: no host was typed") from None
        seed.confirm_target(typed, host)
        return await run(env, args.account_id, dry_run=args.dry_run)
    except Refused as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
