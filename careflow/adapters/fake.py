"""Deterministic fake adapters for every port (BUILD-SPEC.md §21: "fake adapters for every external port so the suite runs with no network").

No network, no model, no key, no clock beyond an optional injected latency. Each fake:
  * is deterministic (the same input gives the same output, so contract and routing tests are stable);
  * counts its calls (the model fakes keep no request text; the email/SMS sinks keep what they were asked to send, because a test must be able to
    assert on it, so never point one at real patient text outside a test);
  * can be told to fail: `fail_next(n, error)` makes the next n calls raise, so fallback, retry and breaker tests need no real outage.

These are for tests and for running the product with no service wired. They are NOT a stand-in for clinical judgment: `FakeDecision` picks
by a stable hash and says so in its engine name, and nothing may treat its answer as meaningful.
"""

import asyncio
import hashlib
import math

from careflow.ports import (
    AdapterResult,
    DecisionRequest,
    DecisionResult,
    EmbeddingResult,
    PermanentError,
    RerankRequest,
    RerankResult,
    SendResult,
    TransientError,
    TranslationRequest,
    TranslationResult,
    Usage,
    VectorHit,
    VectorPoint,
    VisionRequest,
    VisionResult,
)


class _Fake:
    def __init__(self, name: str = "fake", *, latency: float = 0.0) -> None:
        self.name, self.latency, self.calls = name, latency, 0
        self._failures: list[Exception] = []

    def fail_next(self, n: int = 1, error: Exception | None = None) -> None:
        self._failures.extend([error or TransientError(f"{self.name} is down")] * n)

    async def _enter(self) -> None:
        self.calls += 1
        if self.latency:
            await asyncio.sleep(self.latency)
        if self._failures:
            raise self._failures.pop(0)


def _tokens(text: str) -> int:
    return max(1, len(text.split()))


class FakeDecision(_Fake):
    """Chooses by a stable hash of (question_type, text, name): deterministic, and different names may disagree, which is what a contract
    test needs to see. `script` maps a question_type to a fixed choice, for tests that need a known answer."""

    def __init__(self, name: str = "fake-decision", *, script: dict[str, str] | None = None, **kw) -> None:
        super().__init__(name, **kw)
        self.script = script or {}

    async def decide(self, request: DecisionRequest) -> AdapterResult[DecisionResult]:
        await self._enter()
        picked = self.script.get(request.question_type)
        if picked is None:
            digest = hashlib.sha256(f"{request.question_type}|{request.text}|{self.name}".encode()).digest()
            picked = request.options[digest[0] % len(request.options)]
        if picked not in request.options:
            raise PermanentError("the scripted choice is not one of the options")
        weights = {o: (3.0 if o == picked else 1.0) for o in request.options}
        total = sum(weights.values())
        probs = {o: w / total for o, w in weights.items()}
        return AdapterResult(DecisionResult(picked, probs, probs[picked], self.name), Usage(_tokens(request.text), 1))


class FakeEmbedding(_Fake):
    DIMENSIONS = 8

    async def embed(self, texts: list[str]) -> AdapterResult[EmbeddingResult]:
        await self._enter()
        vectors = []
        for t in texts:
            raw = hashlib.sha256(t.encode()).digest()[: self.DIMENSIONS]
            vec = [b / 255.0 for b in raw]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            vectors.append([x / norm for x in vec])
        return AdapterResult(EmbeddingResult(vectors, self.DIMENSIONS), Usage(sum(_tokens(t) for t in texts), 0))


class FakeRerank(_Fake):
    """Scores by shared words with the query (ties keep the given order): enough to prove an ordering changes, with no model behind it."""

    async def rerank(self, request: RerankRequest) -> AdapterResult[RerankResult]:
        await self._enter()
        q = set(request.query.lower().split())
        scores = [float(len(q & set(c.lower().split()))) for c in request.candidates]
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        return AdapterResult(RerankResult(order, scores), Usage(_tokens(request.query) + sum(_tokens(c) for c in request.candidates), 0))


class FakeTranslation(_Fake):
    """Tags the text instead of translating it, so a test can see a translation happened and the original is still held."""

    async def translate(self, request: TranslationRequest) -> AdapterResult[TranslationResult]:
        await self._enter()
        return AdapterResult(
            TranslationResult(f"[{request.source}->{request.target}] {request.text}", request.source, request.target),
            Usage(_tokens(request.text), _tokens(request.text)),
        )


class _FakeSink(_Fake):
    """An email or SMS sink: nothing leaves the process. Sends are idempotent on the key (a replay returns the first provider id and does
    not record a second message), exactly what a real adapter must do (§7.2)."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.sent: list[dict] = []
        self._by_key: dict[str, str] = {}

    async def _record(self, record: dict, key: str) -> SendResult:
        await self._enter()
        if not isinstance(key, str) or not key.strip():
            raise PermanentError("an idempotency key is required on every send")
        if key in self._by_key:
            return SendResult(self._by_key[key], replayed=True)
        provider_id = f"{self.name}-{len(self.sent) + 1}"
        self._by_key[key] = provider_id
        self.sent.append(record)
        return SendResult(provider_id)


class FakeEmail(_FakeSink):
    def __init__(self, name: str = "fake-email") -> None:
        super().__init__(name)

    async def send(self, to: str, subject: str, body: str, *, idempotency_key: str) -> SendResult:
        return await self._record({"to": to, "subject": subject, "body": body}, idempotency_key)


class FakeSms(_FakeSink):
    def __init__(self, name: str = "fake-sms") -> None:
        super().__init__(name)

    async def send(self, to: str, body: str, *, idempotency_key: str) -> SendResult:
        return await self._record({"to": to, "body": body}, idempotency_key)


class FakeUpload(_Fake):
    """An in-memory object store behind `UploadPort`. `client_put` plays the BROWSER: it stores whatever it is given, at any path, with whatever
    type it claims and no limit enforced (a hostile client), because the server must re-verify and never rely on a signature's limits."""

    def __init__(self, name: str = "fake-upload", *, now=None) -> None:
        super().__init__(name)
        import datetime as dt

        self._now = now or (lambda: dt.datetime.now(dt.UTC))
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []
        self.signed: list[dict] = []

    def client_put(self, path: str, content: bytes, content_type: str) -> None:
        self.objects[path] = (content, content_type)

    async def create_signed_upload(self, *, path: str, content_type: str, max_bytes: int, expires_in_seconds: int):
        import datetime as dt

        from careflow.ports import SignedUpload

        await self._enter()
        self.signed.append({"path": path, "content_type": content_type, "max_bytes": max_bytes, "expires_in_seconds": expires_in_seconds})
        return SignedUpload(f"fake://upload/{path}", path, self._now() + dt.timedelta(seconds=expires_in_seconds))

    async def describe(self, path: str):
        from careflow.ports import StoredObject

        await self._enter()
        if path not in self.objects:
            return None
        content, ctype = self.objects[path]
        return StoredObject(path, len(content), ctype, content[:16], hashlib.sha256(content).hexdigest())

    async def read(self, path: str):
        await self._enter()
        return self.objects[path][0] if path in self.objects else None

    async def delete(self, path: str) -> None:
        await self._enter()
        self.objects.pop(path, None)
        self.deleted.append(path)


class FakeVectorStore(_Fake):
    """An in-memory card index: exact cosine, pre-filtered by scope. Stands in for Qdrant (no client, no network, no data)."""

    def __init__(self, name: str = "fake-vector-store", **kw) -> None:
        super().__init__(name, **kw)
        self.points: dict[str, VectorPoint] = {}

    async def upsert(self, points: list[VectorPoint]) -> None:
        await self._enter()
        for p in points:
            self.points[p.card_id] = p

    async def delete(self, card_ids: list[str]) -> None:
        await self._enter()
        for c in card_ids:
            self.points.pop(c, None)

    async def search(self, vector: tuple[float, ...], *, limit: int, scope: dict[str, str]) -> list[VectorHit]:
        await self._enter()
        if not scope:
            raise ValueError("a vector search needs a scope (10.7): it never defaults to searching everything")
        norm_q = math.sqrt(sum(x * x for x in vector)) or 1.0
        hits = []
        for p in self.points.values():
            if any(p.scope.get(k) != v for k, v in scope.items()):
                continue
            norm_p = math.sqrt(sum(x * x for x in p.vector)) or 1.0
            hits.append(VectorHit(p.card_id, p.version, sum(a * b for a, b in zip(vector, p.vector, strict=True)) / (norm_q * norm_p)))
        hits.sort(key=lambda h: (-h.similarity, h.card_id))
        return hits[:limit]


class FakeVision(_Fake):
    """Stands in for the vision role (no hosted service is approved). Returns a fixed transcription per page number, or a default tagged with the page; records the pages it was asked
    for so a test can prove the 15.3 cascade (vision only for pages that failed the earlier tiers)."""

    def __init__(self, name: str = "fake-vision", *, script: dict[int, str] | None = None, **kw) -> None:
        super().__init__(name, **kw)
        self.script, self.pages = script or {}, []

    async def read(self, request: VisionRequest) -> VisionResult:
        await self._enter()
        self.pages.append(request.page)
        return VisionResult(self.script.get(request.page, f"Transcribed text of the scanned page {request.page}: findings are as described in the attached letter."))


class FakeLayout(_Fake):
    """Stands in for the layout tier. `script` maps a page number to its text; a page not in it returns None (the layout tier did not help)."""

    def __init__(self, name: str = "fake-layout", *, script: dict[int, str] | None = None, **kw) -> None:
        super().__init__(name, **kw)
        self.script, self.pages = script or {}, []

    async def read_page(self, content: bytes, page: int) -> str | None:
        await self._enter()
        self.pages.append(page)
        return self.script.get(page)
