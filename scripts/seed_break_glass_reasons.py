"""Insert the break-glass reason codes of ONE clinic from the setting CAREFLOW_BREAK_GLASS_REASONS (comma separated lowercase words). NOT run by anything; a person (the owner) runs it by hand, with the migrator connection.

    python scripts/seed_break_glass_reasons.py --clinic-id <uuid> [--env-file .env] [--dry-run]

Why a script (migration 0058): the clinic's list of reason codes is owner-kept. No application role can read or write the table, so the application cannot invent a code. Under the DEMO pack
(CAREFLOW_PROFILE=demo, CAREFLOW_DATA_POLICY=synthetic_only, CAREFLOW_DEMO_PHASE4=1) the setting comes from the labelled demo placeholder (`demo_reason`); in any other profile the setting must be
in the environment file. It only ADDS codes (a code already listed is left alone) and prints the codes, never anything about a patient.
"""

import argparse
import asyncio
import re
import sys
import uuid
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow import demo_phase4

CODE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


async def run(env: dict, clinic_id: uuid.UUID, dry_run: bool) -> int:
    profile = (env.get("CAREFLOW_PROFILE") or "").strip().lower()
    effective = demo_phase4.effective_environ(env, profile)
    raw = effective.get("CAREFLOW_BREAK_GLASS_REASONS")
    codes = [c.strip() for c in (raw or "").split(",") if c.strip()]
    if not codes or any(not CODE.match(c) for c in codes):
        print("refusing: CAREFLOW_BREAK_GLASS_REASONS is not set, or a code is not a lowercase word (letters, digits, underscore)")
        return 2
    dsn = (env.get("DATABASE_URL_MIGRATOR") or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    if not dsn:
        print("refusing: DATABASE_URL_MIGRATOR is not set")
        return 2
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        if not await conn.fetchval("select 1 from iam.clinics where id = $1", clinic_id):
            print("refusing: no clinic with that id")
            return 2
        have = {r["code"] for r in await conn.fetch("select code from iam.break_glass_reasons where clinic_id = $1", clinic_id)}
        for code in codes:
            print(f"{code}: {'already listed' if code in have else 'add'}")
        if dry_run:
            print("dry run: nothing was written")
            return 0
        async with conn.transaction():
            for code in codes:
                if code not in have:
                    await conn.execute("insert into iam.break_glass_reasons (clinic_id, code) values ($1, $2)", clinic_id, code)
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clinic-id", required=True, type=uuid.UUID)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return asyncio.run(run({**dotenv_values(args.env_file)}, args.clinic_id, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
