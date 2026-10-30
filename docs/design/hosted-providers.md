# Hosted model providers: Gemini (vlm), Jev via OpenRouter (decision), Sarvam (translation)

Decision of the owner, 2026-10-30: hosted providers approved for **SYNTHETIC DEMO use only**. Built behind the gateway; CI never calls a real provider.

## The rules this build enforces (each has a test in `tests/test_providers.py`)
- **Every registry row is `synthetic_only`.** `config/demo-providers.yaml` is refused whole if any row says anything else. **BUILD-SPEC.md §16.5 still forbids a hosted row from ever being `phi_approved`** ("models that touch patient data run locally"); changing that needs a new, explicit decision and a new register entry, not a config edit. A free-tier row is `synthetic_only` by definition.
- **The gateway keeps its data-policy filter.** A PHI request is never offered to these rows (`NoApprovedRoute`); `GatewayVision` sends PHI unless the deployment is demo + `synthetic_only`; `JevAdapter` refuses a PHI request itself.
- **Explicit timeouts**, from the spec's starting budgets (§16.1): vision 60 s per page (via `vlm_row`, required, no default), hosted LLM 20 s, Sarvam 5 s. Connect is 5 s everywhere; redirects are never followed.
- **Keys only from env names** (`GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `SARVAM_API_KEY`), never logged, never in an error message, never in `ci.yml` (guard test).
- **A required per-role daily spend ceiling with NO default** (`CAREFLOW_SPEND_CEILING_VLM_USD`, `_DECISION_USD`, `_TRANSLATION_USD`). In-memory, as decided (`RoleDailyCeilings`; a restart resets the day). An ordinary call that would pass it raises `SpendCeilingReached`, which the callers already turn into a visible hold or a manual task; the safety class is **never** budget-limited and its spend is still counted. A paid row must carry a non-zero price, otherwise its ceiling could never bite.
- **A provider is built only when every gate holds** (`careflow/provider_setup.py`): not CI; profile `demo` + `CAREFLOW_DATA_POLICY=synthetic_only`; the key; a `CAREFLOW_<PROVIDER>_VERIFIED` date (the day you ran the live check) that is not in the future; the role's ceiling. A missing gate is a named reason, never a crash and never a guess.
- **Nothing consumes the providers yet.** `app.state.providers` holds what was built; no route sends a page or a text to them. Wiring a feature to them (vision on uploaded documents, reply interpretation) is a later, separate step. **Reply interpretation with a real model stays blocked until the durable spend ledger exists** (earlier decision).
- **Red flags first.** The vision output is text read from an image: untrusted, screened by the deterministic rules before any other model call (CLAUDE.md rule 2). An empty transcription is a failure, never "clear". Translation is decorative; the native Tamil red-flag rules run on the original text regardless.

## Appendix B findings (from the providers' current documentation, NOT yet confirmed by a real call)
- **Jev is a router model, not a "decisions" endpoint.** OpenRouter documents `typesafe/jev-router` on the ordinary `/api/v1/chat/completions` endpoint with a `jev-router` plugin; it picks a downstream model per request and returns no calibrated probabilities. The spec's Appendix B line ("a decisions endpoint, distinct from /chat/completions") does not match. The adapter therefore asks for strict-JSON `{choice, confidence}` and spreads the confidence like the `llm_fast` fallback; the result is never calibrated and nothing may auto-approve on it. Because the router forwards the text to a model it chooses, the register entry says so. **The spec text is not edited here (rule: spec wording changes are asked first); this note is the record. Decision for the owner: keep Jev as a synthetic-only convenience engine, or drop it for `llm_fast` alone.**
- Gemini: `generateContent` REST, `x-goog-api-key`, `inline_data` image part, `usageMetadata.promptTokenCount/candidatesTokenCount`.
- Sarvam: `POST /translate`, `api-subscription-key`, `mayura:v1` input limit 1,000 characters, answer `translated_text`; billed per character.
- Prices in `config/demo-providers.yaml` are **placeholders** (labelled): confirm them against each pricing page. Free-tier limits are unconfirmed.

## The opt-in live check (yours to run; CI never does)
`uv run python scripts/provider_live_check.py --provider gemini|jev|sarvam --yes-synthetic` sends ONE synthetic request, prints what the service did (HTTP outcome in fixed words, answer key names, whether the usage fields exist, elapsed time), and tells you whether to record the `CAREFLOW_<PROVIDER>_VERIFIED` date. It reads only the repository's `.env`, refuses under CI or outside demo + `synthetic_only`, and never prints a key. For Gemini, `--image` takes a DUMMY image you made yourself.

## Create the three keys (you do this; do not paste them anywhere but your `.env`)
1. **Gemini** (`GEMINI_API_KEY`): Google AI Studio (aistudio.google.com), sign in, "Get API key", create a key in a NEW project used only for CareFlow dev. Leave billing off to stay on the free tier (that is what makes the row free-tier and synthetic-only by definition).
2. **OpenRouter** (`OPENROUTER_API_KEY`): openrouter.ai, sign in, Settings then Keys, create a key named `careflow-dev`. Set a **credit limit** on that key (a few dollars) so a runaway cannot spend more; add only that much credit.
3. **Sarvam** (`SARVAM_API_KEY`): dashboard.sarvam.ai, sign in, API Keys, create a key named `careflow-dev`. Use the free credits; do not add a card until the live check has passed.
4. Put them in the repository's `.env` (never `.env.cli`, never CI secrets, never the frontend), plus the three ceilings, e.g. `CAREFLOW_SPEND_CEILING_VLM_USD=0`, `CAREFLOW_SPEND_CEILING_DECISION_USD=1`, `CAREFLOW_SPEND_CEILING_TRANSLATION_USD=1` (your choice: there is no default; a free-tier row spends nothing).
5. Run the live check for each provider; for each that passes, add `CAREFLOW_GEMINI_VERIFIED=<date>`, `CAREFLOW_OPENROUTER_VERIFIED=<date>`, `CAREFLOW_SARVAM_VERIFIED=<date>`.

## Pre-launch blockers recorded
- No real patient data to any hosted provider, ever, without a new decision overriding §16.5 and the privacy owner's written confirmation.
- The durable spend ledger before any real reply-interpretation model call.
- The prices and free-tier limits confirmed (Appendix B).
