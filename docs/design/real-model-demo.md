# Real models for the synthetic demo: set-up guide

**Demo only.** Three hosted providers are approved for SYNTHETIC data in the `demo` profile (owner decision 2026-10-30): Gemini (reads a page image), Jev through OpenRouter (a decision model), Sarvam (translation). They are refused in the `clinical` profile and under any data policy other than `synthetic_only`; every registry row is `synthetic_only` and is never `phi_approved` (BUILD-SPEC.md 16.5); each has an entry in `data-flow-register.yaml`. **Never send a real letter, name or patient sentence to any of them.** Fakes stay the default: with no key, nothing here is built and the demo runs exactly as before.

The app calls these providers only through the gateway, when a feature is wired to them (`docs/design/hosted-providers.md` lists what is and is not wired). This page is about getting the keys, the spend limits and the first check right.

## The short version (use this)

Create the three keys, then run ONE command; it asks you to paste each key (hidden), asks for the spend limits, writes only your `.env`, and runs the live check last. You never type a variable name and never see a key.

1. **Gemini:** aistudio.google.com, sign in, make a NEW project used only for CareFlow dev, click **Get API key**, create the key in that project. **Spend limit: none to set; keep billing OFF** (with billing off it cannot charge).
2. **OpenRouter (Jev):** openrouter.ai, sign in, **Settings**, then **Keys**, create a key named `careflow-dev`. **Spend limit: set a credit limit of a few dollars on the key and add only that much credit.**
3. **Sarvam:** dashboard.sarvam.ai, sign in, **API Keys**, create a key named `careflow-dev`. **Spend limit: use the free credits; add no card yet; if the dashboard offers a usage limit, set it to a few dollars.**

Then, in PowerShell in the folder that holds your `.env` (the demo run folder, `E:\Claude\careflow-run`, or the repository):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\set_provider_keys.ps1
```

It refuses to run (and writes nothing) if git tracks the `.env`, if the `.env` is inside a git folder and not ignored, or if the `.env` does not say `CAREFLOW_PROFILE=demo` and `CAREFLOW_DATA_POLICY=synthetic_only`. It asks for the three daily ceilings and the per-run cap (press Enter to accept the value shown). Its last step runs `scripts/provider_live_check.py`, shows only PASS or FAIL, time and cost with any key masked, and records today's date as the VERIFIED setting for each provider whose lines all PASS. Then restart the backend. The longer explanation below is for reference; you do not need it to set up.

## 1. The `.env` variable names (put them ONLY in the repository's `.env`)

Never in `.env.cli`, never in CI secrets, never in the frontend, never in a chat. The values are yours.

| Variable | What it is |
|---|---|
| `GEMINI_API_KEY` | the Gemini key |
| `OPENROUTER_API_KEY` | the OpenRouter key (Jev runs through it) |
| `SARVAM_API_KEY` | the Sarvam key |
| `CAREFLOW_GEMINI_VERIFIED` | the date `YYYY-MM-DD` you ran the live check for Gemini and saw it pass |
| `CAREFLOW_OPENROUTER_VERIFIED` | the same for Jev |
| `CAREFLOW_SARVAM_VERIFIED` | the same for Sarvam |
| `CAREFLOW_SPEND_CEILING_VLM_USD` | the most the vision role may spend in one day, in US dollars (no default) |
| `CAREFLOW_SPEND_CEILING_DECISION_USD` | the same for the decision role (no default) |
| `CAREFLOW_SPEND_CEILING_TRANSLATION_USD` | the same for translation (no default) |
| `CAREFLOW_SPEND_CAP_PER_RUN_USD` | the most ONE job run may spend through the gateway, in dollars, above zero (no default) |
| `CAREFLOW_PROFILE=demo` and `CAREFLOW_DATA_POLICY=synthetic_only` | required for any of this to switch on |

A provider is built only when ALL of these hold: not running under CI, profile `demo` with data policy `synthetic_only`, its key present, its verified date not in the future, its role's daily ceiling set to a number, and the per-run cap set. Anything missing is logged by name (never by value) and that provider is simply unavailable.

## 2. Which are free-tier, and what to set on each dashboard

The prices in `config/demo-providers.yaml` are PLACEHOLDERS (labelled "to be confirmed", Appendix B). Confirm each on the provider's own pricing page before relying on it.

| Provider | Tier in this setup | The limit to set on the provider's own dashboard |
|---|---|---|
| Gemini (vision) | free tier, by keeping billing OFF on a new project used only for CareFlow dev | None to set: with billing off it cannot charge. Keep billing off: if the project has billing on, real charges are invisible to the app's ceiling |
| OpenRouter (Jev) | paid (a small credit) | Put a **credit limit on the key** (for example 2 US dollars) and add only that much credit |
| Sarvam (translation) | paid, with free starter credits | Use the free credits; add no card until the live check has passed; if the dashboard offers a spend or usage limit, set it to a few dollars |

Suggested starting values for the app's own limits (your choice, not a recommendation of the build): `CAREFLOW_SPEND_CEILING_VLM_USD=0` (free tier), `CAREFLOW_SPEND_CEILING_DECISION_USD=1`, `CAREFLOW_SPEND_CEILING_TRANSLATION_USD=1`, `CAREFLOW_SPEND_CAP_PER_RUN_USD=0.10`. The app's limits are a second wall behind the dashboards' limits, not a replacement.

## 3. Step by step

1. **Gemini key.** Open Google AI Studio (aistudio.google.com) and sign in. Create a NEW project used only for CareFlow dev. Choose "Get API key" and create a key in that project. Leave billing off. Copy the key straight into `.env` as `GEMINI_API_KEY=...`.
2. **OpenRouter key.** Open openrouter.ai and sign in. Under Settings, then Keys, create a key named `careflow-dev` and set its credit limit to a few dollars. Add only that much credit. Copy it into `.env` as `OPENROUTER_API_KEY=...`.
3. **Sarvam key.** Open dashboard.sarvam.ai and sign in. Under API Keys, create a key named `careflow-dev`. Copy it into `.env` as `SARVAM_API_KEY=...`. Use the free credits.
4. **Set the limits** from section 2 in the same `.env`, and confirm it says `CAREFLOW_PROFILE=demo` and `CAREFLOW_DATA_POLICY=synthetic_only`.
5. **Run the live check** by hand on your machine (CI never does):
   `uv run python scripts/provider_live_check.py --provider all --yes-synthetic`
   It sends ONE small request to each provider, using a built-in DUMMY letter for Gemini (a made-up block-letter page the script draws itself: it can draw nothing but the dummy text) and a made-up sentence for the others. Per provider it prints only PASS or FAIL in fixed words, the elapsed time, the token or character counts and the cost estimate. It never prints a key and never prints what the service answered (only how many of the expected dummy words came back). It refuses to run under CI, or unless `.env` says demo and synthetic-only, and it needs the explicit `--yes-synthetic`. Run one provider alone with `--provider gemini`, `--provider jev` or `--provider sarvam`.
6. **Record the date.** For each provider whose lines all say PASS, add `CAREFLOW_GEMINI_VERIFIED=<today>`, `CAREFLOW_OPENROUTER_VERIFIED=<today>` and `CAREFLOW_SARVAM_VERIFIED=<today>` to `.env`. A FAIL means the adapter's assumption about the real service is wrong: do not record the date; send the printed lines back.
7. **Restart the backend.** The providers are built at startup. Check the log: a provider that is not built says which gate failed, by name.

## 4. What the spend limits do

- Every call goes through the gateway: an ordinary call that would pass the per-run cap, the role's daily ceiling or the clinic's daily total is refused (a visible hold or a manual task, never a quiet skip). Safety-class calls are never budget-limited.
- The clinic's daily total is kept in the durable ledger (`ops.spend_ledger`, written by the queue worker's identity) whenever `CAREFLOW_WORKER_ACCOUNT_ID` is set; otherwise it is in memory and a restart resets it. The per-role daily ceilings are in memory (a restart resets them).
- The app cannot see a provider's own bill. The dashboard limits in section 2 are the real wall.

## 5. Stated limits

"Synthetic" is a label you declare, not something the app inspects; what keeps patient text away is the deployment gate, the gateway's data-policy filter and each adapter's own refusal of a patient-data request. Jev is a router: the downstream model varies per request, so its output is never used as evidence for acceptance, gold sets or calibration. Gemini's free tier may let Google use submitted content to improve its products: another reason no real page goes there. The rest is in `docs/design/hosted-providers.md`.

## After the keys: run one dummy letter through the whole flow
`docs/first-run-guide.md`, section "Optional: one dummy letter through the real models" (`scripts/demo_real_models.py`). What is wired, what stays fake and the limits: `docs/design/hosted-providers.md`, "Update 2026-11-14 (b)".

## Embeddings (the knowledge search)
The key script now also asks for **`CAREFLOW_SPEND_CEILING_EMBED_USD`** (Gemini's embedding model, free tier: `0` is the usual answer). Nothing else to create: it uses the same Gemini key. The live check's Gemini block now has lines E1 to E3 for it. Then `docs/first-run-guide.md`, "Optional: the knowledge search over invented cards".

## A good run, and what the words mean (verified on the demo clinic, 2026-11-14)
**What a passing run looked like** (`scripts/demo_real_models.py --clinic-id <clinic> --fresh`, synthetic dummy letter, the three providers real): stage 1 text read in **4.4 s** (an earlier run: 5.5 s) by Gemini through the gateway; stage 2 the rules **clear** (they ran, on that text, before any decision model); stage 3 the decision model (Jev via OpenRouter, model `typesafe/jev-router`) **suggested** a card with probabilities **0.51 / 0.49**; a triage proposal was stored with its engine, model id, confidence, rule and each alternative's probability and **waits for a person**; stage 4 the Sarvam Tamil draft was **suggested** and not sent. Expect about 2 to 6 s for the page, a few seconds for the decision, about a second for the draft. **Honest reading of the numbers:** the "cards" are two invented placeholders, so the question has no informative answer; 0.51 against 0.49 is a near-tie that passed only because the model put its choice a hair above the other. The confidence is **never calibrated** and nothing may act on it. The data is synthetic only; every provider stays refused in the clinical profile.

**`--fresh`.** Without it the script uses a fixed document id and a second run only reports the first result (no model is called). `--fresh` creates a NEW dummy letter document (a new id) and runs the whole path again; the earlier document is untouched.

**The words a stage can print.** `NOT READ` (no text was read; then the rules show `NOT RUN`, never `clear`), and a fixed-words reason after it: `budget refused`, `timed out x3`, `provider error (retry-worthy) [... status 503]`, `provider refused the request [... status 404]` (a REAL provider or HTTP error), `gate missing`. **`answer rejected: ...`** is different: the provider answered and OUR validation rejected the answer (for the decision: the model's choice is not its own strictly top-ranked option, a tie, probabilities that do not add up, an option that was not offered). It is never worded as a provider refusal. The rule is not loosened.

**What the decision stage asks.** One probability per offered option, adding up to 1 (rounding within 0.02 is scaled away), and the choice must be the option with the strictly highest probability. If the answer is rejected, **one repair request** restates the rule and why it was rejected; both requests' tokens are counted; an answer accepted only after the repair is labelled **`jev+repair`**; a second rejection ends as `answer rejected: ... (after one repair request)` and the case goes to a person, as before.

**Why a failing stage can take about two minutes.** Each model call is allowed 60 s (spec 16.1 starts vision at 60 s per page), and the gateway makes up to **three attempts per route** with a short pause between them, so a provider that does not answer costs up to 3 x 60 s (about 3 minutes). The first live failure took 123.7 s: two timeouts and then a quick failure. The script now says which. It is a worst case, not a normal run: a healthy page takes seconds. The scan-style dummy page is deliberately small (3.5 KB) and the Gemini request has an output cap (8192 tokens) so a page cannot loop for the whole timeout.
