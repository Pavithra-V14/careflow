# P2-2: the `decision` engine tier (BUILD-SPEC.md §5.2, §11.6, §14.3, §17.4)

Status: design, written before the code. Thin adapters over **injectable clients**; only fake clients exist. **No model, weight, runtime,
account, key or network call is added.** Decisions that affect safety are marked **[S]**; each is a fail-closed reading of the spec, so they are
listed here for review rather than asked.

## What is built

- `DecisionPort` (exists in `careflow/ports.py`) and three adapters, `LayaAdapter`, `JevAdapter`, `LlmAdapter` (`careflow/adapters/decision.py`),
  each a small class over a client Protocol (`LayaClient`, `JevClient`, `LlmClient`) that the tests satisfy with fakes. A real client arrives with
  the owner's approval of the service (rule 13) and its data-flow-register entry; the adapter does not change.
- `DecisionService` (`careflow/decision.py`): asks the gateway for role `decision`, then records ONE entry per call (engine, provider, pinned
  model id, confidence, whether calibrated, data class, the fallback attempts, prompt version): ids and numbers only, never the text.
- Script detection (`careflow/script.py`), a pure function over Unicode letter names.
- The contract test (`tests/test_decision_contract.py`): golden inputs through all three adapters, a stated tolerance.

## Routing by data class (§5.2)

| Data class | Chain | How it is produced |
|---|---|---|
| Synthetic | Laya, then Jev, then `llm_fast` | registry priority; Laya and Jev rows `synthetic_only` or better |
| Patient (`phi`) | Laya, then `llm_fast`; **Jev skipped** | the gateway offers a PHI request only to `phi_approved` rows (Laya is self-hosted, so it can be); Jev is a third party, so its row is never `phi_approved` |

**[S] Defence in depth:** `DecisionRequest` now carries a REQUIRED `data_class` (no default: unknown is not synthetic), and `JevAdapter`
refuses a `PHI` request itself (raises `PermanentError`) even if the registry were misconfigured. The gateway's policy filter stays the primary
control; the adapter check means a wrong registry row cannot make Jev see patient text.

## Script detection before Laya (§5.2: "route by detected script before calling, never by confidence after")

Laya "cannot detect a script it wasn't trained for and will return a confident wrong answer on it" (vendor-measured: 0.10 on Hindi at over 0.9
confidence). So the adapter decides applicability from the TEXT, before any call:

| Text | Checkpoint |
|---|---|
| Letters all Latin, `language` hint `en` | English |
| Letters all Latin, any other `language` hint (for instance romanised Tamil, "Tanglish") | multilingual |
| Contains Tamil letters (alone or mixed with Latin) | multilingual; the original text is sent and kept, never replaced |
| Any other letter script (Devanagari, Arabic, Han...), or no letters at all | **not applicable: Laya is skipped** |

**[S]** Only Latin and Tamil are routed to Laya because those are the ones the spec names and the gold set will test. An unsupported script is
NOT "tried and trusted by confidence"; the route is skipped and the next engine answers (or the call fails visibly). The skip is a new outcome,
`RouteNotApplicable`: it is not a failure, does not count against the circuit breaker, and does not count as a model call (the adapter exposes
`accepts(request)`, which the gateway checks before the budget, the spend check and the breaker). The English-versus-multilingual choice for
Latin text with a non-English hint is a deliberate default, to be confirmed against the Tamil gold set (spec: "do not assume which wins").
The third checkpoint, typed-decisions, is selected by question type once the question types exist; today the adapter maps every question to the
English or multilingual checkpoint above.

Also enforced in the adapter, not only in `DecisionRequest`: **never more than 20 options in one `choice` call.**

## Confidence is recorded, and says whether it can be trusted

Every `DecisionResult` carries `engine`, `confidence` (the probability of the chosen option), the full `probabilities`, and two new fields:
`calibrated` (bool) and `route` (the checkpoint or prompt version that answered).

**[S]** Laya's raw output is "near random out of the box"; usable confidence needs temperature scaling fitted per `(engine, question_type)`
(§5.2). A `CalibrationStore` Protocol supplies the temperature; with none stored, the result is returned with `calibrated = False` and the raw
probabilities. **A consumer must treat an uncalibrated confidence as unknown, not as a probability**; nothing in this package auto-approves on
it. The store is in memory today; the versioned, approved calibration table is a later migration. Jev and LLM confidences are never marked
calibrated by this package.

## The LLM adapter (`llm_fast`)

- The prompt text lives in `prompts/` and passes the CI scan: `decision_choice.v1.system.txt` has no placeholder; the `.user.txt` uses only
  allowlisted variables (`question_type`, `options_block`, `input_text`). The loader records the template's version and sha256, and every result
  carries the prompt version that answered (§17.4).
- **[S]** The reply must be JSON naming exactly one of the offered options. Anything else (an unlisted option, two options, free text, a
  malformed object, a confidence outside 0..1) is a `PermanentError`: an unparseable answer is NOT a choice, and text that tries to instruct the
  model cannot widen what is accepted, because only an exact option string is ever returned.

## The contract test (§14.3 hard gate "the three-engine contract test passes")

Golden inputs (synthetic and non-clinical: shop-opening-hours style questions; no clinical content is invented) run through all three adapters.
It asserts, per adapter and per item: the choice is one of the options; probabilities sum to 1 (1e-6); the choice is the argmax; `confidence`
equals the chosen option's probability; the engine name is recorded; and the 20-option cap and the PHI refusal hold. **Stated tolerance (an
engineering starting value the owner may change): the three engines agree on the choice for at least 90% of the golden items, and where they
agree their confidences differ by at most 0.35.**

**Honest limit:** with fake clients this test proves the harness and each adapter's mapping and validation, not the engines. The hard gate is
only met when the same test runs against the real Laya, Jev and LLM, which needs their models and the owner's approval of each service. P2-2 is
therefore "built, contract-tested against fakes", and the Phase 2 acceptance item stays OPEN until the real run. Appendix B items this depends on:
Laya's CPU latency, and Jev's request shape via OpenRouter. The Jev payload is built in ONE function marked UNVERIFIED so a real-shape
correction is a one-place change.

## Local model sizes (for your memory decision; approximate, from the model cards, not measured here)

Nothing below is downloaded or loaded; the clients are Protocols and the tests use fakes.

| Item | Disk (approx.) | RAM when loaded (approx.) |
|---|---|---|
| Laya English or typed-decisions checkpoint (ModernBERT-large, 421M parameters) | 1.7 GB in fp32, 0.85 GB in fp16 | about 2 to 3 GB each, plus the runtime |
| Laya multilingual checkpoint (mmBERT-base, 322M) | 1.3 GB fp32, 0.65 GB fp16 | about 1.5 to 2.5 GB |
| PyTorch CPU runtime (needed to run any of them) | about 0.2 to 0.3 GB wheel, about 1 GB installed with dependencies | the interpreter plus the runtime, a few hundred MB before a model |
| Unstructured (local) with its layout and OCR helpers | several GB with the model downloads (layout detection and OCR), plus system packages | 1 to 3 GB while partitioning a page |
| Local embedding model (`bioclinical-modernbert-base-embeddings`, base size, 768-dim) | about 0.6 GB fp32 | about 1 to 2 GB |

Laya's three checkpoints together are roughly 4.5 GB on disk and would not all fit in memory at once on a small machine; the first run on real
hardware should load one at a time. Each of these waits for your go-ahead.
