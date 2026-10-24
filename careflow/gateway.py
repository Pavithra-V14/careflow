"""The in-process gateway: routing, fallback, retries, circuit breakers and spend ceilings (BUILD-SPEC.md §2, §5.1, §5.2, §6.1, §16.1).

Every model-role call goes through `Gateway.call`. It never talks to a network itself; it calls an adapter registered for the chosen
registry row (today only the fakes in `careflow.adapters.fake`; a real adapter arrives with an owner-approved service, CLAUDE.md rule 13).

WHAT IT DECIDES, IN ORDER
  1. Candidates: the enabled registry rows for the role, best priority first.
  2. Data policy (§2, §5.2): a `PHI` request is only ever offered to a row whose `data_policy` is `phi_approved`. If none exists the call
     raises `NoApprovedRoute` so the caller can DEGRADE VISIBLY (§2 table); it never falls back to a `synthetic_only` row. This filter is
     applied before anything else and no call class bypasses it.
  3. For each candidate: skip it if its circuit breaker is open (10 consecutive failures; one probe after 60 s, §16.1), or, for an ORDINARY
     call, if its estimated cost would pass the per-request or the per-clinic daily ceiling (a free row costs nothing, so it is never
     skipped for spend). Otherwise call it, up to 3 attempts with exponential backoff and jitter on a TRANSIENT failure (a PERMANENT failure
     goes straight to the next row). Every attempt counts as one model call against the run's budget (`careflow.budget`).
  4. The first success wins. The result records WHICH engine answered, the attempts made on the way, the actual cost and the SHADOW cost
     (what the equivalent paid model would have cost, §5.1), so cost per outcome can be computed for free-tier rows too.

THE SAFETY CLASS (§6.1: "the red-flag path and the safety net are never budget-limited"). A call made with `CallClass.SAFETY` is counted but
never refused by the step, tool, model, time or cost ceilings, and it skips the spend ceilings. It still obeys the data policy, still uses
fallback, and still honours a circuit breaker (an open breaker means that engine is down, which is not a budget). Nothing about the safety
class lets a call reach a route the data policy forbids.

Not here (and why): the registry rows come from the caller (a loader over the `ai` schema arrives with migration 0015's reader); the per-clinic
daily spend ledger is an in-memory `SpendLedger` behind a port, so a restart forgets the day's spend until a durable one is built. That
under-enforces after a restart; it is recorded as an open item, and it is harmless while no paid route is wired. No logging: callers log
what they choose, and the gateway has no text to log.
"""

import asyncio
import random
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Final, Protocol

from careflow.budget import CallClass, RunBudget
from careflow.ports import (
    ROLES,
    AdapterResult,
    DataClass,
    PermanentError,
    TransientError,
    Unit,
    Usage,
)

DATA_POLICIES: Final = ("synthetic_only", "phi_approved")
BREAKER_FAILURES: Final = 10
BREAKER_PROBE_SECONDS: Final = 60.0
MAX_ATTEMPTS_PER_ROUTE: Final = 3  # §16.1: "max 3 attempts on a synchronous external call"
DEFAULT_TIMEOUT_SECONDS: Final = 20.0
_UNPINNED = re.compile(r"(^|[-_:/@])(latest|default|stable|current)$", re.IGNORECASE)

Adapter = Callable[[Any], Awaitable[AdapterResult]]


class GatewayError(Exception):
    """Base class. Messages name roles, providers and ceilings, never request text."""


class NoApprovedRoute(GatewayError):
    """No enabled registry row may handle this request (for a PHI request: none is phi_approved). The caller must degrade visibly."""

    def __init__(self, role: str, data_class: DataClass) -> None:
        super().__init__(f"no approved route for role {role!r} with {data_class.value} data")
        self.role, self.data_class = role, data_class


class SpendCeilingReached(GatewayError):
    """Every candidate was skipped because it would pass the per-request or per-clinic daily ceiling."""


class AllRoutesFailed(GatewayError):
    def __init__(self, role: str, attempts: tuple["RouteAttempt", ...]) -> None:
        super().__init__(f"every route for role {role!r} failed or was skipped")
        self.role, self.attempts = role, attempts


@dataclass(frozen=True)
class ModelRow:
    """One row of the model registry (§5.1): provider, PINNED model id, data policy, priority within the role, and prices per 1k units.
    A free-tier row has an actual price of 0 and a non-zero shadow price. `voice_live` is priced per 1k AUDIO tokens, every other role per
    1k text tokens: the unit is part of the row and a mismatch is refused."""

    role: str
    provider: str
    model_id: str
    data_policy: str
    priority: int
    price_in_per_1k: Decimal
    price_out_per_1k: Decimal
    shadow_in_per_1k: Decimal
    shadow_out_per_1k: Decimal
    unit: Unit = "text_tokens"
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    enabled: bool = True
    id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")
        if self.data_policy not in DATA_POLICIES:
            raise ValueError(f"data_policy must be one of {DATA_POLICIES}")
        if not self.provider.strip() or not self.model_id.strip():
            raise ValueError("a registry row needs a provider and a model id")
        if self.model_id != self.model_id.strip(" ") or any(ord(ch) < 32 or ord(ch) == 127 for ch in self.model_id):
            raise ValueError("a model id carries no leading or trailing space and no control character")
        if _UNPINNED.search(self.model_id):
            raise ValueError("a model id must be pinned, not 'latest' or similar (§5.1)")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int) or self.priority < 1:
            raise ValueError("priority must be a positive integer")
        if (self.unit == "audio_tokens") != (self.role == "voice_live"):
            raise ValueError("voice_live is priced per audio token, and only voice_live is")
        if min(self.price_in_per_1k, self.price_out_per_1k, self.shadow_in_per_1k, self.shadow_out_per_1k) < 0:
            raise ValueError("prices cannot be negative")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.model_id)

    def cost(self, usage: Usage) -> tuple[Decimal, Decimal]:
        """(actual, shadow) cost of `usage` in USD. A usage in the wrong unit is a bug in the adapter, not something to price."""
        if usage.unit != self.unit:
            raise GatewayError(f"adapter reported {usage.unit} usage for a {self.unit} row")
        k = Decimal(1000)
        return (
            Decimal(usage.input_tokens) / k * self.price_in_per_1k + Decimal(usage.output_tokens) / k * self.price_out_per_1k,
            Decimal(usage.input_tokens) / k * self.shadow_in_per_1k + Decimal(usage.output_tokens) / k * self.shadow_out_per_1k,
        )


@dataclass(frozen=True)
class RouteAttempt:
    provider: str
    model_id: str
    outcome: str  # ok | skipped_no_adapter | skipped_breaker_open | skipped_spend | failed_transient | timeout | failed_permanent
    calls: int = 0


@dataclass(frozen=True)
class GatewayResult[T]:
    value: T
    provider: str
    model_id: str
    usage: Usage
    cost_usd: Decimal
    shadow_cost_usd: Decimal
    attempts: tuple[RouteAttempt, ...]


class SpendLedger(Protocol):
    def spent(self, clinic_id: uuid.UUID, day: date) -> Decimal: ...

    def record(self, clinic_id: uuid.UUID, day: date, usd: Decimal) -> None: ...


class InMemorySpendLedger:
    def __init__(self) -> None:
        self._spent: dict[tuple[uuid.UUID, date], Decimal] = {}

    def spent(self, clinic_id: uuid.UUID, day: date) -> Decimal:
        return self._spent.get((clinic_id, day), Decimal(0))

    def record(self, clinic_id: uuid.UUID, day: date, usd: Decimal) -> None:
        self._spent[(clinic_id, day)] = self.spent(clinic_id, day) + usd


@dataclass(frozen=True)
class SpendLimits:
    """`clinic_daily_usd` has no default: it is a cost decision for the owner, and a gateway built without one must say so."""

    clinic_daily_usd: Decimal
    per_request_usd: Decimal = Decimal("1.00")  # §6.1: absolute hard stop per job

    def __post_init__(self) -> None:
        if self.clinic_daily_usd < 0 or self.per_request_usd < 0:
            raise ValueError("spend ceilings cannot be negative")


class CircuitBreaker:
    """Closed -> open after `failures` consecutive failures; after `probe_after` seconds ONE probe is allowed (half-open): a success closes
    the breaker, a failure re-opens it for another `probe_after`. One breaker per external dependency (§16.1)."""

    def __init__(self, *, failures: int = BREAKER_FAILURES, probe_after: float = BREAKER_PROBE_SECONDS, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit, self._probe_after, self._clock = failures, probe_after, clock
        self._consecutive = 0
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half_open" if self._probing or self._clock() - self._opened_at >= self._probe_after else "open"

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._probing:
            return False  # a probe is already in flight
        if self._clock() - self._opened_at >= self._probe_after:
            self._probing = True
            return True
        return False

    def success(self) -> None:
        self._consecutive, self._opened_at, self._probing = 0, None, False

    def failure(self) -> None:
        self._probing = False
        self._consecutive += 1
        if self._opened_at is not None or self._consecutive >= self._limit:
            self._opened_at = self._clock()


@dataclass
class Gateway:
    registry: Sequence[ModelRow]
    adapters: Mapping[tuple[str, str], Adapter]
    ledger: SpendLedger
    limits: SpendLimits
    clock: Callable[[], float] = time.monotonic
    today: Callable[[], date] = lambda: datetime.now(UTC).date()
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    jitter: Callable[[], float] = random.random
    base_delay: float = 0.2
    breaker_failures: int = BREAKER_FAILURES
    breaker_probe_seconds: float = BREAKER_PROBE_SECONDS
    _breakers: dict[tuple[str, str], CircuitBreaker] = field(default_factory=dict, init=False, repr=False)

    def breaker(self, key: tuple[str, str]) -> CircuitBreaker:
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(failures=self.breaker_failures, probe_after=self.breaker_probe_seconds, clock=self.clock)
        return self._breakers[key]

    def _candidates(self, role: str, data_class: DataClass) -> list[ModelRow]:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        rows = sorted((r for r in self.registry if r.role == role and r.enabled), key=lambda r: (r.priority, r.provider, r.model_id))
        if data_class is DataClass.PHI:
            rows = [r for r in rows if r.data_policy == "phi_approved"]  # no call class bypasses this
        return rows

    def _spend_blocked(self, row: ModelRow, clinic_id: uuid.UUID, estimate: Usage | None) -> bool:
        """True if an ORDINARY call to this row would pass the per-request or the per-clinic daily ceiling. A free row costs nothing, so no
        spend ceiling applies to it. With no estimate the cost is unknown, which is not zero: a paid row is then blocked once the day's
        spend has reached the ceiling."""
        if row.price_in_per_1k == 0 and row.price_out_per_1k == 0:
            return False
        est = row.cost(estimate)[0] if estimate is not None else Decimal(0)
        spent = self.ledger.spent(clinic_id, self.today())
        if est > self.limits.per_request_usd or spent + est > self.limits.clinic_daily_usd:
            return True
        return estimate is None and spent >= self.limits.clinic_daily_usd

    async def call(
        self,
        role: str,
        payload: Any,
        *,
        data_class: DataClass,
        clinic_id: uuid.UUID,
        call_class: CallClass = CallClass.ORDINARY,
        budget: RunBudget | None = None,
        estimate: Usage | None = None,
    ) -> "GatewayResult[Any]":
        rows = self._candidates(role, data_class)
        if not rows:
            raise NoApprovedRoute(role, data_class)
        attempts: list[RouteAttempt] = []
        any_spend_skip = False
        for row in rows:
            adapter = self.adapters.get(row.key)
            if adapter is None:
                attempts.append(RouteAttempt(row.provider, row.model_id, "skipped_no_adapter"))
                continue
            if call_class is CallClass.ORDINARY and self._spend_blocked(row, clinic_id, estimate):
                any_spend_skip = True
                attempts.append(RouteAttempt(row.provider, row.model_id, "skipped_spend"))
                continue
            breaker = self.breaker(row.key)
            if not breaker.allow():
                attempts.append(RouteAttempt(row.provider, row.model_id, "skipped_breaker_open"))
                continue
            outcome = await self._try_route(row, adapter, breaker, payload, clinic_id, call_class, budget, attempts)
            if outcome is not None:
                return outcome
        if any_spend_skip and all(a.outcome in {"skipped_spend", "skipped_no_adapter", "skipped_breaker_open"} for a in attempts):
            raise SpendCeilingReached("every available route would pass a spend ceiling")
        raise AllRoutesFailed(role, tuple(attempts))

    async def _try_route(self, row, adapter, breaker, payload, clinic_id, call_class, budget, attempts):
        calls, last = 0, "failed_transient"
        for n in range(1, MAX_ATTEMPTS_PER_ROUTE + 1):
            if n > 1 and not breaker.allow():
                break  # the breaker opened during our own retries: stop hammering the engine
            if budget is not None:
                budget.model_call(call_class)  # BudgetExceeded propagates: a ceiling is not a route failure
            calls += 1
            try:
                out = await asyncio.wait_for(adapter(payload), row.timeout_seconds)
            except TimeoutError:
                breaker.failure()
                last = "timeout"
            except TransientError:
                breaker.failure()
                last = "failed_transient"
            except PermanentError:
                breaker.failure()
                last = "failed_permanent"
                break
            else:
                breaker.success()
                actual, shadow = row.cost(out.usage)
                self.ledger.record(clinic_id, self.today(), actual)
                attempts.append(RouteAttempt(row.provider, row.model_id, "ok", calls))
                if budget is not None:
                    budget.add_cost(float(actual), call_class)
                return GatewayResult(out.value, row.provider, row.model_id, out.usage, actual, shadow, tuple(attempts))
            if n < MAX_ATTEMPTS_PER_ROUTE:
                await self.sleep(self.base_delay * (2 ** (n - 1)) * (0.5 + self.jitter()))
        attempts.append(RouteAttempt(row.provider, row.model_id, last, calls))
        return None
