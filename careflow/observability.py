"""Observability ports and their fakes (BUILD-SPEC.md 13.1, 13.2, 13.5; CLAUDE.md rules 6, 7). MECHANISM ONLY: no Langfuse or Grafana client is imported and nothing is sent anywhere. The real sinks are
external services (a data-flow-register entry and an owner decision each, rule 13); until they are approved, the sinks here are in-memory fakes and the exporter is a no-op sink.

WHAT A RECORD CAN HOLD. `CallCapture` is the per-call/tool/step record of 13.5 and its fields are ALL metadata: opaque ids, engine, model, prompt version and hash, token counts, cost, latency, status,
the argument HASH. There is no field that can carry a prompt, a response or an argument value; `assert_metadata_only` refuses a record whose values look like free text. In the `clinical` profile this metadata-only
record is the ONLY thing a trace sink may receive (13.1); in `demo` (synthetic data) a sink may also receive a `body` that the caller attaches explicitly.

METRIC LABELS (13.2): the series budget is 10,000, so a metric may carry ONLY the labels in `METRIC_LABELS` (workflow, engine, model, status class, clinic). Any other label (a patient, thread or job id) is refused
(`LabelRefused`), not dropped, so the mistake is seen in a test.

NEVER BLOCK THE APP (13.1): the exporter keeps a bounded queue and drops the OLDEST ordinary record when it is full; a sink that raises or hangs is swallowed and counted. Sampling under the cap keeps 100% of
errors, escalations, fallbacks and canaries and a configured share of ordinary successes (`CAREFLOW_TRACE_SAMPLE_RATE`, a REQUIRED setting: unset means ordinary successes are NOT exported, never "all").
"""

import hashlib
import json
import os
import random
import re
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Final, Literal, Protocol

METRIC_LABELS: Final = frozenset({"workflow", "engine", "model", "status_class", "clinic"})
ALWAYS_KEPT: Final = frozenset({"error", "escalation", "fallback", "canary"})
Status = Literal["ok", "empty", "degraded", "unavailable", "error", "escalation", "fallback", "canary"]
_OPAQUE: Final = re.compile(r"^[A-Za-z0-9_.:@/\-]{1,120}$")


class ObservabilityError(Exception):
    """Fixed words only."""


class LabelRefused(ObservabilityError):
    pass


def hash_arguments(arguments: Mapping[str, Any]) -> str:
    """A stable hash of a tool call's arguments: the value never leaves, only this."""
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class CallCapture:
    kind: Literal["model", "tool", "step"]
    name: str  # workflow / tool / step name (a code word)
    clinic: str  # opaque clinic id
    origin: str  # live | test | eval | canary
    status: Status
    latency_ms: int
    engine: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_actual: float | None = None
    cost_shadow: float | None = None
    cache_hit: bool | None = None
    argument_hash: str | None = None
    idempotency_key: str | None = None
    retries: int | None = None
    trace_id: str | None = None  # opaque

    def labels(self) -> dict[str, str]:
        return {"workflow": self.name, "engine": self.engine or "none", "model": self.model or "none", "status_class": status_class(self.status), "clinic": self.clinic}


def status_class(status: str) -> str:
    return "ok" if status in ("ok", "empty") else "degraded" if status in ("degraded", "fallback") else "error"


def assert_metadata_only(record: CallCapture) -> None:
    """Every string value must look like an opaque id, a code word or a hash. A sentence (spaces, punctuation) is refused: it could be patient text."""
    for key, value in asdict(record).items():
        if isinstance(value, str) and not _OPAQUE.match(value):
            raise ObservabilityError(f"field {key} does not look like metadata")


def check_labels(labels: Mapping[str, str]) -> None:
    extra = set(labels) - METRIC_LABELS
    if extra:
        raise LabelRefused("a metric label outside the allowed set: " + ", ".join(sorted(extra)))


class TraceSink(Protocol):
    def send(self, record: Mapping[str, Any]) -> None: ...


class MetricsSink(Protocol):
    def count(self, name: str, labels: Mapping[str, str], value: float = 1.0) -> None: ...


@dataclass
class InMemoryTraceSink:
    records: list[Mapping[str, Any]] = field(default_factory=list)

    def send(self, record: Mapping[str, Any]) -> None:
        self.records.append(dict(record))


@dataclass
class InMemoryMetricsSink:
    series: dict[tuple[str, tuple[tuple[str, str], ...]], float] = field(default_factory=dict)

    def count(self, name: str, labels: Mapping[str, str], value: float = 1.0) -> None:
        check_labels(labels)
        key = (name, tuple(sorted(labels.items())))
        self.series[key] = self.series.get(key, 0.0) + value


def sample_rate_from(environ: Mapping[str, str] | None = None) -> float | None:
    raw = (environ if environ is not None else os.environ).get("CAREFLOW_TRACE_SAMPLE_RATE")
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ObservabilityError("CAREFLOW_TRACE_SAMPLE_RATE is not a number") from None
    if not 0 <= value <= 1:
        raise ObservabilityError("CAREFLOW_TRACE_SAMPLE_RATE must be between 0 and 1")
    return value


class Exporter:
    """Bounded, non-blocking, profile-gated. `capture` never raises and never waits; `flush` hands queued records to the sinks (the worker tick calls it)."""

    def __init__(
        self, trace: TraceSink, metrics: MetricsSink, *, profile: str, sample_rate: float | None, capacity: int = 1000, draw: Callable[[], float] = random.random
    ) -> None:
        self._trace, self._metrics, self._profile, self._rate, self._draw = trace, metrics, profile, sample_rate, draw
        self._queue: deque[tuple[CallCapture, str | None]] = deque(maxlen=capacity)
        self.dropped = 0
        self.sink_failures = 0
        self.refused = 0

    def capture(self, record: CallCapture, *, body: str | None = None) -> None:
        try:
            assert_metadata_only(record)
            self._metrics.count("careflow_calls_total", record.labels())
        except ObservabilityError:
            self.refused += 1
            return
        except Exception:  # noqa: BLE001 - tracing must never break the app
            self.sink_failures += 1
            return
        if not self._kept(record):
            return
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1  # the deque drops the oldest on append
        # A body is attached ONLY outside the clinical profile: in `clinical` prompt and response bodies never leave Postgres (13.1).
        self._queue.append((record, body if self._profile != "clinical" else None))

    def _kept(self, record: CallCapture) -> bool:
        if record.status in ALWAYS_KEPT or record.origin == "canary":
            return True
        return self._rate is not None and self._draw() < self._rate

    def flush(self) -> int:
        sent = 0
        while self._queue:
            record, body = self._queue.popleft()
            payload: dict[str, Any] = asdict(record)
            if body is not None:
                payload["body"] = body
            try:
                self._trace.send(payload)
                sent += 1
            except Exception:  # noqa: BLE001
                self.sink_failures += 1
        return sent
