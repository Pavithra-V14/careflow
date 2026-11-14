"""Knowledge search: the retrieval mechanism assembled (BUILD-SPEC.md 10.1, 10.7, 10.8, 9.2; CLAUDE.md rules 1, 3, 6, 7). MECHANISM ONLY, over the SYNTHETIC placeholder cards of config/demo-cards.yaml.

What happens, in order, for one question from a signed-in nurse or doctor (or the worker):
  1. the query is MASKED (careflow.text_guard.mask_pii) before any embedding model sees it, even the fake;
  2. it is embedded (the deterministic fake by default; the hosted Gemini embedding through the gateway only when the demo gates and the provider's own gates all hold);
  3. `CardRetriever` (careflow/retrieval.py) runs BM25 (in this process, over the placeholder cards' text) AND the dense search (pgvector, migration 0085: always by clinic, origin, embedding model, CURRENT version
     and the caller's ROLE), fuses them, joins to Postgres truth for the version, and ABSTAINS unless both signals clear the required thresholds. Abstaining is an answer, not an error. A card the caller's
     role may not read never survives the join (its version is not visible to the caller), so the BM25 side cannot leak it;
  4. an optional RERANKER (a port; the fake scores shared words) may reorder the candidates that remain. It can never un-abstain and never adds a card;
  5. the evidence, if wanted, goes through the CONTEXT ASSEMBLER with a DECLARED token budget: what did not fit is dropped from the lowest priority and reported by label.

EVERY threshold is a required setting with no default (CAREFLOW_RETRIEVAL_MIN_DENSE_SIMILARITY, CAREFLOW_RETRIEVAL_MIN_BM25_RATIO, CAREFLOW_CONTEXT_BUDGET_TOKENS); unset means the retriever abstains with `no_calibrated_policy`
and the budget is unknown (nothing is assembled). The demo pack supplies labelled DEMO ONLY values under demo + synthetic_only. Nothing here decides anything clinical: it returns card ids, versions and scores.
"""

import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Final

from sqlalchemy.ext.asyncio import AsyncConnection

from careflow import demo_cards, demo_phase3, demo_real_models
from careflow.adapters.fake import FakeRerank
from careflow.context_assembler import Assembled, assemble
from careflow.demo_cards import DemoCard
from careflow.embeddings import FAKE_MODEL_ID, HashingEmbedding
from careflow.knowledge_sql import PgCardTruth, PgVectorStore
from careflow.ports import (
    EmbeddingPort,
    PermanentError,
    RerankPort,
    RerankRequest,
    TransientError,
)
from careflow.retrieval import (
    AbstentionPolicy,
    Bm25Index,
    CardRetriever,
    Retrieval,
    retrieval_text,
)
from careflow.text_guard import mask_pii

DENSE_SETTING: Final = "CAREFLOW_RETRIEVAL_MIN_DENSE_SIMILARITY"
BM25_SETTING: Final = "CAREFLOW_RETRIEVAL_MIN_BM25_RATIO"
BUDGET_SETTING: Final = "CAREFLOW_CONTEXT_BUDGET_TOKENS"
TOP_K: Final = 3  # how many candidates go on (a tuning value, not a clinical one)
SWITCH: Final = "CAREFLOW_KNOWLEDGE_SEARCH"  # exactly `on`; off by default


def _number(raw: str | None) -> float | None:
    try:
        return float((raw or "").strip())
    except ValueError:
        return None


def policy_from(environ: Mapping[str, str] | None = None) -> AbstentionPolicy | None:
    """The abstention thresholds, or None unless BOTH required settings are set to numbers in range (no default: None makes the retriever abstain with `no_calibrated_policy`)."""
    env = environ if environ is not None else os.environ
    dense, ratio = _number(env.get(DENSE_SETTING)), _number(env.get(BM25_SETTING))
    if dense is None or ratio is None:
        return None
    try:
        return AbstentionPolicy(dense, ratio)
    except ValueError:
        return None


def budget_from(environ: Mapping[str, str] | None = None) -> int | None:
    """The declared token budget for assembled context, or None when unset or not a whole number above zero."""
    raw = ((environ if environ is not None else os.environ).get(BUDGET_SETTING) or "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= 1 else None


@dataclass(frozen=True)
class SearchOutcome:
    retrieval: Retrieval
    embedding_model: str
    reranked: bool = False


async def rerank(retrieval: Retrieval, masked_query: str, texts: Mapping[str, str], reranker: RerankPort) -> tuple[Retrieval, bool]:
    """Reorder the candidates with the reranker. It never adds a card, never un-abstains, and a failure (or a malformed answer) keeps the order it was given: (retrieval, whether it was reordered)."""
    if retrieval.abstained or len(retrieval.candidates) < 2:
        return retrieval, False
    ids = [c.card_id for c in retrieval.candidates]
    try:
        answer = await reranker.rerank(RerankRequest(masked_query, tuple(texts.get(i, "") for i in ids)))
    except (TransientError, PermanentError):
        return retrieval, False
    order = answer.value.order
    if sorted(order) != list(range(len(ids))):
        return retrieval, False  # not a permutation of the candidates: not used
    return replace(retrieval, candidates=tuple(retrieval.candidates[i] for i in order)), order != list(range(len(ids)))


def build_context(instruction: str, retrieval: Retrieval, texts: Mapping[str, str], *, budget_tokens: int | None, patient_facts: Sequence[tuple[str, str]] = ()) -> Assembled | None:
    """The retrieved cards as EVIDENCE in the context assembler under the DECLARED budget (None when no budget is declared: nothing is assembled, unknown is not a number). An abstention gives no evidence."""
    if budget_tokens is None:
        return None
    evidence = [] if retrieval.abstained else [(c.card_id, texts[c.card_id]) for c in retrieval.candidates if c.card_id in texts]
    return assemble(instruction, budget=budget_tokens, patient_facts=patient_facts, evidence=evidence)


class KnowledgeSearch:
    """One per process. `embedder_for(clinic_id)` returns the embedding port and the model id its vectors carry (the index is filtered by the model, so a fake index and a hosted one never mix)."""

    def __init__(
        self, cards: Sequence[DemoCard], embedder_for: Callable[[uuid.UUID], tuple[EmbeddingPort, str]], policy: AbstentionPolicy | None, *, reranker: RerankPort | None = None,
        budget_tokens: int | None = None, top_k: int = TOP_K,
    ) -> None:
        self.cards, self._embedder_for, self.policy, self.reranker, self.budget_tokens, self.top_k = tuple(cards), embedder_for, policy, reranker, budget_tokens, top_k
        self.texts: dict[str, str] = {c.fields.card_id: retrieval_text(c.fields) for c in self.cards}

    def _bm25(self, scope: dict[str, str]) -> Bm25Index:
        return Bm25Index(self.texts, {cid: scope for cid in self.texts}, {c.fields.card_id: c.fields.version for c in self.cards})

    async def search(self, conn: AsyncConnection, clinic_id: uuid.UUID, origin: str, query: str) -> SearchOutcome:
        masked = mask_pii(query).text
        embedder, model_id = self._embedder_for(clinic_id)
        scope = {"clinic_id": str(clinic_id), "origin": origin}
        retriever = CardRetriever(embedder, PgVectorStore(conn, model_id), PgCardTruth(conn, clinic_id=clinic_id, origin=origin, model_id=model_id), self._bm25(scope), self.policy, self.top_k)
        found = await retriever.retrieve(masked, scope=scope)
        if self.reranker is not None:
            found, did = await rerank(found, masked, self.texts, self.reranker)
            return SearchOutcome(found, model_id, did)
        return SearchOutcome(found, model_id)


def from_environment(environ: Mapping[str, str], settings: Mapping[str, str], profile: str, providers: object | None) -> KnowledgeSearch | None:
    """The search over the SYNTHETIC placeholder cards, or None (the route then answers 503). None unless profile=demo AND data_policy=synthetic_only AND CAREFLOW_KNOWLEDGE_SEARCH is exactly `on`: in the clinical
    profile there are no real cards (kb/cards is clinician-owned and empty), so there is nothing it may serve. The embedder is the deterministic FAKE unless the hosted one passed every gate
    (demo_real_models.allowed and the `embed` row built); `settings` is the environment plus the labelled demo pack, so the thresholds are still required settings (unset: the retriever abstains)."""
    if not demo_phase3.demo_allowed(profile, demo_phase3.data_policy(environ)) or (environ.get(SWITCH) or "").strip().lower() != "on":
        return None
    cards, _ = demo_cards.load()
    model_id = getattr(providers, "embedding_model_id", None) if demo_real_models.allowed(environ, profile) else None

    def embedder_for(clinic_id: uuid.UUID) -> tuple[EmbeddingPort, str]:
        if model_id is not None:
            hosted = providers.embedding_for(clinic_id)  # type: ignore[union-attr]
            if hosted is not None:
                return hosted, model_id
        return HashingEmbedding(), FAKE_MODEL_ID

    return KnowledgeSearch(cards, embedder_for, policy_from(settings), reranker=FakeRerank("fake-rerank"), budget_tokens=budget_from(settings))
