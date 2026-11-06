"""The independent in-app watchdog and alert routing (BUILD-SPEC.md 13.3, 13.4). It runs on the worker's minute tick (a `Sweep`, careflow/tick.py) and depends on NO third party: critical safety alerting must not
rest on a free tier's uptime. It READS operational state and WRITES only alerts; it never approves, resolves, releases or retries anything.

CHECKS (each produces an alert only when something is actually wrong):
  * a red-flag task past its deadline -> `red_flag_overdue`, aimed at the nurse (and, through the escalation chain, the next role up);
  * any other review task past its deadline -> `task_escalated` to the next role up the chain (careflow/review_console.escalations);
  * the oldest queued job older than a limit -> `queue_age` (admin);  any dead-lettered job -> `dead_letters` (admin).
THE LIMITS ARE REQUIRED SETTINGS with no default (`CAREFLOW_WATCHDOG_LIMITS`, JSON `{"red_flag_minutes": n, "queue_age_seconds": n}`; task deadlines are `CAREFLOW_REVIEW_DEADLINE_MINUTES`). A check whose
limit is not configured does not run and is REPORTED as `unconfigured` in the result (a silent skip would look like health).

ROUTING (13.4): the alert class decides the audience role and the channels. The message that leaves the process (e-mail, Slack: third parties) is `class + count + opaque id + sign-in link`, built by
`outbound_text` from the alert alone; it has no field that could carry a name, a diagnosis or a value. A FAILED delivery on any channel raises a `delivery_failed` alert for admin (the in-app banner), so a dead
channel is never silently dead. The real e-mail and Slack senders are external services (rule 13); here they are ports with fakes, and the in-app channel (the `ops.alerts` row) is real.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol

from careflow.review_console import DeadlinePolicy, OpenTask, escalations

CLASS_AUDIENCE: Final = {
    "red_flag_overdue": "nurse",
    "task_overdue": "nurse",
    "task_escalated": "doctor",
    "dead_letters": "admin",
    "queue_age": "admin",
    "canary_approved": "admin",
    "spend_runaway": "admin",
    "delivery_failed": "admin",
    "fallback_spike": "admin",
    "tracing_cap": "admin",
}
# Channels beyond the in-app row (which always exists). 13.4: an escalation also nudges by e-mail; the admin classes also go to the admin Slack channel; a canary goes to admin in-app + Slack.
EXTRA_CHANNELS: Final = {
    "task_escalated": ("email",),
    "dead_letters": ("slack",),
    "queue_age": ("slack",),
    "spend_runaway": ("slack",),
    "delivery_failed": ("slack",),
    "fallback_spike": ("slack",),
    "tracing_cap": ("slack",),
    "canary_approved": ("slack",),
}


@dataclass(frozen=True)
class Alert:
    clinic_id: str
    origin: str
    alert_class: str
    count: int
    ref: str | None
    key: str

    @property
    def audience_role(self) -> str:
        return CLASS_AUDIENCE[self.alert_class]


def outbound_text(alert: Alert, sign_in_url: str) -> str:
    """The ONLY text that leaves the process for an alert: class, count, opaque id, link. Built from the alert's fixed fields, nothing else."""
    ref = f" id {alert.ref}" if alert.ref else ""
    return f"CareFlow alert: {alert.alert_class} x{alert.count}{ref}. Sign in: {sign_in_url}"


class InAppSink(Protocol):
    async def raise_alert(self, alert: Alert) -> None: ...


class ChannelSender(Protocol):
    async def send(self, text: str) -> None: ...


@dataclass
class FakeChannel:
    sent: list[str] = field(default_factory=list)
    fail: bool = False

    async def send(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("delivery failed")
        self.sent.append(text)


class AlertRouter:
    def __init__(self, in_app: InAppSink, channels: dict[str, ChannelSender], sign_in_url: str) -> None:
        self._in_app, self._channels, self._url = in_app, channels, sign_in_url

    async def route(self, alert: Alert) -> None:
        await self._in_app.raise_alert(alert)
        for name in EXTRA_CHANNELS.get(alert.alert_class, ()):
            sender = self._channels.get(name)
            try:
                if sender is None:
                    raise RuntimeError("channel not configured")
                await sender.send(outbound_text(alert, self._url))
            except Exception:  # noqa: BLE001 - a dead channel is made visible, never silent and never fatal
                await self._in_app.raise_alert(Alert(alert.clinic_id, alert.origin, "delivery_failed", 1, None, f"delivery-failed:{name}:{alert.key}"))


@dataclass(frozen=True)
class Limits:
    red_flag_minutes: int | None
    queue_age_seconds: int | None


def limits_from(raw: str | None) -> Limits:
    import json

    if raw is None or not raw.strip():
        return Limits(None, None)
    try:
        d = json.loads(raw)
        out = Limits(
            int(d["red_flag_minutes"]) if "red_flag_minutes" in d else None, int(d["queue_age_seconds"]) if "queue_age_seconds" in d else None
        )
    except (ValueError, TypeError, AttributeError):
        raise ValueError("CAREFLOW_WATCHDOG_LIMITS is malformed") from None
    if any(v is not None and v < 1 for v in (out.red_flag_minutes, out.queue_age_seconds)):
        raise ValueError("CAREFLOW_WATCHDOG_LIMITS values must be positive")
    return out


@dataclass(frozen=True)
class QueueFacts:
    oldest_queued_age_seconds: int | None
    dead_lettered: int


class Reader(Protocol):
    async def open_tasks(self, clinic_id: str) -> Sequence[OpenTask]: ...
    async def queue_facts(self, clinic_id: str) -> QueueFacts: ...


@dataclass
class WatchdogResult:
    raised: int = 0
    unconfigured: list[str] = field(default_factory=list)


async def run_watchdog(
    clinic_id: str, origin: str, reader: Reader, router: AlertRouter, *, now: datetime, deadlines: DeadlinePolicy | None, limits: Limits
) -> WatchdogResult:
    """One pass for one clinic. Idempotent through the alert keys: a second pass in the same state raises nothing new. Nothing it reads is changed."""
    result = WatchdogResult()

    async def raise_(alert: Alert) -> None:
        await router.route(alert)
        result.raised += 1

    tasks = list(await reader.open_tasks(clinic_id))
    due, no_deadline = escalations(tasks, now, deadlines)
    result.unconfigured += [f"deadline:{kind}" for kind in no_deadline]
    by_id = {t.task_id: t for t in tasks}
    for e in due:
        task = by_id[e.task_id]
        if task.kind == "red_flag":
            await raise_(Alert(clinic_id, origin, "red_flag_overdue", 1, e.task_id, f"red-flag-overdue:{e.task_id}:{e.level}"))
        else:
            await raise_(Alert(clinic_id, origin, "task_escalated", 1, e.task_id, f"escalated:{e.task_id}:{e.level}"))
    if limits.red_flag_minutes is None:
        result.unconfigured.append("red_flag_minutes")
    else:
        for t in tasks:
            if t.kind == "red_flag" and (now - t.created_at).total_seconds() / 60 >= limits.red_flag_minutes and not any(e.task_id == t.task_id for e in due):
                await raise_(Alert(clinic_id, origin, "red_flag_overdue", 1, t.task_id, f"red-flag-overdue:{t.task_id}:0"))
    facts = await reader.queue_facts(clinic_id)
    if limits.queue_age_seconds is None:
        result.unconfigured.append("queue_age_seconds")
    elif facts.oldest_queued_age_seconds is not None and facts.oldest_queued_age_seconds >= limits.queue_age_seconds:
        await raise_(Alert(clinic_id, origin, "queue_age", 1, None, f"queue-age:{now:%Y%m%d%H}"))
    if facts.dead_lettered > 0:
        await raise_(Alert(clinic_id, origin, "dead_letters", facts.dead_lettered, None, f"dead-letters:{facts.dead_lettered}:{now:%Y%m%d%H}"))
    return result


Sweeper = Callable[[object], Awaitable[int]]
