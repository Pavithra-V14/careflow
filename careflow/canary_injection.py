"""Canary injection into the Front Door (BUILD-SPEC.md 12.4), DEMO PROFILE ONLY (owner decision 2026-11-06 item 2).

A worker-tick sweep: for each clinic the worker serves, it looks at how many LIVE review tasks appeared in the last minute and, for each, draws once against the configured canary rate
(careflow/canary.py, a required setting, at most 5%); if any draw succeeds it registers ONE canary document (docs.register_canary_document, migration 0042) and enqueues a normal Front Door run for it
with origin `canary`. The run then goes through the same rules-first graph as a real letter and pauses at review; approving it raises the silent admin alert. At most one canary per clinic per tick, and
the document key carries the minute bucket, so a retried tick does not make two.

NEVER in the clinical profile: the sweep is only built when profile=demo, data_policy=synthetic_only and the demo switch is on (careflow/demo_phase3.py), AND a rate is configured (an unset rate
injects nothing). The canary letter is fabricated text made up by the demo text source; no real patient, file or provider is involved.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from random import random
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.canary import should_inject
from careflow.documents import document_id_for
from careflow.front_door_runner import TASK_TYPE as FRONT_DOOR_TASK
from careflow.queue import PostgresQueue

log = logging.getLogger("careflow.canary_injection")
WINDOW_SECONDS: Final = 60


async def canary_sweep(conn: AsyncConnection, rate: float | None, *, draw: Callable[[], float] = random, now: Callable[[], datetime] = lambda: datetime.now(UTC), origin: str = "live") -> int:
    """One pass. Returns how many canaries were enqueued. No rate (a required setting): returns 0 without reading anything. The daily cap lives in the database (docs.canary_settings, migration 0047); a clinic with no cap row gets no canary."""
    if rate is None:
        return 0
    clinics = [r[0] for r in (await conn.execute(text("select distinct r.clinic_id from iam.staff_roles r where r.role = 'queue_worker' and ops.clinic_origin(r.clinic_id) = :o and iam.has_clinic_role(r.clinic_id, array['queue_worker']) order by r.clinic_id"), {"o": origin})).all()]
    made = 0
    for clinic in clinics:
        try:
            n = (
                await conn.execute(
                    text("select count(*) from agents.review_tasks where clinic_id = :c and origin = 'live' and created_at >= clock_timestamp() - make_interval(secs => :w)"),
                    {"c": clinic, "w": WINDOW_SECONDS},
                )
            ).scalar_one()
            if not any(should_inject(rate, draw) for _ in range(int(n))):
                continue
            bucket = now().strftime("%Y%m%d%H%M")
            document_id = document_id_for(clinic, f"canary-bucket:{bucket}")
            async with conn.begin_nested():
                await conn.execute(text("select docs.register_canary_document(:c, :d, :k)"), {"c": clinic, "d": document_id, "k": f"canary:{document_id}"})
                await PostgresQueue().enqueue(conn, clinic, "canary", FRONT_DOOR_TASK, {"document_id": str(document_id)}, f"canary-run:{document_id}")
            made += 1
        except Exception as exc:  # noqa: BLE001 - one clinic failing must not stop the others, and a canary is never worth a failed tick
            log.error("canary injection failed for one clinic: %s", type(exc).__name__)
    return made

