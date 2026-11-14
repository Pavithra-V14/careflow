"""The retrieval-quality harness (BUILD-SPEC.md 10.8, 15.1; CLAUDE.md rules 1, 3). Offline, with the FAKE embedder and the in-memory vector store, over the SYNTHETIC placeholder cards and their made-up questions.

It measures, per question set: recall@k (is the expected card among the first k ranked), MRR (how high it ranks), and, with the abstention policy set, how often the retriever abstained and how often it ANSWERED WITH
THE WRONG CARD. Ranking is measured WITHOUT the abstention policy (dense search and BM25 fused, nothing thresholded), so a ranking number never depends on a threshold; the abstention numbers use the policy and
exist only when it is set.

EVERY threshold is a required setting with NO default (CAREFLOW_RETRIEVAL_EVAL_K, CAREFLOW_RETRIEVAL_EVAL_MIN_RECALL, CAREFLOW_RETRIEVAL_EVAL_MIN_MRR). With any of them unset the report says NOT MEASURED and no
pass or fail is claimed (unknown is not a value). The numbers prove the MECHANISM on invented text with a hashing fake: they say nothing about real cards or a real embedding model, and a real corpus needs a
clinician-reviewed question set (the gold set, 15.1) before any threshold is chosen.
"""

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from careflow.adapters.fake import FakeVectorStore
from careflow.demo_cards import DemoCard, DemoQuery
from careflow.ports import EmbeddingPort, VectorPoint
from careflow.retrieval import (
    AbstentionPolicy,
    Bm25Index,
    CardRetriever,
    reciprocal_rank_fusion,
    retrieval_text,
)

K_SETTING: Final = "CAREFLOW_RETRIEVAL_EVAL_K"
RECALL_SETTING: Final = "CAREFLOW_RETRIEVAL_EVAL_MIN_RECALL"
MRR_SETTING: Final = "CAREFLOW_RETRIEVAL_EVAL_MIN_MRR"
SCOPE: Final = {"domain": "eval"}


@dataclass(frozen=True)
class EvalThresholds:
    k: int
    min_recall_at_k: float
    min_mrr: float


def thresholds_from(environ: Mapping[str, str] | None = None) -> EvalThresholds | None:
    """The three required settings, or None unless ALL are present and in range (k a whole number from 1, the others numbers in (0, 1])."""
    env = environ if environ is not None else os.environ
    try:
        raw_k = (env.get(K_SETTING) or "").strip()
        k, recall, mrr = int(raw_k), float((env.get(RECALL_SETTING) or "").strip()), float((env.get(MRR_SETTING) or "").strip())
    except ValueError:
        return None
    if k < 1 or not (0.0 < recall <= 1.0) or not (0.0 < mrr <= 1.0):
        return None
    return EvalThresholds(k, recall, mrr)


@dataclass
class EvalReport:
    queries: int
    k: int | None
    recall_at_k: float | None
    mrr: float | None
    abstained: int | None  # None: no abstention policy was set, so it was not measured
    answered_wrong: int | None
    status: str  # NOT MEASURED | PASS | FAIL
    reasons: list[str] = field(default_factory=list)


class OfflineWorld:
    """Fake embedder, in-memory vector store and BM25 over the cards: the same CardRetriever as the real path, minus the database."""

    def __init__(self, cards: Sequence[DemoCard], embedder: EmbeddingPort, store: FakeVectorStore, bm25: Bm25Index, truth: "MemoryTruth") -> None:
        self.cards, self.embedder, self.store, self.bm25, self.truth = tuple(cards), embedder, store, bm25, truth

    @classmethod
    async def build(cls, cards: Sequence[DemoCard], embedder: EmbeddingPort) -> "OfflineWorld":
        texts = {c.fields.card_id: retrieval_text(c.fields) for c in cards}
        embedded = await embedder.embed(list(texts.values()))
        store = FakeVectorStore()
        await store.upsert([VectorPoint(c.fields.card_id, c.fields.version, tuple(v), dict(SCOPE)) for c, v in zip(cards, embedded.value.vectors, strict=True)])
        bm25 = Bm25Index(texts, {cid: SCOPE for cid in texts}, {c.fields.card_id: c.fields.version for c in cards})
        return cls(cards, embedder, store, bm25, MemoryTruth({c.fields.card_id: c.fields.version for c in cards}))

    async def rank(self, query: str, limit: int = 20) -> list[str]:
        """Dense and BM25 fused with NO abstention threshold: the ranking alone."""
        embedded = await self.embedder.embed([query])
        dense = await self.store.search(tuple(embedded.value.vectors[0]), limit=limit, scope=dict(SCOPE))
        sparse = self.bm25.search(query, SCOPE)
        return [cid for cid, _ in reciprocal_rank_fusion([[c for c, _ in sparse], [h.card_id for h in dense]])]

    def retriever(self, policy: AbstentionPolicy | None) -> CardRetriever:
        return CardRetriever(self.embedder, self.store, self.truth, self.bm25, policy)


class MemoryTruth:
    def __init__(self, versions: Mapping[str, int]) -> None:
        self._versions = dict(versions)

    async def current_versions(self, card_ids: Sequence[str]) -> Mapping[str, int]:
        return {c: self._versions[c] for c in card_ids if c in self._versions}


async def evaluate(world: OfflineWorld, queries: Sequence[DemoQuery], *, thresholds: EvalThresholds | None, policy: AbstentionPolicy | None) -> EvalReport:
    if not queries:
        return EvalReport(0, None, None, None, None, None, "NOT MEASURED", ["no questions"])
    if thresholds is None:
        return EvalReport(len(queries), None, None, None, None, None, "NOT MEASURED", ["the evaluation thresholds are required settings and are not all set"])
    hits, reciprocal = 0, 0.0
    for q in queries:
        ranked = await world.rank(q.text)
        if q.expect in ranked[: thresholds.k]:
            hits += 1
        if q.expect in ranked:
            reciprocal += 1.0 / (ranked.index(q.expect) + 1)
    recall, mrr = hits / len(queries), reciprocal / len(queries)
    abstained: int | None = None
    wrong: int | None = None
    if policy is not None:
        abstained = wrong = 0
        retriever = world.retriever(policy)
        for q in queries:
            got = await retriever.retrieve(q.text, scope=dict(SCOPE))
            if got.abstained:
                abstained += 1
            elif got.candidates[0].card_id != q.expect:
                wrong += 1
    reasons = []
    if not (recall >= thresholds.min_recall_at_k):
        reasons.append("recall_below_threshold")
    if not (mrr >= thresholds.min_mrr):
        reasons.append("mrr_below_threshold")
    return EvalReport(len(queries), thresholds.k, recall, mrr, abstained, wrong, "FAIL" if reasons else "PASS", reasons)
