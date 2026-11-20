"""Prepare ONE SYNTHETIC patient record in the DEV clinic for a patient who signs in to the portal by e-mail and password. A person runs it by hand; nothing runs it.

    uv run python scripts/seed_dev_portal_patient.py --email <the auth user's e-mail> --clinic-id <uuid> [--env-file .env] [--dry-run]
    uv run python scripts/seed_dev_portal_patient.py --email <the same e-mail> --clinic-id <uuid> --remove

DEMO CONVENIENCE ONLY (docs/demo-runbook.md section 15). Phone-code sign-in needs a paid SMS provider, which is a new external service and is not approved. Create the auth user yourself in the Supabase dashboard
(Authentication, Users, add user, auto-confirm), set `CAREFLOW_DEMO_EMAIL_SIGNIN=on`, then run this once. It does NOT bind the account: it creates the synthetic patient record whose contact is the synthetic phone
derived from the e-mail (careflow/demo_email_signin.py), so that the patient's own claim in the portal (consent, then name and date of birth, exactly as the phone flow) finds it. The binding is made by the
portal's real claim function, with the same checks, and a front-desk person verifies it as usual if it is not an exact match. Nothing here writes an account, a consent, a claim or a role.

The record is marked as a demo placeholder the way the other seeded patients are: origin `test`, and the name starts with "DEMO" (the name is encrypted; the console shows it). The name to type at the claim step
is `DEMO Portal Patient` and the date of birth `1990-01-01`: synthetic, not secret.

What it refuses, before any connection is opened (the rules of scripts/seed_dev_demo_data.py): unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the env
names the DEV project; unless YOU TYPE THE PROJECT HOST; unless the clinic is a `test` clinic; unless the encryption keys are in the env file. It prints COUNTS ONLY: never the e-mail, a phone, a key or a password. It
does not call the Supabase Auth admin API, so it cannot tell whether the e-mail you typed is a real user: a typo makes a record nobody can claim (run it again with the right address; --remove takes the other away).
--remove ERASES the synthetic record the way erasure does (identifying fields removed, the row and its audit trail kept).
"""

import argparse
import asyncio
import hashlib
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

from careflow.crypto import DemographicVault
from careflow.demo_email_signin import phone_for_email
from careflow.mpi import PatientIngestionPipeline

Refused = seed.Refused
NAME = "DEMO Portal Patient"
DOB = "1990-01-01"


def check_email(value: str) -> str:
    email = value.strip()
    if not (3 <= len(email) <= 254) or email.count("@") != 1 or any(c.isspace() for c in email) or email.startswith("@") or email.endswith("@"):
        raise Refused("refusing: --email must be the e-mail address of the auth user you created")
    return email


def key_for(email: str, clinic: uuid.UUID) -> str:
    """The idempotency key of the record: a hash of the address, so a second run is the same record and nothing identifying is stored in the key."""
    return f"demo-portal-patient:{clinic}:{hashlib.sha256(email.strip().lower().encode('utf-8')).hexdigest()[:24]}"


async def run(env: dict[str, str], email: str, clinic: uuid.UUID, vault: DemographicVault, key_version: int, *, remove: bool, dry_run: bool, say=print) -> int:
    engine = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    key = key_for(email, clinic)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
                if row is None or row.origin != "test":
                    raise Refused("refusing: that clinic does not exist here or is not a test clinic")
                if remove:
                    gone = (
                        await conn.execute(
                            text(
                                "update mpi.patients set erased_at = clock_timestamp(), name_ct = null, dob_ct = null, phone_ct = null, name_key_version = null, dob_key_version = null, phone_key_version = null "
                                "where clinic_id = :c and idempotency_key = :k and erased_at is null"
                            ),
                            {"c": clinic, "k": key},
                        )
                    ).rowcount
                    say(f"erased {gone} synthetic portal patient record(s)")
                else:
                    before = (await conn.execute(text("select count(*) from mpi.patients where clinic_id = :c and idempotency_key = :k"), {"c": clinic, "k": key})).scalar_one()
                    pipeline = PatientIngestionPipeline(vault, key_version=key_version, registrar="direct")
                    await pipeline.ingest(conn, clinic_id=clinic, origin="test", name=NAME, dob=DOB, phone=phone_for_email(email), idempotency_key=key)
                    say(f"synthetic portal patient records: {0 if before else 1} created, {1 if before else 0} already there")
                    say("the account is bound by the patient's own claim in the portal (consent, then name and date of birth); nothing was bound here")
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
    ap.add_argument("--email", required=True)
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--remove", action="store_true", help="erase the synthetic record this script made for that address")
    ap.add_argument("--dry-run", action="store_true", help="do everything, then roll it back")
    args = ap.parse_args()
    env = {k: v for k, v in dotenv_values(Path(args.env_file)).items() if v is not None}
    try:
        email = check_email(args.email)
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
        try:
            vault = DemographicVault.from_env({k: v for k, v in env.items() if k and v and k.startswith("CAREFLOW_") and "_SECRET_V" in k})
            key_version = int(env.get("CAREFLOW_ACTIVE_KEY_VERSION") or max(vault.key_versions))
        except Exception:  # noqa: BLE001 - fixed words, never the key material
            raise Refused("refusing: the encryption keys (CAREFLOW_ENC_SECRET_V1 / CAREFLOW_HMAC_SECRET_V1) or CAREFLOW_ACTIVE_KEY_VERSION are missing or malformed in the env file") from None
        return await run(env, email, args.clinic_id, vault, key_version, remove=args.remove, dry_run=args.dry_run)
    except Refused as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
