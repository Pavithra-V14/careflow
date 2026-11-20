"""Why does a patient see no open times? READ-ONLY, in the DEV database. A person runs it by hand; nothing runs it.

    uv run python scripts/diagnose_patient_slots.py --clinic-id <uuid> [--account-id <patient auth user id>] [--env-file .env]

It counts, with exactly the conditions of `sched.patient_open_slots` (migration 0083), how many of the clinic's slots each condition hides from that patient, and how many pass every condition. It prints COUNTS and fixed words only: no ids, no names,
no times other than the earliest and latest start DATE of the slots that are open and in the future. A slot can fail several conditions, so the reason counts overlap and do not add up to the total.

What it refuses, before any connection is opened (the rules of scripts/seed_dev_demo_data.py): unless the env file says CAREFLOW_ENV=dev, CAREFLOW_PROFILE=demo and CAREFLOW_DATA_POLICY=synthetic_only; unless the env names the DEV project;
unless YOU TYPE THE PROJECT HOST; unless the clinic is a `test` clinic. It writes nothing (the transaction is read-only and rolled back).

Without --account-id the patient's own origin cannot be known, so the origin condition is reported as slot counts per origin word instead. With it, the script also says whether that account is an active patient account bound to a live record of the clinic
(the list refuses anyone else) and uses that record's origin.
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

# One row of counts. Every condition is the one in sched.patient_open_slots (migration 0083), spelled the same way; a test reads the migration and checks each is there.
COUNTS_SQL = """
select count(*) as total,
       count(*) filter (where s.status <> 'open') as not_open,
       count(*) filter (where not (s.starts_at > clock_timestamp())) as in_the_past,
       count(*) filter (where b.visit_type_id is null) as type_not_listed_bookable,
       count(*) filter (where vt.id is null or not vt.active) as type_not_active,
       count(*) filter (where l.id is null or not l.active) as location_not_active,
       count(*) filter (where :origin is not null and s.origin <> :origin) as origin_differs,
       count(*) filter (where s.status = 'open' and s.starts_at > clock_timestamp()) as open_and_future,
       count(*) filter (where s.status = 'open' and s.starts_at > clock_timestamp() and (b.visit_type_id is null or vt.id is null or not vt.active)) as open_future_type_blocked,
       count(*) filter (where s.status = 'open' and s.starts_at > clock_timestamp() and (l.id is null or not l.active)) as open_future_location_blocked,
       count(*) filter (where s.status = 'open' and s.starts_at > clock_timestamp() and :origin is not null and s.origin <> :origin) as open_future_origin_differs,
       count(*) filter (
           where s.status = 'open' and s.starts_at > clock_timestamp() and b.visit_type_id is not null and vt.active and l.active and (:origin is null or s.origin = :origin)
       ) as passes_every_condition,
       min(s.starts_at::date) filter (where s.status = 'open' and s.starts_at > clock_timestamp()) as earliest_open_date,
       max(s.starts_at::date) filter (where s.status = 'open' and s.starts_at > clock_timestamp()) as latest_open_date
  from sched.slots s
  left join sched.patient_bookable_visit_types b on b.clinic_id = s.clinic_id and b.visit_type_id = s.visit_type_id
  left join sched.visit_types vt on vt.id = s.visit_type_id and vt.clinic_id = s.clinic_id
  left join sched.locations l on l.id = s.location_id and l.clinic_id = s.clinic_id
 where s.clinic_id = :clinic
"""
PATIENT_SQL = (
    "select pt.origin from mpi.patients pt join iam.accounts a on a.id = pt.account_id and a.account_type = 'patient' and a.status = 'active' "
    "where pt.account_id = :account and pt.clinic_id = :clinic and pt.erased_at is null"
)
BOOKABLE_SQL = "select count(*) from sched.patient_bookable_visit_types b join sched.visit_types vt on vt.id = b.visit_type_id and vt.clinic_id = b.clinic_id and vt.active where b.clinic_id = :clinic"
ORIGINS_SQL = "select s.origin, count(*) from sched.slots s where s.clinic_id = :clinic group by s.origin order by s.origin"

LABELS = (
    ("total", "slots of this clinic"),
    ("not_open", "hidden: status is not open (held, booked, cancelled)"),
    ("in_the_past", "hidden: starts in the past"),
    ("type_not_listed_bookable", "hidden: visit type is not in the owner's bookable list"),
    ("type_not_active", "hidden: visit type is not active"),
    ("location_not_active", "hidden: location is not active"),
    ("origin_differs", "hidden: origin differs from the patient's record"),
    ("open_and_future", "open and in the future (before the type, location and origin checks)"),
    ("passes_every_condition", "PASS EVERY CONDITION: what the patient list would show"),
)


def report(counts: dict, *, bookable_types: int, patient: str, origins: list[tuple[str, int]], with_origin: bool) -> list[str]:
    """The printed lines: counts and fixed words only. Pure, so a test can check there is nothing else in it."""
    lines = [f"patient account: {patient}", f"visit types the owner lists as bookable (and active): {bookable_types}"]
    for key, label in LABELS:
        if key == "origin_differs" and not with_origin:
            lines.append("  origin check: not applied (no --account-id); slots per origin word: " + (", ".join(f"{o} {n}" for o, n in origins) or "none"))
            continue
        lines.append(f"  {counts[key]:>4}  {label}")
    first, last = counts["earliest_open_date"], counts["latest_open_date"]
    lines.append(f"open future slots start between {first} and {last}" if first else "there is no open future slot at all (earliest and latest date: none)")
    lines.append(verdict(counts, bookable_types=bookable_types, with_origin=with_origin, patient=patient))
    return lines


def verdict(counts: dict, *, bookable_types: int, with_origin: bool, patient: str) -> str:
    """One fixed sentence naming the first reason that explains an empty list. It states a reason the counts show; it never guesses."""
    if patient.startswith("NOT"):
        return "verdict: the patient list refuses this account before it looks at slots (it must be an active patient account bound to a live record of the clinic)"
    if counts["passes_every_condition"] > 0:
        return "verdict: the patient list is not empty: it would show slots"
    if counts["total"] == 0:
        return "verdict: the clinic has no slots at all"
    if bookable_types == 0:
        return "verdict: no active visit type is in the bookable list (the patient screen says booking is not set up)"
    if counts["open_and_future"] == 0:
        return "verdict: no slot is both open and in the future: every slot is past, held, booked or cancelled"
    if counts["open_future_type_blocked"] == counts["open_and_future"]:
        return "verdict: every open future slot is of a visit type that is not in the bookable list or not active"
    if counts["open_future_location_blocked"] == counts["open_and_future"]:
        return "verdict: every open future slot is at a location that is not active"
    if with_origin and counts["open_future_origin_differs"] == counts["open_and_future"]:
        return "verdict: every open future slot is of another origin than the patient's record"
    return "verdict: no single reason hides every open slot; read the counts above (the reasons overlap)"


async def run(env: dict[str, str], clinic: uuid.UUID, account: uuid.UUID | None, say=print) -> int:
    engine = create_async_engine(make_url(env["DATABASE_URL_MIGRATOR"].replace("postgresql://", "postgresql+asyncpg://", 1)), connect_args={"statement_cache_size": 0})
    try:
        async with engine.connect() as conn:
            await conn.execute(text("set transaction read only"))  # the first statement of the transaction
            try:
                row = (await conn.execute(text("select origin from iam.clinics where id = :c"), {"c": clinic})).first()
                if row is None or row.origin != "test":
                    raise Refused("refusing: that clinic does not exist here or is not a test clinic")
                origin, patient = None, "not given (--account-id)"
                if account is not None:
                    found = (await conn.execute(text(PATIENT_SQL), {"account": account, "clinic": clinic})).first()
                    if found is None:
                        patient = "NOT an active patient account bound to a live record of this clinic"
                    else:
                        origin, patient = found.origin, f"active, bound to a live record (origin word: {found.origin})"
                counts = dict((await conn.execute(text(COUNTS_SQL), {"clinic": clinic, "origin": origin})).mappings().one())
                bookable = (await conn.execute(text(BOOKABLE_SQL), {"clinic": clinic})).scalar_one()
                origins = [(r[0], r[1]) for r in (await conn.execute(text(ORIGINS_SQL), {"clinic": clinic})).all()]
                for line in report(counts, bookable_types=bookable, patient=patient, origins=origins, with_origin=origin is not None):
                    say(line)
            finally:
                await conn.rollback()
    finally:
        await engine.dispose()
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--account-id", type=uuid.UUID, help="the patient's account (auth user) id; optional")
    ap.add_argument("--env-file", default=".env")
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
        return await run(env, args.clinic_id, args.account_id)
    except Refused as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
