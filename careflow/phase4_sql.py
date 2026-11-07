"""The SQL halves of the Phase 4 ports, each running on the CALLER's own transaction under row-level security (migrations 0039, 0040). No policy is decided here.

* `SqlAlertSink`: inserts into ops.alerts and NEVER uses RETURNING (the writer, a queue worker or a reviewer, has no SELECT right on what it writes). A repeat is a no-op (the key is unique per clinic), caught from a plain insert.
* `SqlWatchdogReader`: the facts the watchdog needs, read as the queue worker: open review tasks of the clinic and the queue's age and dead-letter count. Counts and ages only; no payload is read.
* `watchdog_sweep`: the tick's end-of-tick sweep: for every clinic the worker serves, run the watchdog once. A failing clinic is logged by class and skipped; the rest still run.
* `reviewer_stats_rows`: calls agents.reviewer_stats (admin only; the database refuses anyone else).
"""

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from careflow.cost_canvas import Measured
from careflow.review_console import DeadlinePolicy, OpenTask
from careflow.reviewer_stats import ReviewerRow
from careflow.watchdog import Alert, AlertRouter, Limits, QueueFacts, run_watchdog

log = logging.getLogger("careflow.phase4")


class SqlAlertSink:
    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    async def raise_alert(self, alert: Alert) -> None:
        await self._insert(alert.clinic_id, alert.origin, alert.alert_class, alert.audience_role, alert.count, alert.ref, alert.key)

    async def raise_alert_fields(self, *, clinic_id: str, origin: str, alert_class: str, audience_role: str, count: int, ref: str, idempotency_key: str) -> None:
        """The shape the canary hook uses (careflow/canary.AlertWriter)."""
        await self._insert(clinic_id, origin, alert_class, audience_role, count, ref, idempotency_key)

    # (the canary module calls it `raise_alert` with keywords; this adapter is given to it through `CanaryAlertWriter`)

    async def _insert(self, clinic_id: str, origin: str, alert_class: str, audience_role: str, count: int, ref: str | None, key: str) -> None:
        """A PLAIN insert in a savepoint; a repeat (the unique key) is caught and is a no-op. NOT `on conflict do nothing`: with row-level security PostgreSQL also checks the table's SELECT policy for an
        INSERT that has an ON CONFLICT clause, and the writer (a queue worker, or a reviewer for the silent canary alert) is deliberately not in the audience that may read the row (CI run 42201883307)."""
        try:
            async with self._conn.begin_nested():
                await self._conn.execute(
                    text(
                        "insert into ops.alerts (clinic_id, origin, class, audience_role, alert_count, ref, idempotency_key) values (:c, :o, :k, :a, :n, :r, :i)"
                    ),
                    {"c": uuid.UUID(clinic_id), "o": origin, "k": alert_class, "a": audience_role, "n": count, "r": ref, "i": key},
                )
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) != "23505" and "23505" not in str(getattr(exc.orig, "pgcode", "")) and "UniqueViolation" not in type(getattr(exc.orig, "__cause__", exc.orig)).__name__:
                raise  # only the repeat of the same key is a no-op; any other integrity error is real


class CanaryAlertWriter:
    """Adapts SqlAlertSink to careflow.canary.AlertWriter (keyword form)."""

    def __init__(self, sink: SqlAlertSink) -> None:
        self._sink = sink

    async def raise_alert(self, *, clinic_id: str, origin: str, alert_class: str, audience_role: str, count: int, ref: str, idempotency_key: str) -> None:
        await self._sink.raise_alert_fields(clinic_id=clinic_id, origin=origin, alert_class=alert_class, audience_role=audience_role, count=count, ref=ref, idempotency_key=idempotency_key)


class SqlWatchdogReader:
    def __init__(self, conn: AsyncConnection, origin: str = "live") -> None:
        self._conn, self._origin = conn, origin

    async def open_tasks(self, clinic_id: str) -> Sequence[OpenTask]:
        rows = (
            await self._conn.execute(
                text("select id, kind, assigned_role, created_at from agents.review_tasks where clinic_id = :c and origin = :o and status = 'open' order by created_at limit 500"),
                {"c": uuid.UUID(clinic_id), "o": self._origin},
            )
        ).all()
        return [OpenTask(str(r.id), r.kind, r.assigned_role, r.created_at) for r in rows]

    async def queue_facts(self, clinic_id: str) -> QueueFacts:
        row = (
            await self._conn.execute(
                text(
                    "select (select extract(epoch from (clock_timestamp() - min(q.created_at)))::bigint from ops.queue q where q.clinic_id = :c and q.origin = :o and q.state = 'unclaimed') as oldest, "
                    "(select count(*) from ops.queue d where d.clinic_id = :c and d.origin = :o and d.state = 'dead_lettered' and d.dead_lettered_at > clock_timestamp() - interval '7 days') as dead"
                ),
                {"c": uuid.UUID(clinic_id), "o": self._origin},
            )
        ).first()
        return QueueFacts(None if row is None or row.oldest is None else int(row.oldest), int(row.dead) if row else 0)


async def watchdog_sweep(conn: AsyncConnection, router: AlertRouter, deadlines: DeadlinePolicy | None, limits: Limits, *, origin: str = "live") -> int:
    """The tick sweep. Returns how many alerts were raised. The clinics are those the worker account holds `queue_worker` in (the clinic rows it can see)."""
    clinics = [str(r[0]) for r in (await conn.execute(text("select id from iam.clinics where origin = :o and iam.has_clinic_role(id, array['queue_worker']) order by id"), {"o": origin})).all()]
    reader = SqlWatchdogReader(conn, origin)
    raised = 0
    for clinic in clinics:
        try:
            raised += (await run_watchdog(clinic, origin, reader, router, now=datetime.now(UTC), deadlines=deadlines, limits=limits)).raised
        except Exception as exc:  # noqa: BLE001 - one clinic failing must not stop the others
            log.error("watchdog pass failed for one clinic: %s", type(exc).__name__)
    return raised


async def reviewer_stats_rows(conn: AsyncConnection, clinic_id: uuid.UUID, days: int) -> list[ReviewerRow]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = (await conn.execute(text("select * from agents.reviewer_stats(:c, :s)"), {"c": clinic_id, "s": since})).all()
    return [
        ReviewerRow(str(r.reviewer), r.decisions, r.approvals, r.edits, r.rejections, r.canaries_seen, r.canaries_approved, None if r.median_seconds is None else float(r.median_seconds))
        for r in rows
    ]


async def measured_costs(conn: AsyncConnection, clinic_id: uuid.UUID, days: int) -> "Measured":
    """The cost canvas inputs for one clinic from what the caller (admin) can read: the spend ledger and the queue. Live origin only. The retry cost and the reviewer minutes are NOT measured yet
    (the ledger does not mark a retry and no per-role time is recorded), so they are None and the canvas says so; they are never taken as zero."""
    from decimal import Decimal

    from careflow.cost_canvas import Measured

    spend = (
        await conn.execute(
            text(
                "select count(*) as calls, count(distinct day) as days, coalesce(sum(usd_actual), 0) as actual, coalesce(sum(usd_shadow), 0) as shadow "
                "from ops.spend_ledger where clinic_id = :c and origin = 'live' and created_at >= clock_timestamp() - make_interval(days => :d)"
            ),
            {"c": clinic_id, "d": days},
        )
    ).one()
    workflows = (
        await conn.execute(
            text("select count(*) from ops.queue where clinic_id = :c and origin = 'live' and state = 'completed' and completed_at >= clock_timestamp() - make_interval(days => :d)"),
            {"c": clinic_id, "d": days},
        )
    ).scalar()
    return Measured(int(spend.days), int(workflows or 0), int(spend.calls), Decimal(spend.actual), Decimal(spend.shadow), None, None, None)
