"""Prepare ONE SYNTHETIC patient record in the DEV clinic for a patient who signs in to the portal by e-mail and password. A person runs it by hand; nothing runs it.

    uv run python scripts/seed_dev_portal_patient.py --account-id <the auth user's UID> --clinic-id <uuid> [--env-file .env] [--dry-run]
    uv run python scripts/seed_dev_portal_patient.py --account-id <the same UID> --clinic-id <uuid> --remove
    uv run python scripts/seed_dev_portal_patient.py --account-id <UID> --clinic-id <uuid> --remove-legacy [--dry-run]    (blank the OLDER records this script made before, then seed)
    uv run python scripts/seed_dev_portal_patient.py --account-id <UID> --clinic-id <uuid> --name "DEMO Portal Patient Two"    (a different demo name, nothing removed)

DEMO CONVENIENCE ONLY (docs/demo-runbook.md section 15). Phone-code sign-in needs a paid SMS provider, which is a new external service and is not approved. Create the auth user yourself in the Supabase dashboard
(Authentication, Users, add user, auto-confirm), copy its **User UID** (not the e-mail, never the password), set `CAREFLOW_DEMO_EMAIL_SIGNIN=on`, then run this once. It does NOT bind the account: it creates the
synthetic patient record whose contact is the synthetic phone derived from that account id (careflow/demo_email_signin.py), so that the patient's own claim in the portal (consent, then name and date of birth, exactly
as the phone flow) finds it. The id, not the e-mail address, is used on purpose: an address can be signed up first by someone else, an account id is assigned by the auth service and cannot be chosen. The binding is
made by the portal's real claim function, with the same checks, and a front-desk person confirms the identity and resolves it as usual. Nothing here writes an account, a consent, a claim or a role.

The record is marked as a demo placeholder the way the other seeded patients are: origin `test`, and the name starts with "DEMO" (the name is encrypted; the console shows it). The name to type at the claim step
is `DEMO Portal Patient` and the date of birth `1990-01-01`: synthetic, not secret.

What it refuses, before any connection is opened (the rules of scripts/seed_dev_demo_data.py): unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the env
names the DEV project; unless YOU TYPE THE PROJECT HOST; unless the clinic is a `test` clinic; unless the encryption keys are in the env file. It prints COUNTS ONLY: never an id of yours, a phone, a key or a password. It
does not call the Supabase Auth admin API, so it cannot tell whether the id you typed is a real user: a wrong id makes a record nobody can claim (--remove takes it away).

NO DUPLICATES. Before it creates anything it looks for a live record of the clinic with the same name and date of birth (bound or not) that this account's own seed did not make, and if there is one it STOPS
and says what exists and what to do: `--remove-legacy` (blank the older UNBOUND records this script itself made, found by the key it gives them, then seed) or `--name` (a different demo name, nothing
removed). A bound record is never touched. Two records with one name and birth date would break the front desk's search (it needs exactly one match) and make the claim's matching ambiguous.

--remove is a DEV CLEAN-UP, not the audited two-person erasure: it blanks the identifying fields of the synthetic record this script made (the row, its identity tokens and the audit trail stay, as with any erasure), and
only for a record of origin `test` that is NOT bound to a portal account. A record that was already removed cannot be seeded again for the same account: the script says so and stops (use a new auth user).
"""

import argparse
import asyncio
import hashlib
import re
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
from careflow.demo_email_signin import phone_for_account
from careflow.mpi import NAME_DOB, PatientIngestionPipeline

Refused = seed.Refused
NAME = "DEMO Portal Patient"
DOB = "1990-01-01"
KEY_PREFIX = "demo-portal-patient:"
_NAME_OK = re.compile(r"^DEMO [A-Za-z0-9 ]{1,55}$")


def check_name(value: str) -> str:
    name = " ".join(value.split())
    if not _NAME_OK.fullmatch(name):
        raise Refused("refusing: --name must start with DEMO and use only letters, digits and spaces (at most 60 characters)")
    return name


def duplicate_words(unbound: int, bound: int) -> str | None:
    """Fixed words for the duplicate rule, or None when there is no duplicate."""
    if not unbound and not bound:
        return None
    what = f"{unbound} unbound and {bound} bound"
    return (
        f"refusing: a live record with this name and date of birth already exists in the clinic ({what}) that this account's seed did not make; a second one would break the front desk's search and make the claim ambiguous. "
        "To keep the name: run again with --remove-legacy (it blanks the older UNBOUND records this script made, then seeds). To keep the old record: run again with --name \"DEMO <another name>\" and search for that name."
    )


def key_for(account_id: uuid.UUID, clinic: uuid.UUID) -> str:
    """The idempotency key of the record: a hash of the account id, so a second run is the same record and no id of the person is stored in the key."""
    return f"{KEY_PREFIX}{clinic}:{hashlib.sha256(str(account_id).lower().encode('utf-8')).hexdigest()[:24]}"


def decide(existing: dict | None, *, remove: bool) -> tuple[str, str]:
    """(action, fixed words): `create`, `exists`, `erase`, `nothing` or `refuse`. Pure, so the rules are tested without a database."""
    if remove:
        if existing is None or existing["erased"]:
            return "nothing", "nothing to remove: no live synthetic record for that account"
        if existing["origin"] != "test":
            return "refuse", "refusing: that record is not of origin test"
        if existing["bound"]:
            return "refuse", "refusing: that record is bound to a portal account; it is left alone"
        return "erase", "the synthetic record will be blanked"
    if existing is None:
        return "create", "a synthetic record will be created"
    if existing["erased"]:
        return "refuse", "refusing: a record for that account was removed earlier and cannot be seeded again; use a new auth user"
    return "exists", "the synthetic record is already there"


async def run(
    env: dict[str, str], account_id: uuid.UUID, clinic: uuid.UUID, vault: DemographicVault, key_version: int, *, remove: bool, dry_run: bool, name: str = NAME, remove_legacy: bool = False, say=print
) -> int:
    engine = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    key = key_for(account_id, clinic)
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
                if row is None or row.origin != "test":
                    raise Refused("refusing: that clinic does not exist here or is not a test clinic")
                found = (await conn.execute(text("select id, origin, erased_at, account_id from mpi.patients where clinic_id = :c and idempotency_key = :k"), {"c": clinic, "k": key})).first()
                existing = None if found is None else {"origin": found.origin, "erased": found.erased_at is not None, "bound": found.account_id is not None}
                action, words = decide(existing, remove=remove)
                if action == "refuse":
                    raise Refused(words)
                say(words)
                if action == "erase":
                    gone = (
                        await conn.execute(
                            text(
                                "update mpi.patients set erased_at = clock_timestamp(), name_ct = null, dob_ct = null, phone_ct = null, name_key_version = null, dob_key_version = null, phone_key_version = null "
                                "where id = :i and origin = 'test' and account_id is null and erased_at is null"
                            ),
                            {"i": found.id},
                        )
                    ).rowcount
                    say(f"records blanked: {gone}")
                elif action == "create":
                    if remove_legacy:
                        legacy = (
                            await conn.execute(
                                text(
                                    "update mpi.patients set erased_at = clock_timestamp(), name_ct = null, dob_ct = null, phone_ct = null, name_key_version = null, dob_key_version = null, phone_key_version = null "
                                    "where clinic_id = :c and origin = 'test' and account_id is null and erased_at is null and idempotency_key like :p and idempotency_key <> :k"
                                ),
                                {"c": clinic, "p": f"{KEY_PREFIX}{clinic}:%", "k": key},
                            )
                        ).rowcount
                        say(f"older unbound records this script made, blanked: {legacy}")
                    token = vault.identity_token(NAME_DOB, f"{name} {DOB}", key_version=key_version)
                    rows = (
                        await conn.execute(
                            text(
                                "select (p.account_id is not null) as bound, count(*) as n from mpi.patients p join mpi.identity_tokens t on t.patient_id = p.id and t.clinic_id = p.clinic_id "
                                "where p.clinic_id = :c and p.erased_at is null and t.kind = 'name_dob' and t.key_version = :kv and t.token = :t and p.idempotency_key <> :k group by 1"
                            ),
                            {"c": clinic, "kv": key_version, "t": token, "k": key},
                        )
                    ).all()
                    unbound, bound = sum(r.n for r in rows if not r.bound), sum(r.n for r in rows if r.bound)
                    words = duplicate_words(unbound, bound)
                    if words:
                        raise Refused(words)
                    pipeline = PatientIngestionPipeline(vault, key_version=key_version, registrar="direct")
                    result = await pipeline.ingest(conn, clinic_id=clinic, origin="test", name=name, dob=DOB, phone=phone_for_account(account_id), idempotency_key=key)
                    if result.outcome != "created":
                        raise Refused("refusing: the registration did not create a clean record (it was matched or flagged as a possible duplicate); nothing was kept")
                    say("records created: 1")
                if action in ("create", "exists"):
                    say("the account is bound by the patient's own claim in the portal (consent, then name and date of birth), after front desk confirms the identity; nothing was bound here")
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
    ap.add_argument("--account-id", required=True, type=uuid.UUID, help="the auth user's UID (Supabase dashboard, Authentication, Users)")
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--name", default=NAME, help="the demo name of the record (must start with DEMO); the default is the usual one")
    ap.add_argument("--remove-legacy", action="store_true", help="before seeding, blank the OLDER unbound records this script made (a different key), so the name stays unique")
    ap.add_argument("--remove", action="store_true", help="blank the synthetic record this script made for that account (dev clean-up, not the audited erasure)")
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
        try:
            vault = DemographicVault.from_env({k: v for k, v in env.items() if k and v and k.startswith("CAREFLOW_") and "_SECRET_V" in k})
            key_version = int(env.get("CAREFLOW_ACTIVE_KEY_VERSION") or max(vault.key_versions))
        except Exception:  # noqa: BLE001 - fixed words, never the key material
            raise Refused("refusing: the encryption keys (CAREFLOW_ENC_SECRET_V1 / CAREFLOW_HMAC_SECRET_V1) or CAREFLOW_ACTIVE_KEY_VERSION are missing or malformed in the env file") from None
        return await run(env, args.account_id, args.clinic_id, vault, key_version, remove=args.remove, dry_run=args.dry_run, name=check_name(args.name), remove_legacy=args.remove_legacy)
    except Refused as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
