"""Card retrieval, the pure-code part (careflow/retrieval.py; BUILD-SPEC.md §10). Offline: a fake vector store and a scripted embedder.

NO CLINICAL CONTENT. The "cards" are synthetic stand-ins about shop hours, parking and the like. No threshold is asserted as right: every
policy number below is a TEST INPUT, and the one thing tested about thresholds is that none exists by default (CLAUDE.md rule 1)."""

import dataclasses
import inspect
from collections.abc import Sequence

import pytest

from careflow.adapters.fake import FakeVectorStore
from careflow.ports import (
    AdapterResult,
    EmbeddingResult,
    TransientError,
    Usage,
    VectorPoint,
)
from careflow.retrieval import (
    AbstentionPolicy,
    Bm25Index,
    CardFields,
    CardRetriever,
    RetrievalError,
    reciprocal_rank_fusion,
    retrieval_text,
)

SCOPE = {"domain": "demo"}

CARDS = {
    "hours": CardFields("hours", 1, "Opening hours", "When the clinic is open", ("open times",), ("when do you open",), ("H1",)),
    "parking": CardFields("parking", 1, "Parking", "Where to park a car", ("car park",), ("where can I park",), ("P1",)),
    "billing": CardFields("billing", 2, "Billing questions", "Invoices and payments", ("invoice",), ("my bill is wrong",), ("B1",)),
}
VERS = {cid: c.version for cid, c in CARDS.items()}
# Orthogonal unit vectors per card: a query vector chosen per test decides the cosine.
VECTORS = {"hours": (1.0, 0.0, 0.0), "parking": (0.0, 1.0, 0.0), "billing": (0.0, 0.0, 1.0)}


class Embedder:
    def __init__(self, vector=(1.0, 0.0, 0.0), fail=False):
        self.vector, self.fail, self.queries = vector, fail, []

    async def embed(self, texts):
        self.queries.append(texts)
        if self.fail:
            raise TransientError("embedding is down")
        return AdapterResult(EmbeddingResult([list(self.vector)], 3), Usage(1, 0))


class Truth:
    def __init__(self, versions):
        self.versions, self.asked = dict(versions), []

    async def current_versions(self, card_ids: Sequence[str]):
        self.asked.append(list(card_ids))
        return {c: v for c, v in self.versions.items() if c in card_ids}


async def build(*, policy, vector=(1.0, 0.0, 0.0), embed_fail=False, truth=None, indexed_versions=None, store_fail=False, bm25_versions=None):
    store = FakeVectorStore()
    await store.upsert(
        [VectorPoint(cid, (indexed_versions or {}).get(cid, c.version), VECTORS[cid], dict(SCOPE)) for cid, c in CARDS.items()]
    )
    if store_fail:
        store.fail_next()
    bm25 = Bm25Index({cid: retrieval_text(c) for cid, c in CARDS.items()}, {cid: dict(SCOPE) for cid in CARDS}, VERS)
    embedder = Embedder(vector, embed_fail)
    truth = truth or Truth({cid: c.version for cid, c in CARDS.items()})
    return CardRetriever(embedder, store, truth, bm25, policy), embedder, truth


OPEN_POLICY = AbstentionPolicy(min_dense_similarity=0.5, min_bm25_ratio=0.1)  # a TEST input, not a recommendation


# ---- the text a card is indexed by --------------------------------------------------------------------------------------------------------


def test_retrieval_text_is_built_from_the_structured_fields_only_and_never_from_rules():
    @dataclasses.dataclass(frozen=True)
    class WithRules(CardFields):
        rules: str = "SENTINEL-RULE-TEXT-55"
        internal_note: str = "SENTINEL-NOTE-55"

    text = retrieval_text(WithRules("c", 1, "Title", "Desc", ("syn",), ("phrase",), ("code",)))
    assert text == "Title\nDesc\nsyn\nphrase\ncode" and "SENTINEL" not in text
    assert "rules" not in {f.name for f in dataclasses.fields(CardFields)}
    assert "rules" not in inspect.getsource(retrieval_text)


# ---- BM25 and fusion ----------------------------------------------------------------------------------------------------------------------


def test_bm25_ranks_the_card_that_shares_the_query_words_first_and_ignores_cards_that_share_none():
    index = Bm25Index({cid: retrieval_text(c) for cid, c in CARDS.items()}, {cid: dict(SCOPE) for cid in CARDS}, VERS)
    ranked = index.search("when do you open", SCOPE)
    assert ranked[0][0] == "hours" and [c for c, _ in ranked].count("billing") == 0
    assert index.search("zzzz qqqq", SCOPE) == []
    assert 0 < ranked[0][1] / index.ceiling("when do you open") <= 1


def test_bm25_applies_the_scope_before_ranking_and_refuses_an_empty_scope():
    scopes = {"hours": {"domain": "demo"}, "parking": {"domain": "other"}, "billing": {"domain": "demo"}}
    index = Bm25Index({cid: retrieval_text(c) for cid, c in CARDS.items()}, scopes, VERS)
    assert "parking" not in [c for c, _ in index.search("park car", {"domain": "demo"})]
    with pytest.raises(RetrievalError):
        index.search("park", {})
    with pytest.raises(RetrievalError):
        Bm25Index({"a": "x"}, {}, {})


def test_rrf_is_the_textbook_sum_of_reciprocal_ranks_and_ties_break_by_id():
    fused = dict(reciprocal_rank_fusion([["a", "b", "c"], ["b", "a"]]))
    assert fused["a"] == pytest.approx(1 / 61 + 1 / 62) and fused["b"] == pytest.approx(1 / 62 + 1 / 61) and fused["c"] == pytest.approx(1 / 63)
    assert [c for c, _ in reciprocal_rank_fusion([["b", "a"], ["a", "b"]])] == ["a", "b"]  # equal scores: by id
    assert [c for c, _ in reciprocal_rank_fusion([["x", "x", "y"]])] == ["x", "y"]  # a repeated id counts once
    assert reciprocal_rank_fusion([]) == [] and reciprocal_rank_fusion([[], []]) == []


# ---- retrieval, abstention, the join -------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_confident_match_returns_the_card_with_its_absolute_signals():
    retriever, embedder, _ = await build(policy=OPEN_POLICY)
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert not result.abstained and result.reason is None
    top = result.candidates[0]
    assert (top.card_id, top.version) == ("hours", 1) and top.dense_similarity == pytest.approx(1.0) and top.bm25_ratio > 0.1
    assert embedder.queries == [["when do you open"]]


@pytest.mark.asyncio
async def test_there_is_no_default_threshold_and_no_policy_means_abstain_not_retrieve():
    assert all(f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING for f in dataclasses.fields(AbstentionPolicy))
    with pytest.raises(TypeError):
        AbstentionPolicy()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        CardRetriever(Embedder(), FakeVectorStore(), Truth({}), Bm25Index({}, {}, {}))  # type: ignore[call-arg]
    retriever, embedder, _ = await build(policy=None)
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert (result.abstained, result.reason, result.candidates) == (True, "no_calibrated_policy", ())
    assert embedder.queries == []  # nothing was embedded: the query is not sent anywhere without a policy


@pytest.mark.asyncio
async def test_a_policy_out_of_range_is_refused():
    for bad in ((1.5, 0.1), (-1.5, 0.1), (0.5, -0.1), (0.5, 1.1)):
        with pytest.raises(ValueError):
            AbstentionPolicy(*bad)


@pytest.mark.parametrize(
    ("policy", "vector"),
    [
        (AbstentionPolicy(0.9, 0.0), (0.6, 0.8, 0.0)),  # dense similarity of the top card is 0.6, below 0.9
        (AbstentionPolicy(0.0, 0.99), (1.0, 0.0, 0.0)),  # bm25 ratio is below 0.99
    ],
)
@pytest.mark.asyncio
async def test_below_the_threshold_abstains_and_returns_nothing_even_though_candidates_exist(policy, vector):
    retriever, *_ = await build(policy=policy, vector=vector)
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert (result.abstained, result.reason, result.candidates) == (True, "below_threshold", ())


@pytest.mark.asyncio
async def test_a_query_that_matches_nothing_abstains():
    retriever, *_ = await build(policy=AbstentionPolicy(-1.0, 0.0), vector=(0.0, 0.0, 0.0))
    result = await retriever.retrieve("zzzz", scope=SCOPE)
    assert result.abstained and result.candidates == ()
    assert (await retriever.retrieve("   ", scope=SCOPE)).reason == "no_candidates"


@pytest.mark.parametrize("kwargs", [{"embed_fail": True}, {"store_fail": True}])
@pytest.mark.asyncio
async def test_when_the_dense_side_is_down_the_dense_signal_is_unknown_so_it_abstains_not_retrieves_on_bm25_alone(kwargs):
    retriever, *_ = await build(policy=AbstentionPolicy(-1.0, 0.0), **kwargs)  # even a policy that would accept anything
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert (result.abstained, result.reason, result.candidates) == (True, "dense_unavailable", ())


@pytest.mark.asyncio
async def test_a_hit_whose_version_is_not_the_current_one_in_postgres_is_discarded_at_the_join():
    truth = Truth({"hours": 2, "parking": 1, "billing": 2})  # Postgres moved hours to version 2; the index still holds version 1
    retriever, *_ = await build(policy=OPEN_POLICY, truth=truth)
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert all(c.card_id != "hours" for c in result.candidates)
    assert result.stale_discarded >= 1
    assert truth.asked  # the join really asked Postgres


@pytest.mark.asyncio
async def test_a_card_that_is_no_longer_retrievable_is_discarded_and_if_nothing_is_left_it_abstains():
    retriever, *_ = await build(policy=OPEN_POLICY, truth=Truth({}))
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert (result.abstained, result.reason, result.candidates) == (True, "all_stale", ())
    assert result.stale_discarded >= 1


@pytest.mark.asyncio
async def test_a_stale_top_hit_is_never_returned_even_if_it_scored_best():
    truth = Truth({"hours": 1, "parking": 1, "billing": 2})
    retriever, *_ = await build(policy=AbstentionPolicy(-1.0, 0.0), truth=truth, indexed_versions={"hours": 0})
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert "hours" not in [c.card_id for c in result.candidates]


@pytest.mark.asyncio
async def test_a_retrieval_without_a_scope_raises_and_the_store_refuses_one_too():
    retriever, embedder, _ = await build(policy=OPEN_POLICY)
    with pytest.raises(RetrievalError):
        await retriever.retrieve("when do you open", scope={})
    assert embedder.queries == []
    with pytest.raises(ValueError):
        await FakeVectorStore().search((1.0, 0.0, 0.0), limit=3, scope={})


@pytest.mark.asyncio
async def test_the_vector_store_prefilters_by_scope():
    store = FakeVectorStore()
    await store.upsert([VectorPoint("a", 1, (1.0, 0.0), {"domain": "x"}), VectorPoint("b", 1, (1.0, 0.0), {"domain": "y"})])
    assert [h.card_id for h in await store.search((1.0, 0.0), limit=5, scope={"domain": "x"})] == ["a"]


@pytest.mark.asyncio
async def test_the_query_never_appears_in_a_repr():
    retriever, *_ = await build(policy=OPEN_POLICY)
    result = await retriever.retrieve("SENTINEL-QUERY-31 when do you open", scope=SCOPE)
    assert "SENTINEL" not in repr(result) and "SENTINEL" not in repr(retriever)


@pytest.mark.asyncio
async def test_a_card_whose_bm25_text_is_stale_is_discarded_even_if_its_vector_is_current():
    truth = Truth({"hours": 1, "parking": 1, "billing": 3})
    retriever, *_ = await build(policy=AbstentionPolicy(-1.0, 0.0), truth=truth, bm25_versions={"hours": 1, "parking": 1, "billing": 2})
    result = await retriever.retrieve("my bill is wrong invoice", scope=SCOPE)
    assert "billing" not in [c.card_id for c in result.candidates] and result.stale_discarded >= 1


@pytest.mark.asyncio
async def test_a_nan_signal_is_unknown_and_abstains():
    retriever, *_ = await build(policy=AbstentionPolicy(-1.0, 0.0), vector=(float("nan"), 0.0, 0.0))
    result = await retriever.retrieve("when do you open", scope=SCOPE)
    assert result.abstained and result.candidates == ()


def test_a_message_never_carries_the_query():
    assert "query" not in str(RetrievalError("a retrieval needs a scope (§10.7)"))
