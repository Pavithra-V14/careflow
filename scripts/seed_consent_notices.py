"""Publish consent notices from the required setting CAREFLOW_CONSENT_NOTICES. NOT run by anything; a person (the owner) runs it by hand, with the migrator connection.

    python scripts/seed_consent_notices.py [--env-file .env] [--origin live|test] [--dry-run]

Why a script and not a route (owner decision of 2026-11-08, migration 0053): consent notices are PLATFORM-WIDE legal text. No application role, and so no clinic admin, can publish one; the application can only
read them. This script connects as the migrator, so the act is a deliberate, owner-run, audited change.

What it does: reads CAREFLOW_CONSENT_NOTICES (counsel owns the wording; see careflow/consent_settings.py), compares with what is published, and for each purpose publishes the NEXT version only. The same version
with the same text is left alone; the same version with DIFFERENT text, or a version that skips ahead, stops the script before anything is written. Each publication writes an audit row (key
`sys:consent-notice:<purpose>:<version>`). It prints purposes and versions, never the text.
"""

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from careflow.consent_settings import (
    ConsentSettingsError,
    parse,
    plan_publication,
)


async def run(env: dict, origin: str, dry_run: bool) -> int:
    from careflow import demo_phase4

    effective = demo_phase4.effective_environ(env, (env.get("CAREFLOW_PROFILE") or "").strip().lower())  # the labelled DEMO placeholder under the demo pack, the environment file otherwise
    try:
        wanted = parse(effective.get("CAREFLOW_CONSENT_NOTICES"))
    except ConsentSettingsError as exc:
        print(f"refusing: {exc}")
        return 2
    if wanted is None:
        print("refusing: CAREFLOW_CONSENT_NOTICES is not set; there is nothing to publish")
        return 2
    dsn = (env.get("DATABASE_URL_MIGRATOR") or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    if not dsn:
        print("refusing: DATABASE_URL_MIGRATOR is not set")
        return 2
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=20)
    try:
        existing: dict[str, list[tuple[int, str]]] = {}
        for r in await conn.fetch("select purpose, version, body from iam.consent_notices"):
            existing.setdefault(r["purpose"], []).append((r["version"], r["body"]))
        actions = plan_publication(existing, wanted)
        for action, purpose, version in actions:
            print(f"{purpose} v{version}: {action}")
        if any(a in ("conflict", "version_gap") for a, _, _ in actions):
            print("stopping: nothing was written")
            return 1
        if dry_run:
            print("dry run: nothing was written")
            return 0
        async with conn.transaction():
            for action, purpose, version in actions:
                if action != "publish":
                    continue
                await conn.execute("insert into iam.consent_notices (purpose, version, body) values ($1, $2, $3)", purpose, version, wanted[purpose].body)
                await conn.execute(
                    "insert into audit.log (origin, clinic_id, actor_id, actor_role, action, resource_type, resource_id, details, idempotency_key) "
                    "values ($1, null, null, 'owner_script', 'consent.notice_publish', 'consent_notice', $2, jsonb_build_object('purpose', $3::text, 'version', $4::int), $5)",
                    origin, f"{purpose}:{version}", purpose, version, f"sys:consent-notice:{purpose}:{version}",
                )
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("--origin", choices=("live", "test"), default="live")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    env = {**dotenv_values(args.env_file)}
    return asyncio.run(run(env, args.origin, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
