# P2-6 (pure-code part) - card retrieval mechanics

Code: `careflow/retrieval.py`, `VectorStorePort` in `careflow/ports.py`, `FakeVectorStore` in `careflow/adapters/fake.py`. Tests: `tests/test_retrieval.py`.
NO card content anywhere (cards are clinician-authored). Qdrant, the embedding model and the card tables are NOT built here.

## What it does
retrieval text from structured fields only (no rules field exists) -> BM25 (own code) + dense (EmbeddingPort -> VectorStorePort) -> RRF (k=60) ->
version-checked join to Postgres truth (stale or retired hits discarded and counted) -> abstention on the TOP candidate's absolute signals.

## Decisions (noted)
- **No default threshold.** `AbstentionPolicy(min_dense_similarity, min_bm25_ratio)` has two required numbers; the retriever's `policy` has no default
  and `None` abstains with `no_calibrated_policy` (nothing is embedded). The values need the gold set (§15.1) and a clinical lead.
- **Why two absolute signals, not the fused score.** RRF scores are rank-based: a weak lone candidate that tops both lists scores as high as a strong one.
- **Dense side down => abstain** (`dense_unavailable`), never BM25-only: the dense signal is unknown and an unknown signal satisfies no policy.
- **Scope is a required pre-filter on both sides** (BM25 index carries each card's scope; the vector store filters before ranking).
- **Deviation from §10.1:** BM25 is held in-process, not stored as Qdrant sparse vectors with server-side RRF (see the module docstring). Say if you want the sparse-vector layout instead.
- The `embed` call carries the patient's query: the route must allow patient text (gateway data policy). Not wired here.

## Open (not mine to decide)
Card schema/tables and who may publish (clinicians); the scope keys; the reranker step (§10.6, decorative); calibrated thresholds; real Qdrant adapter (approved library, needs Context7 + data-flow entry when built).
