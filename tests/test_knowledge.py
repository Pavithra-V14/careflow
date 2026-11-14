"""The retrieval mechanism, OFFLINE (migration 0085's Python side): the fake embedder, the Gemini embedding adapter (recorded-shape fake transport, no network), the `embed` role behind the gateway and its gates, the
synthetic placeholder cards, the pgvector store's SQL calls (a recording connection), the reranker port, the context assembler wiring with a declared budget, the quality harness with every threshold a required
setting, the route's gating, and a static read of the migration. NO REAL PROVIDER IS CALLED. The database half is tests/test_knowledge_db_live.py. Synthetic, invented words only."""

import asyncio
import math
import re
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import yaml

from careflow import demo_cards, knowledge, retrieval_eval
from careflow.adapters.fake import FakeRerank
from careflow.adapters.gemini_embed import GeminiEmbeddingAdapter
from careflow.context_assembler import BudgetTooSmall
from careflow.embeddings import (
    DIMENSIONS,
    FAKE_MODEL_ID,
    GatewayEmbedding,
    HashingEmbedding,
    vector_literal,
)
from careflow.gateway import (
    Gateway,
    InMemorySpendLedger,
    ModelRow,
    SpendCeilingReached,
    SpendLimits,
)
from careflow.knowledge_sql import (
    KnowledgeError,
    PgCardTruth,
    PgVectorStore,
    Refused,
    VersionConflict,
)
from careflow.ports import (
    AdapterResult,
    EmbeddingRequest,
    EmbeddingResult,
    PermanentError,
    RerankResult,
    TransientError,
    Usage,
    VectorPoint,
)
from careflow.provider_setup import KEY_VARIABLES, build_providers
from careflow.retrieval import AbstentionPolicy, Candidate, Retrieval
from tests.test_providers import CLINIC, KEY, Mock, good_env, json_reply

ROOT = Path(__file__).resolve().parent.parent
CLINIC_ID = uuid.uuid4()


def embed_ok(n=2, dims=DIMENSIONS, value=0.5):
    return {"embeddings": [{"values": [value] * dims} for _ in range(n)]}


# ---- the fake embedder ---------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_fake_embedder_is_deterministic_768_unit_length_and_puts_shared_words_closer():
    e = HashingEmbedding()
    a = await e.embed(["the zorblat widget goes back", "the zorblat widget returns", "a flimmer lamp in the corridor"])
    assert a.value.dimensions == DIMENSIONS == 768 and all(len(v) == 768 for v in a.value.vectors)
    assert all(abs(math.sqrt(sum(x * x for x in v)) - 1) < 1e-9 for v in a.value.vectors)
    again = await e.embed(["the zorblat widget goes back"])
    assert again.value.vectors[0] == a.value.vectors[0]
    dot = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))
    assert dot(a.value.vectors[0], a.value.vectors[1]) > dot(a.value.vectors[0], a.value.vectors[2])
    assert e.model_id == FAKE_MODEL_ID != "gemini-embedding-2"  # a fake index and a hosted one never mix


@pytest.mark.asyncio
async def test_the_fake_embedder_refuses_empty_too_many_or_too_long_texts_and_a_vector_literal_refuses_bad_numbers():
    e = HashingEmbedding()
    for bad in ([], [""], ["   "], ["x"] * 101, ["x" * 8001]):
        with pytest.raises(PermanentError):
            await e.embed(bad)
    for bad in ([0.0] * 767, [0.0] * 769, [float("nan")] + [0.0] * 767, [float("inf")] + [0.0] * 767):
        with pytest.raises(PermanentError):
            vector_literal(bad)
    assert vector_literal([0.5] * 768).startswith("[0.5,0.5") and vector_literal([0.5] * 768).endswith("]")


# ---- the Gemini embedding adapter ---------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_adapter_sends_one_item_per_text_with_the_model_and_768_dimensions_and_normalises_the_answer():
    mock = Mock(json_reply(embed_ok(2)))
    adapter = GeminiEmbeddingAdapter(KEY, "gemini-embedding-2", timeout_seconds=20, http=mock.client())
    out = await adapter(EmbeddingRequest(("alpha text", "beta text"), CLINIC))
    (req,) = mock.requests
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-2:batchEmbedContents" and req.headers["x-goog-api-key"] == KEY
    body = __import__("json").loads(req.content)
    assert [r["model"] for r in body["requests"]] == ["models/gemini-embedding-2"] * 2 and all(r["outputDimensionality"] == 768 for r in body["requests"])
    assert [r["content"]["parts"][0]["text"] for r in body["requests"]] == ["alpha text", "beta text"]
    assert out.value.dimensions == 768 and len(out.value.vectors) == 2 and abs(math.sqrt(sum(x * x for x in out.value.vectors[0])) - 1) < 1e-9
    assert out.usage.input_tokens > 0 and out.usage.output_tokens == 0  # estimated HIGH: no usage is documented for this call


@pytest.mark.asyncio
async def test_the_adapter_refuses_a_wrong_count_wrong_size_or_non_numeric_answer_and_never_leaks_the_key():
    for body in ({"embeddings": [{"values": [0.1] * 768}]}, embed_ok(2, dims=767), {"embeddings": [{"values": ["x"] * 768}] * 2}, {"embedding": {"values": [0.1] * 768}}, {"embeddings": "no"}, {"embeddings": [{"values": [True] * 768}] * 2}):
        adapter = GeminiEmbeddingAdapter(KEY, "gemini-embedding-2", timeout_seconds=20, http=Mock(json_reply(body)).client())
        with pytest.raises(PermanentError) as caught:
            await adapter(EmbeddingRequest(("a", "b"), CLINIC))
        assert KEY not in f"{caught.value!s} {caught.value!r}"
    zero = GeminiEmbeddingAdapter(KEY, "gemini-embedding-2", timeout_seconds=20, http=Mock(json_reply(embed_ok(1, value=0.0))).client())
    with pytest.raises(PermanentError):
        await zero(EmbeddingRequest(("a",), CLINIC))  # a vector with no length cannot be compared
    for status, kind in ((429, TransientError), (503, TransientError), (401, PermanentError), (404, PermanentError)):
        adapter = GeminiEmbeddingAdapter(KEY, "gemini-embedding-2", timeout_seconds=20, http=Mock(json_reply({"error": {"message": f"echo {KEY}"}}, status)).client())
        with pytest.raises(kind) as caught:
            await adapter(EmbeddingRequest(("a",), CLINIC))
        assert KEY not in f"{caught.value!s} {caught.value!r}"


def test_the_adapter_refuses_a_key_a_model_that_is_not_an_embedding_model_and_a_redirecting_client():
    with pytest.raises(ValueError):
        GeminiEmbeddingAdapter("  ", "gemini-embedding-2", timeout_seconds=20)
    for model in ("gemini-2.5-flash", "../x", "gemini-embedding-2/../y", "gpt-4", "", "gemini-embedding-2\n"):
        with pytest.raises(ValueError):
            GeminiEmbeddingAdapter(KEY, model, timeout_seconds=20)
    with pytest.raises(ValueError):
        GeminiEmbeddingAdapter(KEY, "gemini-embedding-2", timeout_seconds=20, http=httpx.AsyncClient(follow_redirects=True))


# ---- the embed role behind the gateway and its gates ---------------------------------------------------------------------------------------------


def embed_env(**extra):
    return good_env(CAREFLOW_SPEND_CEILING_EMBED_USD="1.00", **extra)


def test_the_embed_role_is_built_only_when_every_gate_holds_and_says_why_otherwise():
    built = build_providers(embed_env(), profile="demo", http=Mock(json_reply(embed_ok(1))).client())
    assert built.embedding_model_id == "gemini-embedding-2" and built.embedding_for(CLINIC_ID) is not None and "gemini-embed" not in built.unavailable
    for env, profile, why in (
        (embed_env(CAREFLOW_ENV="ci"), "demo", "CI"), (embed_env(), "clinical", "profile=demo"), (embed_env(CAREFLOW_DATA_POLICY="phi_approved"), "demo", "synthetic_only"),
        (embed_env(CAREFLOW_DATA_POLICY=None), "demo", "synthetic_only"),
    ):
        got = build_providers(env, profile=profile)
        assert got.embedding_model_id is None and got.embedding_for(CLINIC_ID) is None and got.gateway is None, (profile, why)
    no_ceiling = build_providers(good_env(CAREFLOW_SPEND_CEILING_EMBED_USD=None), profile="demo")
    assert no_ceiling.embedding_for(CLINIC_ID) is None and "CAREFLOW_SPEND_CEILING_EMBED_USD" in no_ceiling.unavailable["gemini-embed"] and no_ceiling.vision is not None  # vision still built
    no_key = build_providers(embed_env(**{KEY_VARIABLES["gemini"]: None}), profile="demo")
    assert no_key.embedding_for(CLINIC_ID) is None and KEY_VARIABLES["gemini"] in no_key.unavailable["gemini-embed"]
    no_date = build_providers(embed_env(CAREFLOW_GEMINI_VERIFIED=""), profile="demo")
    assert no_date.embedding_for(CLINIC_ID) is None
    no_cap = build_providers(embed_env(CAREFLOW_SPEND_CAP_PER_RUN_USD=None), profile="demo")
    assert no_cap.embedding_for(CLINIC_ID) is None


@pytest.mark.asyncio
async def test_the_embed_route_goes_through_the_gateway_as_synthetic_only_and_a_stop_is_an_unknown_answer_not_a_crash():
    mock = Mock(json_reply(embed_ok(2)))
    built = build_providers(embed_env(), profile="demo", http=mock.client())
    got = await built.embedding_for(CLINIC_ID).embed(["one", "two"])
    assert len(got.value.vectors) == 2 and len(mock.requests) == 1
    # a ceiling of zero on a FREE row never bites (nothing is spent); a failing provider is a TransientError for the retriever (dense_unavailable)
    failing = build_providers(embed_env(), profile="demo", http=Mock(json_reply({"error": {}}, 500)).client())
    with pytest.raises(TransientError):
        await failing.embedding_for(CLINIC_ID).embed(["one"])
    row = ModelRow("embed", "gemini", "gemini-embedding-2", "synthetic_only", 1, Decimal("0.001"), Decimal("0.001"), Decimal("0.001"), Decimal(0))

    async def adapter(request):
        return AdapterResult(EmbeddingResult([[0.1] * 768], 768), Usage(1000, 0))

    gateway = Gateway([row], {("gemini", "gemini-embedding-2"): adapter}, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=Decimal(0)), origin="test")
    with pytest.raises(TransientError):  # a spend ceiling reached becomes an abstention upstream, not an exception out of the route
        await GatewayEmbedding(gateway, CLINIC_ID, "gemini-embedding-2").embed(["one"])
    assert SpendCeilingReached  # (the gateway's own exception, converted above)


@pytest.mark.asyncio
async def test_the_gateway_wrapper_refuses_a_wrong_sized_answer():
    row = ModelRow("embed", "gemini", "gemini-embedding-2", "synthetic_only", 1, Decimal(0), Decimal(0), Decimal("0.001"), Decimal(0))

    async def adapter(request):
        return AdapterResult(EmbeddingResult([[0.1] * 8], 8), Usage(1, 0))

    gateway = Gateway([row], {("gemini", "gemini-embedding-2"): adapter}, InMemorySpendLedger(), SpendLimits(clinic_daily_usd=Decimal(1)), origin="test")
    with pytest.raises(PermanentError):
        await GatewayEmbedding(gateway, CLINIC_ID, "gemini-embedding-2").embed(["one"])


def test_the_embed_row_is_in_the_provider_file_as_synthetic_only_free_tier_and_labelled():
    doc = yaml.safe_load((ROOT / "config" / "demo-providers.yaml").read_text(encoding="utf-8"))
    (row,) = [r for r in doc["rows"] if r["role"] == "embed"]
    assert row["data_policy"] == "synthetic_only" and row["provider"] == "gemini" and row["free_tier"] is True and row["label"].startswith("DEMO ONLY")
    register = yaml.safe_load((ROOT / "data-flow-register.yaml").read_text(encoding="utf-8"))["calls"]
    entry = next(c for c in register if c["id"] == "gemini-embeddings")  # the data-flow register lists the call (CLAUDE.md rule 7)
    assert "batchEmbedContents" in entry["host"] and entry["source"] == "careflow/adapters/gemini_embed.py" and "synthetic" in entry["data_sent"].lower()


# ---- the synthetic placeholder cards -------------------------------------------------------------------------------------------------------------


def test_the_placeholder_cards_are_labelled_invented_and_kb_cards_stays_empty():
    cards, queries = demo_cards.load()
    assert len(cards) >= 5 and len(queries) >= 8 and all(c.fields.card_id.startswith("demo-card-") for c in cards)
    assert {q.expect for q in queries} <= {c.fields.card_id for c in cards}
    nork = next(c for c in cards if c.fields.card_id == "demo-card-nork")
    assert "nurse" not in nork.audience and "doctor" in nork.audience  # one card exists to prove the role filter
    kb_files = [p.name for p in (ROOT / "kb" / "cards").iterdir()]
    assert kb_files == [".gitkeep"]  # clinician-owned and EMPTY: nothing here is, or becomes, a real card
    raw = (ROOT / "config" / "demo-cards.yaml").read_text(encoding="utf-8")
    assert raw.count("DEMO ONLY - NOT A CLINICAL DECISION") >= len(cards) + 1


def test_a_card_file_that_is_unlabelled_has_unknown_keys_or_a_bad_id_is_refused(tmp_path):
    doc = yaml.safe_load((ROOT / "config" / "demo-cards.yaml").read_text(encoding="utf-8"))
    mutations = (
        lambda d: d.__setitem__("label", "x"), lambda d: d["cards"][0].__setitem__("label", "x"), lambda d: d["cards"][0].__setitem__("extra", 1), lambda d: d["cards"][0].__setitem__("card_id", "real-card-1"),
        lambda d: d["cards"][0].__setitem__("audience", ["janitor"]), lambda d: d["cards"][0].__setitem__("version", 0), lambda d: d["queries"][0].__setitem__("expect", "demo-card-missing"),
        lambda d: d["cards"].append(dict(d["cards"][0])), lambda d: d.__setitem__("rules", []),
    )
    for mutate in mutations:
        bad = yaml.safe_load(yaml.safe_dump(doc))
        mutate(bad)
        path = tmp_path / "bad.yaml"
        path.write_text(yaml.safe_dump(bad), encoding="utf-8")
        with pytest.raises(demo_cards.DemoCardsRefused):
            demo_cards.load(path)


# ---- the store's SQL calls ---------------------------------------------------------------------------------------------------------------------


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def one(self):
        return self._rows[0]


class Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class RecordingConn:
    def __init__(self, rows=(), raises=None):
        self.calls, self._rows, self._raises = [], list(rows), raises

    def begin_nested(self):
        return Nested()

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        if self._raises:
            raise self._raises
        return Rows(self._rows)


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def dbapi_error(state):
    from sqlalchemy.exc import DBAPIError

    class Orig(Exception):
        sqlstate = state

    return DBAPIError("stmt", {}, Orig())


@pytest.mark.asyncio
async def test_the_store_calls_only_the_three_functions_with_the_vector_literal_and_the_scope():
    conn = RecordingConn([Row(o_card_id="c1", o_card_version=2, o_similarity=0.75)])
    store = PgVectorStore(conn, "m-1")
    hits = await store.search(tuple([0.1] * 768), limit=5, scope={"clinic_id": str(CLINIC_ID), "origin": "test"})
    assert [(h.card_id, h.version, h.similarity) for h in hits] == [("c1", 2, 0.75)]
    sql, params = conn.calls[0]
    assert "kb.search_card_embeddings" in sql and params["c"] == CLINIC_ID and params["o"] == "test" and params["m"] == "m-1" and params["n"] == 5 and params["q"].startswith("[0.1,")
    assert "kb.card_embeddings" not in sql.replace("kb.search_card_embeddings", "")  # never the table itself
    for bad in ({}, {"clinic_id": str(CLINIC_ID)}, {"origin": "test"}, {"clinic_id": "not-a-uuid", "origin": "test"}):
        with pytest.raises(KnowledgeError):
            await store.search(tuple([0.1] * 768), limit=5, scope=bad)  # a search without a clinic and an origin never runs (10.7)
    with pytest.raises(KnowledgeError):
        await store.delete(["c1"])  # append-only
    truth = PgCardTruth(RecordingConn([Row(o_card_id="c1", o_card_version=3)]), clinic_id=CLINIC_ID, origin="test", model_id="m-1")
    assert dict(await truth.current_versions(["c1", "c2"])) == {"c1": 3} and dict(await truth.current_versions([])) == {}


@pytest.mark.asyncio
async def test_the_stores_refusals_are_one_fixed_word_each_and_a_changed_version_is_a_conflict():
    scope = {"clinic_id": str(CLINIC_ID), "origin": "test", "audience": "nurse", "content_sha256": "a" * 64}
    point = VectorPoint("c1", 1, tuple([0.1] * 768), scope)
    with pytest.raises(Refused):
        await PgVectorStore(RecordingConn(raises=dbapi_error("42501")), "m").search(tuple([0.1] * 768), limit=3, scope=scope)
    with pytest.raises(Refused):
        await PgVectorStore(RecordingConn(raises=dbapi_error("42501")), "m").index_one(point)
    with pytest.raises(VersionConflict):
        await PgVectorStore(RecordingConn(raises=dbapi_error("23505")), "m").index_one(point)
    with pytest.raises(Refused):
        await PgCardTruth(RecordingConn(raises=dbapi_error("42501")), clinic_id=CLINIC_ID, origin="test", model_id="m").current_versions(["c1"])
    created = await PgVectorStore(RecordingConn([Row(o_created=True)]), "m").index_one(point)
    assert created is True


# ---- the reranker and the context assembler --------------------------------------------------------------------------------------------------


def retrieval_of(*ids, abstained=False):
    return Retrieval(abstained, "below_threshold" if abstained else None, () if abstained else tuple(Candidate(i, 1, 0.03, 0.9, 0.5) for i in ids))


@pytest.mark.asyncio
async def test_the_reranker_reorders_never_adds_never_unabstains_and_a_failure_keeps_the_order():
    texts = {"a": "alpha beta", "b": "the zorblat widget quibble", "c": "gamma"}
    got, did = await knowledge.rerank(retrieval_of("a", "b", "c"), "zorblat quibble widget", texts, FakeRerank("r"))
    assert [c.card_id for c in got.candidates] == ["b", "a", "c"] and did is True
    same, did = await knowledge.rerank(retrieval_of("b", "a"), "zorblat quibble", texts, FakeRerank("r"))
    assert [c.card_id for c in same.candidates] == ["b", "a"] and did is False
    nothing, did = await knowledge.rerank(retrieval_of(abstained=True), "q", texts, FakeRerank("r"))
    assert nothing.abstained and nothing.candidates == () and did is False  # it can never un-abstain
    failing = FakeRerank("r")
    failing.fail_next(1)
    kept, did = await knowledge.rerank(retrieval_of("a", "b"), "q", texts, failing)
    assert [c.card_id for c in kept.candidates] == ["a", "b"] and did is False

    class Odd:
        async def rerank(self, request):
            return AdapterResult(RerankResult([0, 0], [1.0, 1.0]), Usage(1, 0))

    odd, did = await knowledge.rerank(retrieval_of("a", "b"), "q", texts, Odd())
    assert [c.card_id for c in odd.candidates] == ["a", "b"] and did is False  # not a permutation: not used


def test_the_context_is_assembled_under_a_declared_budget_and_what_was_dropped_is_reported_by_label():
    texts = {"a": "alpha " * 10, "b": "beta " * 10, "c": "gamma " * 10}
    r = retrieval_of("a", "b", "c")
    assert knowledge.build_context("Answer with the evidence.", r, texts, budget_tokens=None) is None  # no declared budget: nothing is assembled (unknown is not a number)
    full = knowledge.build_context("Answer with the evidence.", r, texts, budget_tokens=500)
    assert full.included[1:] == ("a", "b", "c") and full.dropped == () and full.tokens <= 500
    tight = knowledge.build_context("Answer with the evidence.", r, texts, budget_tokens=40)
    assert tight.included[0] == "instruction" and "c" in tight.dropped and tight.tokens <= 40  # the lowest-ranked card goes first, reported by label only
    assert all("gamma" not in label for label in tight.dropped)
    none = knowledge.build_context("Answer with the evidence.", retrieval_of(abstained=True), texts, budget_tokens=500)
    assert none.included == ("instruction",)  # an abstention brings no evidence
    with pytest.raises(BudgetTooSmall):
        knowledge.build_context("Answer with the evidence. " * 50, r, texts, budget_tokens=5)


# ---- thresholds are required settings ------------------------------------------------------------------------------------------------------------


def test_every_threshold_is_a_required_setting_with_no_default():
    assert knowledge.policy_from({}) is None and knowledge.budget_from({}) is None and retrieval_eval.thresholds_from({}) is None
    assert knowledge.policy_from({knowledge.DENSE_SETTING: "0.3"}) is None and knowledge.policy_from({knowledge.BM25_SETTING: "0.3"}) is None  # both or neither
    assert knowledge.policy_from({knowledge.DENSE_SETTING: "0.3", knowledge.BM25_SETTING: "0.3"}) == AbstentionPolicy(0.3, 0.3)
    for bad in ("abc", "", "2", "-2", "nan"):
        assert knowledge.policy_from({knowledge.DENSE_SETTING: bad, knowledge.BM25_SETTING: "0.3"}) is None
    assert knowledge.budget_from({knowledge.BUDGET_SETTING: "400"}) == 400 and knowledge.budget_from({knowledge.BUDGET_SETTING: "0"}) is None and knowledge.budget_from({knowledge.BUDGET_SETTING: "-5"}) is None
    good = {retrieval_eval.K_SETTING: "3", retrieval_eval.RECALL_SETTING: "0.8", retrieval_eval.MRR_SETTING: "0.6"}
    assert retrieval_eval.thresholds_from(good) == retrieval_eval.EvalThresholds(3, 0.8, 0.6)
    for drop in good:
        assert retrieval_eval.thresholds_from({k: v for k, v in good.items() if k != drop}) is None
    for k, v in (("CAREFLOW_RETRIEVAL_EVAL_K", "0"), ("CAREFLOW_RETRIEVAL_EVAL_MIN_RECALL", "0"), ("CAREFLOW_RETRIEVAL_EVAL_MIN_RECALL", "1.5"), ("CAREFLOW_RETRIEVAL_EVAL_MIN_MRR", "x")):
        assert retrieval_eval.thresholds_from({**good, k: v}) is None


# ---- the quality harness -------------------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_harness_measures_ranking_with_no_threshold_and_says_not_measured_without_them():
    cards, queries = demo_cards.load()
    world = await retrieval_eval.OfflineWorld.build(cards, HashingEmbedding())
    unmeasured = await retrieval_eval.evaluate(world, queries, thresholds=None, policy=None)
    assert unmeasured.status == "NOT MEASURED" and unmeasured.recall_at_k is None and unmeasured.mrr is None  # no pass or fail is claimed
    report = await retrieval_eval.evaluate(world, queries, thresholds=retrieval_eval.EvalThresholds(3, 0.8, 0.6), policy=None)
    assert report.status == "PASS" and report.recall_at_k == 1.0 and report.mrr == 1.0 and report.abstained is None  # abstention is not measured without its policy
    with_policy = await retrieval_eval.evaluate(world, queries, thresholds=retrieval_eval.EvalThresholds(3, 0.8, 0.6), policy=AbstentionPolicy(0.30, 0.30))
    assert with_policy.abstained == 0 and with_policy.answered_wrong == 0
    assert (await retrieval_eval.evaluate(world, [], thresholds=retrieval_eval.EvalThresholds(3, 0.8, 0.6), policy=None)).status == "NOT MEASURED"


@pytest.mark.asyncio
async def test_the_harness_fails_when_the_ranking_is_wrong_and_counts_a_wrong_answer():
    cards, queries = demo_cards.load()
    world = await retrieval_eval.OfflineWorld.build(cards, HashingEmbedding())
    swapped = [demo_cards.DemoQuery(q.text, "demo-card-nork" if q.expect != "demo-card-nork" else "demo-card-zorblat") for q in queries]
    report = await retrieval_eval.evaluate(world, swapped, thresholds=retrieval_eval.EvalThresholds(1, 0.8, 0.6), policy=AbstentionPolicy(0.30, 0.30))
    assert report.status == "FAIL" and "recall_below_threshold" in report.reasons and report.answered_wrong and report.answered_wrong > 0


@pytest.mark.asyncio
async def test_an_unrelated_question_abstains_and_a_missing_policy_abstains_with_its_reason():
    cards, _ = demo_cards.load()
    world = await retrieval_eval.OfflineWorld.build(cards, HashingEmbedding())
    scope = dict(retrieval_eval.SCOPE)
    nothing = await world.retriever(AbstentionPolicy(0.30, 0.30)).retrieve("a question about nothing in the library at all", scope=scope)
    assert nothing.abstained and nothing.reason == "below_threshold"
    no_policy = await world.retriever(None).retrieve("where do I hand back a zorblat widget", scope=scope)
    assert no_policy.abstained and no_policy.reason == "no_calibrated_policy"


# ---- the route, the app and the migration ---------------------------------------------------------------------------------------------------------


def test_knowledge_search_is_built_only_in_demo_synthetic_with_the_switch_exactly_on():
    on = {"CAREFLOW_DATA_POLICY": "synthetic_only", knowledge.SWITCH: "on"}
    assert knowledge.from_environment(on, {}, "demo", None) is not None
    for env, profile in (({}, "demo"), ({"CAREFLOW_DATA_POLICY": "synthetic_only"}, "demo"), (on, "clinical"), ({**on, "CAREFLOW_DATA_POLICY": "phi_approved"}, "demo"), ({**on, knowledge.SWITCH: "1"}, "demo"), ({**on, knowledge.SWITCH: "true"}, "demo")):
        assert knowledge.from_environment(env, {}, profile, None) is None, (env, profile)
    built = knowledge.from_environment(on, {knowledge.DENSE_SETTING: "0.3", knowledge.BM25_SETTING: "0.3", knowledge.BUDGET_SETTING: "400"}, "demo", None)
    assert built.policy == AbstentionPolicy(0.3, 0.3) and built.budget_tokens == 400 and len(built.cards) >= 5
    embedder, model = built._embedder_for(CLINIC_ID)
    assert isinstance(embedder, HashingEmbedding) and model == FAKE_MODEL_ID  # fakes are the default: no real embedder without the real-model gates
    unset = knowledge.from_environment(on, {}, "demo", None)
    assert unset.policy is None and unset.budget_tokens is None  # thresholds unset: the retriever will abstain, the budget is unknown


def test_the_hosted_embedder_is_used_only_when_the_real_model_gates_and_the_embed_row_all_hold():
    on = {"CAREFLOW_DATA_POLICY": "synthetic_only", knowledge.SWITCH: "on"}
    providers = build_providers(embed_env(), profile="demo", http=Mock(json_reply(embed_ok(1))).client())
    without_gate = knowledge.from_environment(on, {}, "demo", providers)
    assert without_gate._embedder_for(CLINIC_ID)[1] == FAKE_MODEL_ID  # CAREFLOW_DEMO_REAL_MODELS is off: the fake
    with_gate = knowledge.from_environment({**on, "CAREFLOW_DEMO_REAL_MODELS": "on"}, {}, "demo", providers)
    embedder, model = with_gate._embedder_for(CLINIC_ID)
    assert model == "gemini-embedding-2" and isinstance(embedder, GatewayEmbedding)
    no_embed = knowledge.from_environment({**on, "CAREFLOW_DEMO_REAL_MODELS": "on"}, {}, "demo", build_providers(good_env(CAREFLOW_SPEND_CEILING_EMBED_USD=None), profile="demo"))
    assert no_embed._embedder_for(CLINIC_ID)[1] == FAKE_MODEL_ID  # the embed row was not built: the fake, not a crash


def test_the_route_checks_the_role_before_it_answers_503_and_returns_only_ids_and_scores():
    source = (ROOT / "careflow" / "app.py").read_text(encoding="utf-8")
    start = source.index("async def knowledge_search_route")
    body = source[start : start + 2200]
    assert body.index("_console_clinic(conn, body.clinic_id, KNOWLEDGE_ROLES)") < body.index("status_code=503")
    assert "KNOWLEDGE_ROLES = (\"nurse\", \"doctor\")" in source
    model = source[source.index("class KnowledgeCandidateOut") : source.index("class KnowledgeSearchOut")]
    assert set(re.findall(r"^    (\w+):", model, re.MULTILINE)) == {"card_id", "version", "similarity", "bm25_ratio"}  # no card text, no query


def test_the_migration_is_append_only_forced_rls_with_no_grant_no_policy_no_text_and_definer_functions_that_pin_the_search_path():
    sql = (ROOT / "db" / "migrations" / "0085_card_embeddings.sql").read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    assert "create extension if not exists vector with schema extensions" in code
    assert "enable row level security" in code and "force row level security" in code and "create policy" not in code
    assert not re.search(r"grant\s+(select|insert|update|delete|all)[^;]*kb\.card_embeddings", code)  # no table grant to anyone
    assert "enable always trigger card_embeddings_no_change" in code and "enable always trigger card_embeddings_no_truncate" in code
    columns = re.search(r"create table kb\.card_embeddings \((.*?)\n\);", code, re.DOTALL).group(1)
    names = set(re.findall(r"^\s{4}(\w+)\s", columns, re.MULTILINE)) - {"constraint"}
    assert names == {"id", "clinic_id", "origin", "card_id", "card_version", "model_id", "audience", "embedding", "content_sha256", "indexed_by", "created_at", "idempotency_key"}
    functions = re.findall(r"create function (kb\.\w+)\(", code)
    assert set(functions) == {"kb.card_embeddings_guard", "kb.index_card_embedding", "kb.search_card_embeddings", "kb.current_card_versions"}
    index_args = re.search(r"create function kb\.index_card_embedding\((.*?)\)\s*returns", code, re.DOTALL).group(1)
    assert "extensions.vector" not in index_args and "p_query text" in code  # the vector is passed as TEXT: callers never need USAGE on the extensions schema
    for name in ("index_card_embedding", "search_card_embeddings", "current_card_versions"):
        block = code[code.index(f"create function kb.{name}") : code.index(f"revoke all on function kb.{name}")]
        assert "security definer set search_path = ''" in block and "auth.uid()" in block and "42501" in block
        assert f"grant execute on function kb.{name}" in code and "to careflow_app" in code
    search = code[code.index("create function kb.search_card_embeddings") : code.index("revoke all on function kb.search_card_embeddings")]
    for needed in ("e.clinic_id = p_clinic_id", "e.origin = p_origin", "e.model_id = p_model_id", "iam.has_clinic_role(e.clinic_id, e.audience)", "not exists", "n.card_version > e.card_version"):
        assert needed in search, needed  # clinic, origin, model, role and CURRENT version: always
    assert "kb.card_embeddings" not in "\n".join(line for line in code.splitlines() if "grant" in line)


def test_the_migration_is_in_the_schema_check_and_the_new_live_file_is_in_exactly_one_ci_shard():
    assert "0085" in (ROOT / "careflow" / "schema_check.py").read_text(encoding="utf-8")
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("tests/test_knowledge_db_live.py") == 1
    assert asyncio  # (the module is async-aware; pytest.ini sets the mode per test)


# ---- the owner-run indexing script ----------------------------------------------------------------------------------------------------------------


def load_index_script():
    import importlib.util
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("index_demo_cards_under_test", ROOT / "scripts" / "index_demo_cards.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_indexing_script_refuses_outside_dev_demo_synthetic_before_any_connection_and_prints_no_secret(tmp_path, monkeypatch, capsys):
    script = load_index_script()
    cases = {
        "clinical": "CAREFLOW_ENV=dev\nCAREFLOW_PROFILE=clinical\nCAREFLOW_DATA_POLICY=synthetic_only\n",
        "not-dev": "CAREFLOW_ENV=ci\nCAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=synthetic_only\n",
        "not-synthetic": "CAREFLOW_ENV=dev\nCAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=phi_approved\n",
        "no-urls": "CAREFLOW_ENV=dev\nCAREFLOW_PROFILE=demo\nCAREFLOW_DATA_POLICY=synthetic_only\nSOME_OTHER_SECRET=SECRET-NEVER-PRINTED\n",
    }
    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("asked for the host before refusing")))
    for name, content in cases.items():
        env = tmp_path / f"{name}.env"
        env.write_text(content, encoding="utf-8")
        monkeypatch.setattr("sys.argv", ["index_demo_cards.py", "--clinic-id", str(uuid.uuid4()), "--env-file", str(env)])
        assert asyncio.run(script.main()) == 1, name
    out = capsys.readouterr().out
    assert out.count("refusing") == 4 and "SECRET-NEVER-PRINTED" not in out


def test_the_indexing_script_prints_only_metadata():
    source = (ROOT / "scripts" / "index_demo_cards.py").read_text(encoding="utf-8")
    code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
    assert "_API_KEY" not in code
    assert not re.search(r"say\([^)]*(\.text\b|q\.text|\.vectors|card_text|retrieval_text)", code)  # no card text, question or vector is ever printed
    assert "queue_worker" in code and "origin != \"test\"" in code and "seed.confirm_target" in code
