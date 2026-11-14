"""Embeddings: the vector size, the deterministic FAKE embedder (the default), and the gateway wrapper that lets the hosted one be used as an `EmbeddingPort` (BUILD-SPEC.md 10.1, 5.1; CLAUDE.md rules 2, 7).

768 numbers per vector: the size of kb.card_embeddings (migration 0085) and what the Gemini embedding adapter asks for. `HashingEmbedding` is the FAKE: a bag of hashed words, normalised, so two texts that share words
are close and two that share none are far. It calls nothing, needs no key, and makes the whole retrieval path (index, search, version join, abstention, quality harness) runnable and testable offline. It is NOT
a language model's understanding of meaning: a synonym is as far away as an unrelated word. Its `model_id` is `fake-hash-768`, a different value from any hosted model's, so a fake index and a hosted index
never mix (the search filters by model).

`GatewayEmbedding` sends texts through the GATEWAY (role `embed`: durable spend ledger, daily ceiling, per-run cap, data-policy filter) as SYNTHETIC data only. The texts it is given are card text (invented
placeholders in this build) or a query that the caller has already MASKED; nothing patient-identifying is meant to travel here and the data class is fixed at synthetic.
"""

import hashlib
import math
import re
import uuid
from collections.abc import Sequence
from typing import Final

from careflow.budget import BudgetExceeded
from careflow.gateway import (
    AllRoutesFailed,
    Gateway,
    NoApprovedRoute,
    SpendCeilingReached,
)
from careflow.ports import (
    AdapterResult,
    DataClass,
    EmbeddingRequest,
    EmbeddingResult,
    PermanentError,
    TransientError,
    Usage,
)

DIMENSIONS: Final = 768
FAKE_MODEL_ID: Final = "fake-hash-768"
MAX_TEXTS: Final = 100  # the hosted batch limit, applied to the fake and the wrapper too
MAX_TEXT_CHARS: Final = 8000
_WORD = re.compile(r"[a-z0-9]+")


def normalise(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if not math.isfinite(norm) or norm == 0:
        raise PermanentError("an embedding with no length cannot be compared")
    return [x / norm for x in vector]


def vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form, `[0.1,0.2,...]`, built from finite floats only (a NaN or infinity is refused: unknown is not a number)."""
    if len(vector) != DIMENSIONS or not all(math.isfinite(x) for x in vector):
        raise PermanentError("a vector must be exactly 768 finite numbers")
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def check_texts(texts: Sequence[str]) -> None:
    if not texts or len(texts) > MAX_TEXTS or any((not isinstance(t, str)) or not t.strip() or len(t) > MAX_TEXT_CHARS for t in texts):
        raise PermanentError("the texts to embed are empty, too many or too long")


class HashingEmbedding:
    """The FAKE embedder: hashed bag of words into 768 buckets (signed, so unrelated words tend to cancel), L2-normalised. Deterministic; no network."""

    model_id = FAKE_MODEL_ID

    async def embed(self, texts: list[str]) -> AdapterResult[EmbeddingResult]:
        check_texts(texts)
        vectors = []
        for t in texts:
            vec = [0.0] * DIMENSIONS
            for word in _WORD.findall(t.lower()):
                digest = hashlib.sha256(word.encode("utf-8")).digest()
                vec[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0 if digest[4] & 1 else -1.0
            if not any(vec):
                vec[0] = 1.0  # a text with no words still gets a (fixed) vector rather than a crash
            vectors.append(normalise(vec))
        return AdapterResult(EmbeddingResult(vectors, DIMENSIONS), Usage(sum(len(t) // 4 + 1 for t in texts), 0))


class GatewayEmbedding:
    """`EmbeddingPort` over the gateway's `embed` role for ONE clinic (the spend is that clinic's). `model_id` is what the hosted row is called, so the index can be filtered by it."""

    def __init__(self, gateway: Gateway, clinic_id: uuid.UUID, model_id: str) -> None:
        self._gateway, self._clinic_id, self.model_id = gateway, clinic_id, model_id

    async def embed(self, texts: list[str]) -> AdapterResult[EmbeddingResult]:
        check_texts(texts)
        try:
            outcome = await self._gateway.call("embed", EmbeddingRequest(tuple(texts), self._clinic_id), data_class=DataClass.SYNTHETIC, clinic_id=self._clinic_id)
        except (NoApprovedRoute, AllRoutesFailed, SpendCeilingReached, BudgetExceeded):
            raise TransientError("the embedding route is unavailable") from None  # the retriever turns this into an abstention (`dense_unavailable`): unknown is not a value
        value = outcome.value
        if not isinstance(value, EmbeddingResult) or value.dimensions != DIMENSIONS or len(value.vectors) != len(texts):
            raise PermanentError("the embed route did not return one 768-number vector per text")
        return AdapterResult(value, outcome.usage)
