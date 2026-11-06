"""The worker-tick sweeps for the care-gap overdue alert and the after-the-fact sampler (migration 0048; BUILD-SPEC.md 12.6, 24). Both run as the queue worker at the end of a tick, per clinic the worker serves,
and one clinic failing never stops the others. Neither reads a table the worker has no policy on: the overdue check uses the narrow function `ops.care_gap_rules_for_worker`, and the sampler reads only the
decisions the worker already could and writes only a reference.

THE SAMPLER'S CANDIDATES. The spec samples AUTO-APPROVED cases (12.6). No auto-approval path exists (the autonomy mode is `shadow` and no code approves on its own), so the default candidate source returns
none and the sweep writes nothing: the mechanism is built and tested with a stand-in source, and it is NOT claimed to be sampling anything real until an auto-approval path exists. The rate is the required
setting CAREFLOW_AUTO_REVIEW_SAMPLE_RATE (unset means no sample, and the caller says the automatic path is unmeasured).
"""

import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow import producers_sql
from careflow.care_gap_sql import RecurrenceSource
from careflow.learning_labels import sample_for_review
from careflow.phase4_sql import SqlAlertSink
from careflow.watchdog import Alert

log = logging.getLogger("careflow.producer_sweeps")

CandidateSource = Callable[[AsyncConnection, uuid.UUID, str], Awaitable[list[uuid.UUID]]]


async def no_auto_approved_decisions(conn: AsyncConnection, clinic_id: uuid.UUID, origin: str) -> list[uuid.UUID]:
    """There is no auto-approval path yet, so there is nothing to sample."""
    return []


async def _clinics(conn: AsyncConnection, origin: str) -> list[uuid.UUID]:
    return [r[0] for r in (await conn.execute(text("select id from iam.clinics where origin = :o and iam.has_clinic_role(id, array['queue_worker']) order by id"), {"o": origin})).all()]


async def care_gap_overdue_sweep(conn: AsyncConnection, alert_after_days: int | None, *, origin: str = "live", now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> int:
    """Raise one `task_overdue` alert per overdue recurrence older than the limit (a required setting: unset raises nothing). The key is per item, so a second pass raises nothing new. Returns alerts attempted."""
    if alert_after_days is None or alert_after_days < 1:
        return 0
    raised = 0
    sink = SqlAlertSink(conn)
    for clinic in await _clinics(conn, origin):
        try:
            async with conn.begin_nested():
                rows = await producers_sql.worker_overdue_rows(conn, clinic)
                source = RecurrenceSource(conn, clinic, origin)
                for r in rows:
                    item = source._item(SimpleNamespace(id=r.episode_rule_id, last_done_on=r.last_done_on, interval_days=r.interval_days, annual_month=r.annual_month, overdue_after_days=r.overdue_after_days, today=r.today))
                    if item is None or item.opened_at is None or (now().date() - item.opened_at.date()).days < alert_after_days:
                        continue
                    await sink.raise_alert(Alert(str(clinic), origin, "task_overdue", 1, item.item_id, f"care-gap-overdue:{item.source}:{item.item_id}"))
                    raised += 1
        except Exception as exc:  # noqa: BLE001 - one clinic failing must not stop the others
            log.error("care-gap overdue sweep failed for one clinic: %s", type(exc).__name__)
    return raised


async def sampler_sweep(conn: AsyncConnection, rate: float | None, *, source: CandidateSource = no_auto_approved_decisions, draw: Callable[[], float] = random.random, origin: str = "live") -> int:
    """Write a review sample for about `rate` of the candidate decisions. No rate: nothing. A decision already sampled is skipped by the key. Returns new samples."""
    if rate is None:
        return 0
    made = 0
    for clinic in await _clinics(conn, origin):
        try:
            async with conn.begin_nested():
                ids = await source(conn, clinic, origin)
                for decision in sample_for_review([str(i) for i in ids], rate, draw):
                    if await producers_sql.record_sample(conn, clinic_id=clinic, origin=origin, decision_id=uuid.UUID(decision)):
                        made += 1
        except Exception as exc:  # noqa: BLE001
            log.error("sampler sweep failed for one clinic: %s", type(exc).__name__)
    return made
