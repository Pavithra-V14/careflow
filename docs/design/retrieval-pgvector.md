# Retrieval in Postgres with pgvector (mechanism level, synthetic demo)

**Decision of the owner, 2026-11-14.** Card retrieval is built to MECHANISM level inside the existing Supabase Postgres using the `pgvector` extension, with embeddings from Google's Gemini embedding model through the already verified Gemini key. This is a **documented deviation from BUILD-SPEC.md §3 / §10.1 (Qdrant)**: no new external service, no new dependency. **The spec text is not edited here** (spec wording changes are asked first); this note and `docs/spec-compliance.md` are the record. Appendix B item 14 (Qdrant per-clinic access control) is not needed for this path: tenancy is Postgres RLS and the definer functions below.

## What exists
| Piece | Where | Notes |
|---|---|---|
| Vector table | `db/migrations/0085_card_embeddings.sql`, `kb.card_embeddings` | per clinic, per origin, per card id, per card VERSION, per embedding model; opaque ids, a SHA-256 of the embedded text, the roles allowed to retrieve it, 768 numbers. **No card text, no patient text, no free-text column.** Append-only (update, delete, truncate refused, even for the owner; ENABLE ALWAYS). Forced RLS, **no policy, no table grant**. |
| Indexing | `kb.index_card_embedding` (admin or queue worker of the clinic), `careflow/knowledge_sql.py` `index_cards`, `scripts/index_demo_cards.py` | idempotent on a key; the same version with different content is refused (a changed card is a NEW version); a row's origin must be its clinic's origin |
| Retrieval | `kb.search_card_embeddings` (nurse, doctor, queue worker), `careflow/knowledge.py` | **always** filters by clinic, origin, embedding model, CURRENT version and the caller's ROLE; one 42501 for every refusal; exact (sequential) cosine, no ANN index |
| Version truth | `kb.current_card_versions`, `PgCardTruth` | the join of spec 10.8 over the same table |
| Pipeline | `careflow/retrieval.py` (unchanged) | BM25 + dense, RRF, version join, abstention. `PgVectorStore` is its `VectorStorePort` |
| Embedder (default) | `careflow/embeddings.py` `HashingEmbedding` | deterministic hashed bag of words, 768 numbers, no network: **the fake is the default** |
| Embedder (hosted) | `careflow/adapters/gemini_embed.py`, role `embed` in `config/demo-providers.yaml`, `provider_setup.build_providers` | `gemini-embedding-2`, `outputDimensionality` 768, free tier, `synthetic_only`, through the gateway (durable spend ledger, `CAREFLOW_SPEND_CEILING_EMBED_USD`, the per-run cap). Refused in the clinical profile, under CI, and under any data policy other than `synthetic_only`. Used only when `CAREFLOW_DEMO_REAL_MODELS` is on. |
| Reranker | `RerankPort` + `FakeRerank`, `knowledge.rerank` | reorders only; never adds a card, never un-abstains; a failure keeps the order |
| Context | `knowledge.build_context` over `careflow/context_assembler.py` | the retrieved cards are EVIDENCE under a DECLARED budget (`CAREFLOW_CONTEXT_BUDGET_TOKENS`); what did not fit is reported by label |
| Quality harness | `careflow/retrieval_eval.py` | recall@k, MRR, abstentions, wrong answers over the placeholder cards; **every threshold a required setting, none defaulted** |
| Placeholder cards | `config/demo-cards.yaml`, `careflow/demo_cards.py` | invented words that mean nothing, labelled DEMO ONLY. **`kb/cards` stays clinician-owned and empty** (a test proves it) |
| Route | `POST /v1/staff/knowledge/search` (nurse, doctor) | returns card ids, versions and scores only; the query is masked before any model and is stored nowhere; 503 until `CAREFLOW_KNOWLEDGE_SEARCH=on` in a demo + `synthetic_only` deployment |

## Required settings (no defaults)
`CAREFLOW_RETRIEVAL_MIN_DENSE_SIMILARITY`, `CAREFLOW_RETRIEVAL_MIN_BM25_RATIO` (both or the retriever abstains with `no_calibrated_policy`), `CAREFLOW_CONTEXT_BUDGET_TOKENS` (unset: nothing is assembled), `CAREFLOW_RETRIEVAL_EVAL_K`, `CAREFLOW_RETRIEVAL_EVAL_MIN_RECALL`, `CAREFLOW_RETRIEVAL_EVAL_MIN_MRR` (unset: the harness says NOT MEASURED), `CAREFLOW_SPEND_CEILING_EMBED_USD`. The demo pack (`config/demo-phase4.yaml`, `CAREFLOW_DEMO_PHASE4=1`) supplies labelled DEMO ONLY values; they are not clinical and not calibrated.

## Limits, stated
1. **Mechanism, not a clinical store.** No real card exists. The thresholds need a clinician-reviewed gold set (spec 15.1) before any real value is chosen; the harness numbers on invented text with a hashing fake prove only that the pieces work.
2. **The fake does not understand meaning:** a synonym is as far from a word as an unrelated word. Only the hosted model (after the live check) embeds meaning.
3. **A fake index and a hosted index never mix** (the search filters by the model id). Switching to the hosted model means indexing again with `--real`; the first index stays (append-only).
4. **The BM25 side runs in this process over the placeholder cards' text and knows no roles.** A card the caller's role may not read is dropped at the version join (the database does not return it), so it cannot leak; it is counted as stale. The database, not the application, enforces the role.
5. **Postgres truth for card versions is the index itself** until a real card lifecycle exists (clinician-owned, not built).
6. **No ANN index.** An exact scan is right at tens to hundreds of cards per clinic and cannot return too few rows under a selective filter. An HNSW index is a later, measured decision.
7. **The Gemini response shape and the usage fields are Appendix B facts**, not documented in the REST examples: confirmed only by `scripts/provider_live_check.py --provider gemini` (lines E1 to E3). Usage is estimated HIGH until then.
8. **Erasure and retention:** the table holds no patient data (cards only), so patient erasure does not touch it; its retention is the owner's decision.
9. **Reviewer pass:** the `rls-security-reviewer` subagent was started for 0085 but ended on the API usage limit before reporting; no reviewer report exists yet. Do not treat 0085 as reviewed.
