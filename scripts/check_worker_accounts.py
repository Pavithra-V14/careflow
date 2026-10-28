"""Fail if any account holds `queue_worker` together with another role (docs/runbooks/staff-accounts.md; migration 0024). Read-only; prints opaque account ids only.

    python scripts/check_worker_accounts.py [--origin live]      # DATABASE_URL_MIGRATOR from the environment or .env

The CI migrate job runs it with `--origin live`: the CI project holds tagged origin='test' fixtures that are not a deployment, and a real violation is one among live accounts.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from careflow.ops_checks import accounts_holding_worker_and_another_role


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origin", default=None, choices=["live", "test", "eval", "canary"], help="check only grants of this origin (default: all)")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL_MIGRATOR") or dotenv_values(".env").get("DATABASE_URL_MIGRATOR") or ""
    if not url:
        print("ERROR: DATABASE_URL_MIGRATOR is not set")
        return 2
    conn = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://", 1), timeout=20, statement_cache_size=0)
    try:
        bad = await accounts_holding_worker_and_another_role(conn.fetch, args.origin)
    finally:
        await conn.close()
    if bad:
        print(f"FAIL: {len(bad)} account(s) hold queue_worker together with another role: {', '.join(bad)}")
        return 1
    print(f"OK: no account holds queue_worker together with another role (origin: {args.origin or 'all'})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
