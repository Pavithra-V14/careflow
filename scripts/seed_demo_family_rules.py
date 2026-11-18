"""Enter CLEARLY LABELLED PLACEHOLDER family-link rules into the DEV database, so the family-link flow can be walked in a demo. A person runs it by hand; nothing runs it.

    uv run python scripts/seed_demo_family_rules.py --clinic-id <uuid> [--env-file .env] [--dry-run]
    uv run python scripts/seed_demo_family_rules.py --clinic-id <uuid> --remove      (takes the demo rows out again; only rows with this label)

THE NUMBERS ARE NOT COUNSEL'S. The real age limits and the consent rule are counsel's decision (docs/approval-pack/counsel-cover-note.md); in every real deployment `iam.family_link_rules` stays EMPTY and family links stay
denied until counsel's values are entered. The two rows written here carry the label `DEMO PLACEHOLDER, not counsel's values` in their `provenance` column and use deliberately odd numbers (a guardian link for a
dependant aged 0 to 9, a carer link for a dependant aged 30 to 130, the linked person's consent required for both) so nobody can mistake them for a legal threshold. Nothing is written to a migration or to any repository
default; the rows exist only in the dev database you point it at.

What it refuses, before any connection is opened (the same rules as scripts/seed_dev_demo_data.py): unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the
env names the DEV project; unless YOU TYPE THE PROJECT HOST; unless the clinic is a `test` clinic; and it refuses to touch a clinic that already holds a rule row WITHOUT the demo label (a real rule is never
overwritten or removed). It prints fixed words and the host; never a key.
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
LABEL = "DEMO PLACEHOLDER, not counsel's values"
# link type -> (youngest, oldest, linked person's consent required). Deliberately odd demo numbers.
DEMO_RULES = {"guardian_of_minor": (0, 9, True), "carer_of_adult": (30, 130, True)}


async def run(env: dict[str, str], clinic: uuid.UUID, *, remove: bool, dry_run: bool, say=print) -> int:
    engine = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
                if row is None or row.origin != "test":
                    raise Refused("refusing: that clinic does not exist here or is not a test clinic")
                foreign = (await conn.execute(text("select count(*) from iam.family_link_rules where clinic_id = :c and provenance is distinct from :l"), {"c": clinic, "l": LABEL})).scalar_one()
                if foreign:
                    raise Refused("refusing: this clinic already holds a family-link rule that is not a demo placeholder; a real rule is never touched")
                if remove:
                    gone = (await conn.execute(text("delete from iam.family_link_rules where clinic_id = :c and provenance = :l"), {"c": clinic, "l": LABEL})).rowcount
                    say(f"removed {gone} demo placeholder rule(s)")
                else:
                    for link_type, (low, high, consent) in DEMO_RULES.items():
                        await conn.execute(
                            text("insert into iam.family_link_rules (clinic_id, link_type, min_dependant_age_years, max_dependant_age_years, requires_dependant_consent, provenance) values (:c, :t, :lo, :hi, :k, :l) on conflict (clinic_id, link_type) do nothing"),
                            {"c": clinic, "t": link_type, "lo": low, "hi": high, "k": consent, "l": LABEL},
                        )
                    say(f"entered the demo placeholder rules for this clinic ({', '.join(DEMO_RULES)}); label: {LABEL}")
                    say("these numbers are NOT counsel's: use them only to walk the demo")
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
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--remove", action="store_true", help="take the demo placeholder rules out again (only rows with the demo label)")
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
        return await run(env, args.clinic_id, remove=args.remove, dry_run=args.dry_run)
    except Refused as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
