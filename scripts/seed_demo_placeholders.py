"""Enter CLEARLY LABELLED DEMO PLACEHOLDERS for the owner-managed tables a screen needs, in the DEV database, so every non-clinical screen can be walked in a demo. A person runs it by hand; nothing runs it.

    uv run python scripts/seed_demo_placeholders.py --clinic-id <uuid> [--env-file .env] [--dry-run]
    uv run python scripts/seed_demo_placeholders.py --clinic-id <uuid> --remove        (takes out only rows that carry the demo label)

NOTHING HERE IS APPROVED. Every row carries `provenance` = "DEMO PLACEHOLDER, not approved by the clinic owner" (migration 0096: the owner-managed tables have no other note field and the application cannot read them).
The values come from the AI-prepared proposals in docs/approval-pack/clinic-proposed-values.md where one exists (the four break-glass reasons, the 60-minute window and the 3 re-opens); where the proposals
give no number (the public-grievance hourly cap, the canary cap, the per-call spend cap, how many future bookings a patient may hold) the number is an INVENTED DEMO NUMBER and the label says so. No clinical content is
entered: no card, no red-flag rule, no threshold, no wording a patient reads. Nothing is written to a migration or a repository default.

Entered, per clinic: iam.break_glass_reasons and iam.break_glass_settings, iam.public_grievance_limits, docs.canary_settings, ops.spend_limits, sched.patient_bookable_visit_types (every visit type of the clinic).
Not entered here: family-link rules (scripts/seed_demo_family_rules.py), consent notices (scripts/seed_consent_notices.py with the demo pack), and every setting that lives in the environment (the demo pack and
docs/demo-runbook.md). See docs/demo-placeholders.md for what stays blocked, by owner.

What it refuses, before any connection is opened (the rules of scripts/seed_dev_demo_data.py): unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the env names
the DEV project; unless YOU TYPE THE PROJECT HOST; unless the clinic is a `test` clinic. It is idempotent (a second run changes nothing), never touches a row without the label, and prints COUNTS ONLY, never a key.
The application refuses to start outside profile=demo with synthetic data while any labelled row exists (careflow/placeholder_check.py).
"""

import argparse
import asyncio
import sys
import uuid
from decimal import Decimal
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
LABEL = "DEMO PLACEHOLDER, not approved by the clinic owner"
INVENTED = "DEMO PLACEHOLDER, not approved by the clinic owner (invented demo number)"

# From docs/approval-pack/clinic-proposed-values.md section 1 (AI-prepared proposals, not approved).
BREAK_GLASS_REASONS = ("emergency_care", "covering_colleague", "urgent_question", "other")
BREAK_GLASS_MINUTES = 60
BREAK_GLASS_REOPENS = 3
# INVENTED demo numbers (the proposals give none).
PUBLIC_GRIEVANCE_PER_HOUR = 20
CANARY_PER_DAY = 5
SPEND_USD_PER_CALL = Decimal("0.05")
BOOKABLE_MAX_FUTURE = 2

TABLES = (
    "iam.break_glass_reasons", "iam.break_glass_settings", "iam.public_grievance_limits", "docs.canary_settings", "ops.spend_limits", "sched.patient_bookable_visit_types",
)


async def run(env: dict[str, str], clinic: uuid.UUID, *, remove: bool, dry_run: bool, say=print) -> int:
    engine = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    counts: dict[str, int] = {}
    try:
        async with engine.connect() as conn:
            tx = await conn.begin()
            try:
                row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
                if row is None or row.origin != "test":
                    raise Refused("refusing: that clinic does not exist here or is not a test clinic")
                if remove:
                    for table in TABLES:
                        gone = (await conn.execute(text(f"delete from {table} where clinic_id = :c and provenance like 'DEMO PLACEHOLDER%'"), {"c": clinic})).rowcount
                        counts[table] = gone
                else:
                    p = {"c": clinic, "l": LABEL, "i": INVENTED}
                    for code in BREAK_GLASS_REASONS:
                        counts["iam.break_glass_reasons"] = counts.get("iam.break_glass_reasons", 0) + (await conn.execute(
                            text("insert into iam.break_glass_reasons (clinic_id, code, provenance) values (:c, :code, :l) on conflict (clinic_id, code) do nothing"), {**p, "code": code})).rowcount
                    steps = (
                        ("iam.break_glass_settings", "insert into iam.break_glass_settings (clinic_id, max_minutes, max_reopens, provenance) values (:c, :a, :b, :l) on conflict (clinic_id) do nothing",
                         {"a": BREAK_GLASS_MINUTES, "b": BREAK_GLASS_REOPENS}),
                        ("iam.public_grievance_limits", "insert into iam.public_grievance_limits (clinic_id, max_per_hour, provenance) values (:c, :a, :i) on conflict (clinic_id) do nothing", {"a": PUBLIC_GRIEVANCE_PER_HOUR}),
                        ("docs.canary_settings", "insert into docs.canary_settings (clinic_id, max_per_day, provenance) values (:c, :a, :i) on conflict (clinic_id) do nothing", {"a": CANARY_PER_DAY}),
                        ("ops.spend_limits", "insert into ops.spend_limits (clinic_id, max_usd_per_call, provenance) values (:c, :a, :i) on conflict (clinic_id) do nothing", {"a": SPEND_USD_PER_CALL}),
                        (
                            "sched.patient_bookable_visit_types",
                            (
                                "insert into sched.patient_bookable_visit_types (clinic_id, visit_type_id, max_future_bookings, provenance) select clinic_id, id, :a, :i from sched.visit_types "
                                "where clinic_id = :c on conflict (clinic_id, visit_type_id) do nothing"
                            ),
                            {"a": BOOKABLE_MAX_FUTURE},
                        ),
                    )
                    for table, sql, extra in steps:
                        counts[table] = (await conn.execute(text(sql), {**p, **extra})).rowcount
                say(("removed" if remove else "entered") + " (rows per table; a row already there is left alone and not counted):")
                for table in TABLES:
                    say(f"  {table}: {counts.get(table, 0)}")
                say(f"every row is labelled: {LABEL!r}; none is approved")
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
    ap.add_argument("--remove", action="store_true", help="take out only the rows that carry the demo label")
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
