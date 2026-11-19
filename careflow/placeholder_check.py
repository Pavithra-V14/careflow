"""The startup check that no demo placeholder sits under a deployment that is not the synthetic demo (owner instruction 2026-11-19).

Demo seed scripts (scripts/seed_demo_placeholders.py, seed_demo_family_rules.py, seed_consent_notices.py with the demo pack) enter owner-managed rows labelled "DEMO PLACEHOLDER, not approved by <owner>". The database
function `ops.demo_placeholder_rows()` (migration 0096) counts them per source, counts only. Outside profile=demo with data_policy=synthetic_only the application REFUSES TO START while any count is above zero, and
also when the count cannot be read: unknown is not clean (CLAUDE.md rule 3).
"""

from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

LABEL_PREFIX: Final = "DEMO PLACEHOLDER"
# Every source `ops.demo_placeholder_rows()` must report (migration 0096). A missing source is NOT a clean source: the check refuses if the answer is not complete.
EXPECTED_SOURCES: Final = (
    "iam.break_glass_reasons", "iam.break_glass_settings", "iam.public_grievance_limits", "docs.canary_settings", "ops.spend_limits", "sched.patient_bookable_visit_types", "iam.family_link_rules",
    "iam.consent_notices",
)


class PlaceholdersPresent(RuntimeError):
    """A placeholder row exists, or could not be ruled out, in a deployment that is not the synthetic demo. Fixed words: sources and counts only, never a row."""


async def check(engine: AsyncEngine, *, demo_allowed: bool) -> None:
    if demo_allowed:
        return
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(text("select o_source, o_rows from ops.demo_placeholder_rows()"))).all()
    except Exception as exc:  # noqa: BLE001 - any failure to read means "not proven clean"; the class name only
        raise PlaceholdersPresent(f"cannot rule out demo placeholder rows ({type(exc).__name__}): refusing to start outside profile=demo with data_policy=synthetic_only") from None
    reported = {r.o_source for r in rows}
    if not set(EXPECTED_SOURCES) <= reported:
        raise PlaceholdersPresent("the placeholder count is incomplete (a source is missing from the answer): refusing to start outside profile=demo with data_policy=synthetic_only")
    present = {r.o_source: int(r.o_rows) for r in rows if int(r.o_rows) > 0}
    if present:
        listing = ", ".join(f"{source}: {count}" for source, count in sorted(present.items()))
        raise PlaceholdersPresent(f"demo placeholder rows exist ({listing}): refusing to start outside profile=demo with data_policy=synthetic_only; remove them with the seed scripts' --remove")
