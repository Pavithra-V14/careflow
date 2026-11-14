"""The `embed` role over Gemini's embedding model (BUILD-SPEC.md 10.1, 5.1). DEMO PROFILE, SYNTHETIC DATA ONLY (owner decision 2026-11-14).

A callable the gateway registers for an `embed` row: `await adapter(EmbeddingRequest) -> AdapterResult[EmbeddingResult]`. The registry row must say `data_policy: synthetic_only`; a PHI request is never offered
to it (spec 16.5). Endpoint, header and request shape come from Google's embeddings documentation of 2026-11-14 (`POST /v1beta/models/<model>:batchEmbedContents`, header `x-goog-api-key`, one item per text
with `model`, `content.parts[].text` and `outputDimensionality` 768; the model re-normalises a truncated vector). The RESPONSE shape (`embeddings[].values`) and whether usage is reported are NOT documented
in the REST examples, so they are Appendix B facts CONFIRMED only by the opt-in live check (`scripts/provider_live_check.py --provider gemini`, the embeddings step): the parser accepts exactly
`embeddings[].values` and refuses anything else; usage is estimated HIGH (unknown is not zero). Every vector is checked (768 finite numbers) and L2-normalised here regardless.

Failures are the fixed words of `provider_http` (never the key, a URL or a body); the provider's own error fields ride on the exception's `provider_detail` for the live check only.
"""

import re
from typing import Final

import httpx

from careflow.adapters.provider_http import (
    new_client,
    post_json,
    refuse_redirecting_client,
)
from careflow.embeddings import DIMENSIONS, check_texts, normalise
from careflow.ports import (
    AdapterResult,
    EmbeddingRequest,
    EmbeddingResult,
    PermanentError,
    Usage,
)

HOST: Final = "https://generativelanguage.googleapis.com"
MODEL_RE: Final = re.compile(r"\Agemini-embedding-[a-z0-9][a-z0-9.\-]{0,30}\Z")


class GeminiEmbeddingAdapter:
    def __init__(self, api_key: str, model_id: str, *, timeout_seconds: float, http: httpx.AsyncClient | None = None) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("a Gemini API key is required")
        if not MODEL_RE.fullmatch(model_id or ""):
            raise ValueError("the Gemini embedding model id is not a plain embedding model name")
        if http is not None:
            refuse_redirecting_client(http)
        self._key, self._model, self._seconds = api_key.strip(), model_id, timeout_seconds
        self._http = http or new_client(timeout_seconds)

    async def __call__(self, request: EmbeddingRequest) -> AdapterResult[EmbeddingResult]:
        check_texts(request.texts)
        body = {
            "requests": [
                {"model": f"models/{self._model}", "content": {"parts": [{"text": t}]}, "outputDimensionality": DIMENSIONS}
                for t in request.texts
            ]
        }
        answer = await post_json(self._http, f"{HOST}/v1beta/models/{self._model}:batchEmbedContents", headers={"x-goog-api-key": self._key}, body=body, seconds=self._seconds)
        vectors = _vectors_of(answer, len(request.texts))
        # Unknown is not zero: no usage is documented for this call, so spend is estimated HIGH (one token per 3 characters).
        return AdapterResult(EmbeddingResult(vectors, DIMENSIONS), Usage(sum(len(t) // 3 + 1 for t in request.texts), 0))


def _vectors_of(answer: dict, expected: int) -> list[list[float]]:
    items = answer.get("embeddings")
    if not isinstance(items, list) or len(items) != expected:
        raise PermanentError("the provider did not return one embedding per text")
    out: list[list[float]] = []
    for item in items:
        values = item.get("values") if isinstance(item, dict) else None
        if not isinstance(values, list) or len(values) != DIMENSIONS or not all(isinstance(x, int | float) and not isinstance(x, bool) for x in values):
            raise PermanentError("an embedding is not exactly 768 numbers")
        out.append(normalise([float(x) for x in values]))
    return out
