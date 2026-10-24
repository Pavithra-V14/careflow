"""Protocol-card retrieval, the pure-code part (BUILD-SPEC.md §10.1, §10.2, §10.7, §10.8; CLAUDE.md rules 1, 3, 6). Package P2-6 (code only).

NO CARD CONTENT lives here or in the tests. Cards are clinician-authored; every card in a test is a synthetic, non-clinical stand-in
("Opening hours", "Parking") with no medical meaning.

What is here
  * `retrieval_text(card)`: the text a card is indexed by, built ONLY from its structured fields (title, description, synonyms, patient phrases,
    codes). A card's rules are evaluated, never searched, so they are not an input: `CardFields` has no rules field and the builder reads nothing else.
  * `Bm25Index`: BM25 in our own code over those texts (the standard Robertson form; k1 and b are the textbook parameters, not tuned).
  * `reciprocal_rank_fusion`: parameter-free apart from the literature constant k (60); a tie breaks by card id so the result is deterministic.
  * `CardRetriever.retrieve`: BM25 + dense (through `EmbeddingPort` and `VectorStorePort`, both faked offline) -> RRF -> VERSION-CHECKED JOIN to
    Postgres truth -> abstention.

Safety properties, each tested
  * ABSTENTION IS AN OUTCOME, NOT AN ERROR (§10.8), and there is NO DEFAULT THRESHOLD. `AbstentionPolicy` has two required numbers with no default;
    the retriever's `policy` field has no default either, and `None` means "not calibrated yet": it abstains with reason `no_calibrated_policy`.
    The numbers need a calibrated value from the gold set (§15.1), set by a clinical lead. The dense similarity and the BM25 ratio are ABSOLUTE
    signals (a rank-based fused score is not: a weak candidate that tops both lists would look perfect).
  * Unknown is not a value. If the embedding service or the vector store is down, the dense similarity of the top candidate is UNKNOWN, a policy
    that needs it cannot be satisfied, and the retriever abstains (`dense_unavailable`) instead of retrieving on BM25 alone.
  * The index is disposable and Postgres is truth (§10.8): a hit whose version is not the current version in Postgres, or whose card is no
    longer retrievable, is DISCARDED at the join, and counted so a stale index is visible.
  * A search without a scope raises (§10.7): the permission/metadata filter is a pre-filter that is never optional.
  * Nothing here logs; the query is patient text and appears in no repr or exception. NOTE for the caller: the dense query goes through the `embed`
    role, so it must travel a route whose data policy allows patient text (the gateway decides).

Known deviation from §10.1, stated: BM25 is computed in this process over the cards' texts and fused here, not stored as Qdrant sparse vectors with
server-side RRF. For a card corpus of tens to hundreds of units this is exact, avoids per-card IDF drift on a partial reindex, and keeps the abstention
signal visible. The sparse-vector layout can replace it if the corpus grows; `reciprocal_rank_fusion` is the reference the server-side result is tested against.
"""

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from careflow.grounding import words
from careflow.ports import EmbeddingPort, TransientError, VectorHit, VectorStorePort

BM25_K1: Final = 1.5  # textbook defaults (Robertson and Zaragoza); algorithmic, not clinical
BM25_B: Final = 0.75
RRF_K: Final = 60  # the constant from the original RRF paper; fixed, not tuned
CANDIDATES_PER_LIST: Final = 20


class RetrievalError(Exception):
    """Fixed words only; never the query."""


@dataclass(frozen=True)
class CardFields:
    """The ONLY card fields retrieval may read (§10.2). There is deliberately no `rules` field."""

    card_id: str
    version: int
    title: str
    description: str = ""
    synonyms: tuple[str, ...] = ()
    patient_phrases: tuple[str, ...] = ()
    codes: tuple[str, ...] = ()


def retrieval_text(card: CardFields) -> str:
    parts = [card.title, card.description, *card.synonyms, *card.patient_phrases, *card.codes]
    return "\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())


# ---- BM25 --------------------------------------------------------------------------------------------------------------------------------


class Bm25Index:
    def __init__(self, texts: Mapping[str, str], scopes: Mapping[str, Mapping[str, str]], versions: Mapping[str, int], *, k1: float = BM25_K1, b: float = BM25_B) -> None:
        if set(scopes) != set(texts) or set(versions) != set(texts):
            raise RetrievalError("every indexed card needs a scope and a version")
        self._k1, self._b, self._scopes, self.versions = k1, b, scopes, dict(versions)
        self._tf = {cid: Counter(words(t)) for cid, t in texts.items()}
        self._len = {cid: sum(c.values()) for cid, c in self._tf.items()}
        self._n = len(texts)
        self._avg = (sum(self._len.values()) / self._n) if self._n else 0.0
        df: Counter[str] = Counter()
        for c in self._tf.values():
            df.update(c.keys())
        self._idf = {t: math.log(1 + (self._n - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def ceiling(self, query: str) -> float:
        """The most a document could score for this query (every query term saturated): used to express a score as a 0..1 ratio."""
        return sum(self._idf.get(t, 0.0) * (self._k1 + 1) for t in set(words(query)))

    def search(self, query: str, scope: Mapping[str, str], limit: int = CANDIDATES_PER_LIST) -> list[tuple[str, float]]:
        """Only cards whose scope matches EVERY item of `scope` are scored: the filter is applied before ranking, as for the vector store (§10.7)."""
        if not scope:
            raise RetrievalError("a search needs a scope (§10.7)")
        terms = set(words(query))
        scored: list[tuple[str, float]] = []
        for cid, tf in self._tf.items():
            if any(self._scopes[cid].get(k) != v for k, v in scope.items()):
                continue
            norm = 1 - self._b + self._b * (self._len[cid] / self._avg) if self._avg else 1.0
            score = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    score += self._idf[t] * (f * (self._k1 + 1)) / (f + self._k1 * norm)
            if score > 0:
                scored.append((cid, score))
        scored.sort(key=lambda p: (-p[1], p[0]))
        return scored[:limit]


# ---- fusion ------------------------------------------------------------------------------------------------------------------------------


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], *, k: int = RRF_K) -> list[tuple[str, float]]:
    """score(d) = sum over rankings of 1 / (k + rank), rank starting at 1. Ties break by id."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for rank, cid in enumerate(ranking, start=1):
            if cid in seen:
                continue
            seen.add(cid)
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda p: (-p[1], p[0]))


# ---- abstention and the join -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AbstentionPolicy:
    """Both numbers are REQUIRED and have no default: they need a calibrated value from the gold set, chosen by a clinical lead (§10.8, §15.1).
    `min_dense_similarity`: cosine of the top candidate. `min_bm25_ratio`: its BM25 score over the best score the query could earn, in [0, 1]."""

    min_dense_similarity: float
    min_bm25_ratio: float

    def __post_init__(self) -> None:
        if not (-1.0 <= self.min_dense_similarity <= 1.0) or not (0.0 <= self.min_bm25_ratio <= 1.0):
            raise ValueError("abstention thresholds are out of range")


class CardTruth(Protocol):
    """Postgres is truth (§10.8): the current version of each card that may be retrieved. A card absent from the result (unpublished, retired,
    unknown) is not retrievable. What 'retrievable' means is the card lifecycle's business, not retrieval's."""

    async def current_versions(self, card_ids: Sequence[str]) -> Mapping[str, int]: ...


AbstainReason = Literal["no_calibrated_policy", "no_candidates", "dense_unavailable", "below_threshold", "all_stale"]


@dataclass(frozen=True)
class Candidate:
    card_id: str
    version: int
    fused_score: float
    dense_similarity: float | None = None
    bm25_ratio: float | None = None


@dataclass(frozen=True)
class Retrieval:
    """Only the TOP candidate is gated by the policy; ranks 2 and 3 are context for a human or a reranker, never confident matches (their signals may be None).
    `candidates` is empty when `abstained`: a valid outcome that routes to a human. `stale_discarded` counts hits dropped at the join."""

    abstained: bool
    reason: AbstainReason | None
    candidates: tuple[Candidate, ...] = ()
    stale_discarded: int = 0


@dataclass
class CardRetriever:
    embedder: EmbeddingPort
    store: VectorStorePort
    truth: CardTruth
    bm25: Bm25Index
    policy: AbstentionPolicy | None = field(repr=False)  # no default on purpose: None must be written down by the caller
    top_k: int = 3  # tuning values, not clinical: how many candidates go on to the next stage

    async def retrieve(self, query: str, *, scope: dict[str, str]) -> Retrieval:
        if not scope:
            raise RetrievalError("a retrieval needs a scope (§10.7)")
        if not isinstance(query, str) or not query.strip():
            return Retrieval(True, "no_candidates")
        if self.policy is None:
            return Retrieval(True, "no_calibrated_policy")

        sparse = self.bm25.search(query, scope)
        dense: list[VectorHit] | None
        try:
            embedded = await self.embedder.embed([query])
            dense = await self.store.search(tuple(embedded.value.vectors[0]), limit=CANDIDATES_PER_LIST, scope=scope)
        except TransientError:
            dense = None  # unknown, not empty
        if dense is None:
            return Retrieval(True, "dense_unavailable")

        fused = reciprocal_rank_fusion([[cid for cid, _ in sparse], [h.card_id for h in dense]])
        if not fused:
            return Retrieval(True, "no_candidates")
        versions = {h.card_id: h.version for h in dense}
        bm25_scores = dict(sparse)
        sims = {h.card_id: h.similarity for h in dense}
        current = await self.truth.current_versions([cid for cid, _ in fused])
        ceiling = self.bm25.ceiling(query)

        kept: list[Candidate] = []
        stale = 0
        for cid, score in fused:
            # Every version the index side knows (BM25 text and vector) must be the current one, or the hit is stale.
            known = {v for v in (versions.get(cid), self.bm25.versions.get(cid)) if v is not None}
            if cid not in current or any(v != current[cid] for v in known):
                stale += 1  # the index is behind Postgres, or the card is no longer retrievable: discard, never return it
                continue
            ratio = (bm25_scores[cid] / ceiling) if cid in bm25_scores and ceiling > 0 else None
            kept.append(Candidate(cid, current[cid], score, sims.get(cid), ratio))
        if not kept:
            return Retrieval(True, "all_stale", stale_discarded=stale)
        top = kept[0]
        # A signal that is missing for the top candidate is UNKNOWN, and an unknown signal cannot satisfy the policy.
        # Written so that NaN (an unknown number) fails: `not (x >= threshold)`, never `x < threshold`.
        if (
            top.dense_similarity is None
            or top.bm25_ratio is None
            or not math.isfinite(top.dense_similarity)
            or not math.isfinite(top.bm25_ratio)
            or not (top.dense_similarity >= self.policy.min_dense_similarity)
            or not (top.bm25_ratio >= self.policy.min_bm25_ratio)
        ):
            return Retrieval(True, "below_threshold", stale_discarded=stale)
        return Retrieval(False, None, tuple(kept[: self.top_k]), stale)
