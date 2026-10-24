"""The ports every external capability sits behind (BUILD-SPEC.md §3, §5, §10, §18, §21): DecisionPort, EmbeddingPort, RerankPort,
TranslationPort, EmailPort and SmsPort. The queue has its own (`careflow.queue.QueuePort`).

A port is a typed Protocol plus the small value types that cross it. Nothing here calls a network or imports an HTTP client; the real
adapters arrive in the package that needs each one, after the owner approves the service (CLAUDE.md rule 13), and each adds its
data-flow-register entry. Until then every port has a fake in `careflow.adapters.fake`, so the whole suite runs with no network (§21).

Two rules the types themselves enforce:
  * `DecisionRequest` refuses more than 20 options (§5.2: "never more than 20 options in one `choice` call"; accuracy collapses above it).
  * Every model-role adapter reports `Usage`, because cost is computed from the registry's price and the tokens used (§5.1, §15). Voice is
    priced by AUDIO tokens in its own unit, so `Usage` names its unit and the registry refuses a mismatch.

Data class: a request is `SYNTHETIC` or `PHI`. The gateway (careflow.gateway) will only send a `PHI` request to a registry row whose
`data_policy` is `phi_approved`; an adapter never decides that for itself.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal, Protocol

MAX_DECISION_OPTIONS: Final = 20  # §5.2 hard rule
ROLES: Final = ("llm_large", "llm_planner", "llm_fast", "vlm", "embed", "rerank", "llm_judge", "decision", "voice_live")
Unit = Literal["text_tokens", "audio_tokens"]


class TransientError(Exception):
    """A failure worth retrying (a timeout, a 5xx, a rate limit). Adapters raise it; the gateway retries it with backoff."""


class PermanentError(Exception):
    """A failure that retrying cannot fix (a refused request, a bad key). The gateway goes straight to the next route."""


class RouteNotApplicable(Exception):
    """This engine cannot handle THIS input (an unsupported script, an over-long text). It is not a failure: the gateway skips the route
    without counting a model call or a breaker failure. An adapter that raises it must not have called its model."""


class DataClass(StrEnum):
    SYNTHETIC = "synthetic"
    PHI = "phi"  # real patient text: only a phi_approved route may see it


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    unit: Unit = "text_tokens"


@dataclass(frozen=True)
class AdapterResult[T]:
    """What an adapter returns to the gateway: the typed value and what it used."""

    value: T
    usage: Usage = field(default_factory=Usage)


# ---- decision ----------------------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionRequest:
    question_type: str  # calibration is stored per (engine, question_type), §5.2
    text: str = field(repr=False)  # may be patient text: never in a repr
    options: tuple[str, ...]
    # REQUIRED, no default: a request that does not say what it carries is not assumed to be synthetic. A third-party adapter refuses PHI itself.
    data_class: DataClass
    language: str = "en"  # a hint for engine/checkpoint choice, e.g. "en", "ta", "ta-Latn" (romanised Tamil); never trusted over the script

    def __post_init__(self) -> None:
        if not self.question_type.strip():
            raise ValueError("a decision needs a question_type")
        if len(self.options) < 2:
            raise ValueError("a decision needs at least two options")
        if len(self.options) > MAX_DECISION_OPTIONS:
            raise ValueError(f"a decision call takes at most {MAX_DECISION_OPTIONS} options")
        if len(set(self.options)) != len(self.options):
            raise ValueError("decision options must be distinct")


@dataclass(frozen=True)
class DecisionResult:
    choice: str
    probabilities: dict[str, float]
    confidence: float  # the probability of `choice`
    engine: str  # which engine answered (§5.2: every call records it)
    # Whether `probabilities` were calibrated for (engine, question_type) (§5.2). False means the confidence is RAW and must be treated as
    # unknown, not as a probability: nothing may auto-approve on it.
    calibrated: bool = False
    route: str = ""  # the checkpoint or prompt version that answered, for the record


class DecisionPort(Protocol):
    async def decide(self, request: DecisionRequest) -> AdapterResult[DecisionResult]: ...


# ---- embedding, rerank, translation ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    dimensions: int


class EmbeddingPort(Protocol):
    async def embed(self, texts: list[str]) -> AdapterResult[EmbeddingResult]: ...


@dataclass(frozen=True)
class VectorPoint:
    """One protocol card in the vector index (BUILD-SPEC.md 10.2: one card = one retrieval unit; cards hold no patient data). The index is
    disposable: Postgres is truth, and `version` is what the index was built from, checked again at the join (10.8)."""

    card_id: str
    version: int
    vector: tuple[float, ...] = field(repr=False)
    scope: dict[str, str] = field(default_factory=dict)  # deterministic metadata only; the only fields allowed to gate a query (10.7)


@dataclass(frozen=True)
class VectorHit:
    card_id: str
    version: int
    similarity: float  # cosine, in [-1, 1]


class VectorStorePort(Protocol):
    """The card index (Qdrant). `scope` is a PRE-filter (10.7) and is required: a search with an empty scope raises rather than searching everything."""

    async def upsert(self, points: list[VectorPoint]) -> None: ...

    async def search(self, vector: tuple[float, ...], *, limit: int, scope: dict[str, str]) -> list[VectorHit]: ...

    async def delete(self, card_ids: list[str]) -> None: ...


@dataclass(frozen=True)
class RerankRequest:
    query: str = field(repr=False)
    candidates: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True)
class RerankResult:
    order: list[int]  # candidate indexes, best first
    scores: list[float]  # aligned to the candidates as given


class RerankPort(Protocol):
    async def rerank(self, request: RerankRequest) -> AdapterResult[RerankResult]: ...


@dataclass(frozen=True)
class TranslationRequest:
    text: str = field(repr=False)
    source: str
    target: str


@dataclass(frozen=True)
class TranslationResult:
    text: str = field(repr=False)
    source: str
    target: str


class TranslationPort(Protocol):
    async def translate(self, request: TranslationRequest) -> AdapterResult[TranslationResult]: ...


# ---- email and SMS (not model roles: no registry row, no token usage) ------------------------------------------------------------------------


@dataclass(frozen=True)
class SendResult:
    provider_id: str
    replayed: bool = False


class EmailPort(Protocol):
    async def send(self, to: str, subject: str, body: str, *, idempotency_key: str) -> SendResult: ...


class SmsPort(Protocol):
    async def send(self, to: str, body: str, *, idempotency_key: str) -> SendResult: ...


# ---- uploads (BUILD-SPEC.md §17.3; docs/design/uploads-supabase-storage.md) ------------------------------------------------------------------


@dataclass(frozen=True)
class SignedUpload:
    """What the browser is handed: a short-lived URL it can PUT one object to, at a path the SERVER chose. The signature may carry a size and
    type limit, but the server never relies on that: it re-verifies the stored object itself (careflow.documents)."""

    url: str
    path: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredObject:
    """What the storage actually holds at a path, as observed by the server side of the adapter, not as claimed by any client."""

    path: str
    size: int
    content_type: str  # as stored (may be whatever the uploader said; verified against `head` by the caller)
    head: bytes = field(repr=False)  # the first bytes of the content, for a magic-number check
    sha256: str  # lowercase hex of the whole content


class UploadPort(Protocol):
    async def create_signed_upload(self, *, path: str, content_type: str, max_bytes: int, expires_in_seconds: int) -> SignedUpload: ...

    async def describe(self, path: str) -> StoredObject | None:
        """The object at `path`, or None if nothing is there. Reads the stored bytes; never trusts a client-supplied size or type."""
        ...

    async def read(self, path: str) -> bytes | None:
        """The full bytes at `path`, or None if nothing is there. For a worker that is about to use the document: the caller re-hashes them."""
        ...

    async def delete(self, path: str) -> None:
        """Remove the object at `path` (a no-op if it is already gone)."""
        ...
