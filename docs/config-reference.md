# Configuration reference

Every `CAREFLOW_*`, `FEATURE_*` and secret name the code reads, with its meaning, default, the profile it is allowed in, what it must be before real patients, and what refuses when it is wrong. **No value appears here, and none may be pasted into chat, issues, commits or logs.**

`tests/test_config_reference.py` fails if the code reads a setting this file does not list, or this file lists a name the code no longer reads. The file lists the NAMES the application and the owner-run scripts read; the settings that only a test or a CI workflow sets are not here.

**Reading the table.** *Profiles* says where the setting is allowed; a demo-only item makes the application refuse to start anywhere else. *Before real patients* is what it must be for a launch, not what it is today: this file does not approve anything (see `docs/design/pre-launch-checklist.md`). *Kind*: ID (identity or connection), SECRET, SWITCH (off unless exactly `on`), REQ (required setting, no default: unset means the feature says NOT CONFIGURED), LIMIT (a numeric limit), DEMO (a demo gate or verification date).


## Identity of the deployment

These decide which rules apply to everything else.

| Name | Kind | Meaning | Default | Profiles | Before real patients | What refuses |
|---|---|---|---|---|---|---|
| `CAREFLOW_PROFILE` | ID | Operating profile: `demo` or `clinical`. | unset or anything else means `clinical` (unknown is not permissive) | demo and clinical | `clinical` | Every demo gate below refuses to start unless this is `demo`. |
| `CAREFLOW_DATA_POLICY` | ID | Deployment-level declaration of what data may be here: `synthetic_only` or `phi_approved`. | unset or any other value is NOT `synthetic_only` | demo and clinical | must NOT be `synthetic_only`; `phi_approved` only after the owner's approvals | The demo packs, hosted providers and `CAREFLOW_REQUIRE_AAL2=false` refuse to start unless profile=demo AND this is `synthetic_only`. |
| `CAREFLOW_ENV` | ID | Which hosted project this env file points at: `dev` or `ci`. Read by the owner-run scripts, not by the server. | unset | scripts only | not used in production | `scripts/seed_*.py` refuse unless `dev`. |
| `CAREFLOW_DEV_PROJECT_REF` | ID | The dev project's reference, typed by the owner, so a seed script can prove the database URL belongs to the dev project. | unset | scripts only | not used in production | Seed scripts refuse when it is missing or differs from the project in `SUPABASE_URL` and the migrator URL. |

## Connections and secrets

Never printed, logged or committed; CI scans for them. Rotation is in `docs/ops-runbook.md`.

| Name | Kind | Meaning | Default | Profiles | Before real patients | What refuses |
|---|---|---|---|---|---|---|
| `SUPABASE_URL` | ID | The hosted project URL (`https://<ref>.supabase.co`). Its host decides the token issuer. | none, REQUIRED | demo and clinical | the production project, in an India region (spec 16.5) | The server does not start without it (settings error). |
| `SUPABASE_JWKS_URL` | ID | Where to fetch the token signing keys. | `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` | demo and clinical | leave unset | Tokens are selected by `kid`; a token that cannot be verified is one fixed 401. |
| `SUPABASE_JWT_SECRET` | SECRET | Legacy shared secret. Mutually exclusive with JWKS; stays blank for these projects. | blank | demo and clinical | blank | Not read by the server. |
| `SUPABASE_PUBLISHABLE_KEY` | SECRET | Public (publishable) key, for sign-in from a browser or a spike script. | none | demo and clinical | as issued | Not a secret by itself; never put the secret key where this goes. |
| `SUPABASE_SECRET_KEY` | SECRET | The project's secret key (`sb_secret_...`). One-time setup and the Auth admin call only; never used in a test assertion. | none | demo and clinical | held only where an owner-run job needs it | `CAREFLOW_AUTH_ADMIN_VERIFIED` must also name a date before the admin adapter is built. |
| `DATABASE_URL_APP` | SECRET | The application's database connection: restricted `careflow_app` role, transaction-mode pooler (port 6543), no driver-side prepared statements. | none, REQUIRED | demo and clinical | the production project's pooler URL for the restricted role | The server does not start without it; `schema_check` refuses to start on a database missing the migrations this code needs. |
| `DATABASE_URL_MIGRATOR` | SECRET | The migration runner and owner-run scripts: session-mode pooler (port 5432), the owner role. | none | owner-run only | kept off the running server | `python -m careflow.migrate` stops with an error naming the variable if it is unset; it is never read by the web server. |
| `CAREFLOW_ENC_SECRET_V<n>` | SECRET | AES-256-GCM key for key version n (32 random bytes, base64). One per version; versions are numbered from 1 (`CAREFLOW_ENC_SECRET_V1` is the first). | none, REQUIRED (at least V1) | demo and clinical | generated and stored in the hosting provider's secret store; never in the repo | Loading fails with a fixed `CryptoConfigError` if none is found, or if a version has only one of the pair. |
| `CAREFLOW_HMAC_SECRET_V<n>` | SECRET | HMAC-SHA256 key (at least 32 bytes, base64) for blocking tokens, key version n (`CAREFLOW_HMAC_SECRET_V1` is the first). Must pair with the same n above. | none, REQUIRED (at least V1) | demo and clinical | as above | As above. |
| `CAREFLOW_ACTIVE_KEY_VERSION` | ID | Which key version NEW demographics are encrypted under. | optional while exactly one version is loaded; REQUIRED once several are | demo and clinical | set explicitly during and after a rotation | With several versions loaded and this unset, registration answers a visible 503. |
| `CAREFLOW_LABEL_PSEUDONYM_KEY` | SECRET | Secret for pseudonymising reviewer identity in a training-label export (HMAC). | none | demo and clinical | set before any export | Unset means no export is produced. |
| `GEMINI_API_KEY` | SECRET | Hosted Gemini key (vision, embeddings) for the synthetic demo only. | none | demo only (synthetic data) | must not be set in a `clinical` deployment | The provider adapter is refused unless demo + synthetic_only and `CAREFLOW_GEMINI_VERIFIED` names a date. |
| `OPENROUTER_API_KEY` | SECRET | OpenRouter key for the Jev decision engine (synthetic demo only). | none | demo only (synthetic data) | must not be set in `clinical` | Same refusal; `CAREFLOW_OPENROUTER_VERIFIED`. |
| `SARVAM_API_KEY` | SECRET | Sarvam translation key (synthetic demo only). | none | demo only (synthetic data) | must not be set in `clinical` | Same refusal; `CAREFLOW_SARVAM_VERIFIED`. |
| `CORS_ALLOWED_ORIGINS` | ID | Comma-separated EXACT browser origins allowed to call the API. | unset means none | demo and clinical | the production site origin only | An origin not listed gets no CORS headers; no wildcard. |
| `CAREFLOW_SIGN_IN_URL` | ID | The sign-in link placed in notifications. | `(sign-in link not configured)` | demo and clinical | the real sign-in address | Not validated; a placeholder in a message is a visible fault. |

## Second factor and sessions

| Name | Kind | Meaning | Default | Profiles | Before real patients | What refuses |
|---|---|---|---|---|---|---|
| `CAREFLOW_REQUIRE_AAL2` | SWITCH | Every staff route needs a verified second factor (aal2 + TOTP). | ON (true) | `false` allowed in demo + synthetic_only only | must stay on | The application refuses to start with it off anywhere except demo + synthetic_only (`MfaSettingRefused`). |
| `CAREFLOW_STEP_UP_MAX_AGE_SECONDS` | LIMIT | How recent a second factor must be for a sensitive action (merge, publish, mode change, erasure, role change). | none, REQUIRED | demo and clinical | set to the owner's chosen window | Unset: the sensitive routes answer a visible 503 and log an error. |
| `CAREFLOW_AUTH_ADMIN_VERIFIED` | DEMO | A date (not in the future) on which the Auth admin call was confirmed against the dev project. | unset | demo and clinical | set only after a real confirmation | Unset: the Auth admin adapter is not built; invitations and recovery stay on the fake port. |
| `CAREFLOW_REQUIRED_ROLES` | REQ | Model roles whose lack of a `phi_approved` route makes a `clinical` deployment report NOT ready. | none (unset means not configured, so not ready) | `clinical` | the launch-blocking roles | `GET /readyz` answers not ready. |

## Worker and queue

| Name | Kind | Meaning | Default | Profiles | Before real patients | What refuses |
|---|---|---|---|---|---|---|
| `CAREFLOW_RUN_WORKER` | SWITCH | Run the in-process worker that drains the queue. | off | demo and clinical | on, in the process the owner chooses | On without `CAREFLOW_WORKER_ACCOUNT_ID`: the worker is NOT started and queued jobs wait (logged). |
| `CAREFLOW_WORKER_ACCOUNT_ID` | ID | Account id of the `queue_worker` the worker runs as; row-level security decides what it sees. | none | demo and clinical | the production worker account | See above. |
| `CAREFLOW_DURABLE_RUNS` | DEMO | `memory` keeps demo run state in memory on a database without migrations 0037/0038. | durable (own Postgres tables) | demo only (synthetic data) | unset | Only read by the demo Front Door components. |

## Feature switches

Each is off unless exactly `on` (the `FEATURE_*` flags accept `1`, `true`, `yes` or `on`). Routes answer 503 after the identity check while off.

| Name | Kind | Meaning | Default | Profiles | Before real patients | What refuses |
|---|---|---|---|---|---|---|
| `CAREFLOW_PATIENT_MESSAGING` | SWITCH | A patient sends and reads messages (migration 0082). | off | demo and clinical | on only after the account-stage rules, the rate limit and consent texts exist | Also needs the keys and `CAREFLOW_MESSAGE_MAX_PER_MINUTE`; sending is 503 without the limit. |
| `CAREFLOW_PATIENT_BOOKING` | SWITCH | A patient books and cancels (0083). | off | demo and clinical | on only with the owner's bookable visit types listed | Needs `sched.patient_bookable_visit_types` rows; none means nothing is bookable. |
| `CAREFLOW_PATIENT_VISIT_PREP` | SWITCH | A patient sees and marks the clinic's own checklist for their own visit (0089). | off | demo and clinical | on once the clinic's checklist exists | With no checklist the patient is told so; never invented. |
| `CAREFLOW_FAMILY_LINKS` | SWITCH | Family links: request, front-desk verification, revoke (0091, 0093). Deny by default. | off | demo and clinical | on only after counsel's rules are entered into `iam.family_link_rules` | No rule row means `not_configured`; a verified link grants nothing. |
| `CAREFLOW_KNOWLEDGE_EDITOR` | SWITCH | The card and template editor (0090, 0092, 0093). | off | demo and clinical | on once clinicians will author | A doctor who is not the author approves; nothing is seeded. |
| `CAREFLOW_KNOWLEDGE_SEARCH` | SWITCH | Knowledge search over approved cards (0085). | off | demo and clinical | on only with an approved embedding route (none in `clinical` today) | Without an embedding route search answers 503 or abstains. |
| `CAREFLOW_VECTOR_REBUILD` | SWITCH | Nightly reconcile of the card vector index from Postgres: indexes every approved card version missing from the index, once a day (spec 16.6). | off | demo only (synthetic data) | off (refused in `clinical`) | On outside demo + synthetic_only: the application refuses to start (`RebuildRefused`). Needs an embedding route and `CAREFLOW_KNOWLEDGE_EDITOR=on`. |
| `FEATURE_CARE_JOURNEY` | SWITCH | Builds the Care Journey agent graph. | off | demo and clinical | off until the plan parser has an approved model route | With it off no Care Journey node exists (structural rule). |
| `FEATURE_VISIT_READINESS` | SWITCH | Builds the Visit Readiness agent graph. | off | demo and clinical | off until clinician content exists | As above. |
| `FEATURE_VOICE_CHANNEL` | SWITCH | Clinic-level voice channel flag (the patient must also set `voice_enabled`). | off | demo and clinical | off: no engine exists | The session route answers 503 without an engine and the transcript retention period. |

## Demo gates

Each loads labelled DEMO placeholders and refuses to start outside demo + synthetic_only.

| Name | Kind | Meaning | Default | Profiles | Before real patients | What refuses |
|---|---|---|---|---|---|---|
| `CAREFLOW_DEMO_PHASE3` | DEMO | Switch (`1`) for the Phase 3 demo scheduling placeholders and the demo Front Door. | off | demo only (synthetic data) | unset | Set outside demo + synthetic_only: the application refuses to start. |
| `CAREFLOW_DEMO_PHASE4` | DEMO | Switch (`1`) for the Phase 4 demo placeholders (reason codes, deadlines, watchdog limits, consent notice, visit checklist). | off | demo only (synthetic data) | unset | Same refusal. |
| `CAREFLOW_DEMO_REAL_MODELS` | DEMO | Switch to call the hosted demo providers (Gemini, OpenRouter, Sarvam) with synthetic data. | off, the fakes stay | demo only (synthetic data) | unset | Needs demo + synthetic_only, the demo Front Door, the provider key, the `*_VERIFIED` date and the spend ceilings, or the fakes stay. |
| `CAREFLOW_SCHED_*` | DEMO | Scheduling settings; any value carrying the demo label is refused outside the demo. | none | demo and clinical | real, owner-approved values | A value with the demo label outside demo + synthetic_only: refuse to start. |
| `CAREFLOW_GEMINI_VERIFIED` | DEMO | A date on which the Gemini call was verified by a real run. | unset | demo only (synthetic data) | n/a | Unset: the adapter is not built. |
| `CAREFLOW_OPENROUTER_VERIFIED` | DEMO | A date on which the OpenRouter (Jev) call was verified. | unset | demo only (synthetic data) | n/a | Unset: the adapter is not built. |
| `CAREFLOW_SARVAM_VERIFIED` | DEMO | A date on which the Sarvam call was verified. | unset | demo only (synthetic data) | n/a | Unset: the adapter is not built. |
| `CAREFLOW_STORAGE_BUCKET` | REQ | The Supabase Storage bucket for document uploads. | none | demo only until the overwrite spike (Appendix B) is closed | set after the spike | Real intake is built only if this, `CAREFLOW_STORAGE_VERIFIED` and a secret key all hold; otherwise upload routes answer 503. |
| `CAREFLOW_STORAGE_VERIFIED` | DEMO | A date on which `scripts/storage_spike.py` was run against the project. | unset | demo until closed | set after the spike | Unset: the storage adapter is not built. |
| `CAREFLOW_AUTONOMY_MODE` | REQ | The clinic's autonomy mode for the demo Front Door. | unset or unrecognised means `shadow` | demo (real modes are stored in the database) | n/a: the stored history decides | Unrecognised is `shadow`. |
| `CAREFLOW_AUTONOMOUS_MODE_ENABLED` | SWITCH | Allows the two-person act that moves a clinic to `autonomous`. | off (503) | demo and clinical | off until the evidence gates are met (spec 14.7) | Even when on, a stored `autonomous` changes nothing in `clinical` without the sign-off below. |
| `CAREFLOW_CLINICAL_AUTONOMY_SIGNOFF` | REQ | Evidence that a clinical lead signed off autonomy for the `clinical` profile. | none | `clinical` | present only with a real sign-off | Absent: `autonomous` has no effect in `clinical`. |

## Required settings with no default

Clinical, legal or commercial values the owner supplies. Unset means the feature says NOT CONFIGURED; the code never guesses. Demo placeholders exist only under the demo gates above.

| Name | Kind | Meaning | Default | Profiles | Before real patients | What refuses |
|---|---|---|---|---|---|---|
| `CAREFLOW_REVIEW_REASON_CODES` | REQ | Comma-separated reason words for reject/edit (12.2). | none | demo and clinical | the clinic's real list (they become training labels) | Unset: no code is accepted and none is required. |
| `CAREFLOW_REVIEW_DEADLINE_MINUTES` | REQ | JSON map of task kind to deadline minutes (12.3). | none | demo and clinical | clinical-governance values | Unset: no deadline escalation for that kind. |
| `CAREFLOW_WATCHDOG_LIMITS` | REQ | JSON `{red_flag_minutes, queue_age_seconds}` for the in-app watchdog. | none | demo and clinical | owner's limits | A check with no limit does not run and says so. |
| `CAREFLOW_RUBBER_STAMP_POLICY` | REQ | JSON thresholds for rubber-stamp monitoring. | none | demo and clinical | clinical lead's thresholds | Unset: no rubber-stamp flag. |
| `CAREFLOW_CANARY_RATE` | REQ | Canary share of the review queue (spec 1-2%). | none: no canaries | demo and clinical | owner's rate | Must be above 0 and at most 0.05; otherwise refused. |
| `CAREFLOW_AUTO_REVIEW_SAMPLE_RATE` | REQ | Share of auto-approved cases pulled for after-the-fact review (spec ~5%). | none | demo and clinical | owner's rate | Unset: nothing is sampled. |
| `CAREFLOW_TRACE_SAMPLE_RATE` | REQ | Share of ordinary successes exported to tracing. | none: ordinary successes not exported | demo and clinical | owner's rate | Never read as 'all'. |
| `CAREFLOW_CARE_GAP_RULES` | REQ | Recurrence rule definitions (clinician content). | none | demo and clinical | clinician-authored rules | Unset: the care-gap routes answer 503. |
| `CAREFLOW_CARE_GAP_ALERT_DAYS` | REQ | Age in days after which a care-gap item raises an overdue alert. | none | demo and clinical | owner's age | Unset: the check does not run and reports `alert_unconfigured`. |
| `CAREFLOW_CARE_PLAN_TASK_PRIORITY` | REQ | Priority of the task raised for a care-plan step. | none | demo and clinical | clinical decision | Unset (with the role): the care-plan producer is not built. |
| `CAREFLOW_CARE_PLAN_TASK_ROLE` | REQ | Role the care-plan task goes to. | none | demo and clinical | clinical decision | As above. |
| `CAREFLOW_VISIT_READINESS_TASK_PRIORITY` | REQ | Priority of the Visit Readiness escalation task. | none | demo and clinical | clinical decision | Unset: the producer is not built. |
| `CAREFLOW_VISIT_READINESS_CONTENT` | REQ | The clinic's own intake questions and readiness checklist (also the patient visit-preparation items). | none (the demo pack carries labelled placeholders) | demo and clinical | clinician-authored | Unset: nothing is asked and a patient sees 'Nothing to prepare for this visit.' |
| `CAREFLOW_REVOKE_TASK_PRIORITY` | REQ | Priority of the task raised when an identity confirmation is revoked. | none | demo and clinical | clinical decision | Missing: the revoke route needs it in the request. |
| `CAREFLOW_REVOKE_TASK_ROLE` | REQ | Role that receives that task. | none | demo and clinical | clinical decision | As above. |
| `CAREFLOW_REVOKE_ALERT_REASONS` | REQ | Revoke reasons that also raise an admin alert. | none: never | demo and clinical | owner's list | Unset: no admin alert. |
| `CAREFLOW_ERASE_REASONS` | REQ | Closed list of reasons a patient may be erased for. | none | demo and clinical | privacy owner's list | Unset: the erase route answers 503. |
| `CAREFLOW_GRIEVANCE_CATEGORIES` | REQ | Closed grievance categories. | none | demo and clinical | privacy owner's list | Unset: the grievance routes answer 503. |
| `CAREFLOW_SEX_WORDS` | REQ | Closed words offered for a patient's sex. | none | demo and clinical | privacy owner's list | Unset: the profile route cannot set it. |
| `CAREFLOW_MEDICATION_CODES` | REQ | The medication vocabulary (codes only, no dose). | none | demo and clinical | clinician-authored | Unset: the medication routes answer 503. |
| `CAREFLOW_CONSENT_NOTICES` | REQ | Consent notice texts per purpose (counsel's wording). | none | demo and clinical | counsel-approved | Unset: nothing can be consented to. Read by the owner-run seed script only. |
| `CAREFLOW_CONSENT_LABELS` | REQ | Patient-facing names of the consent purposes. | none | demo and clinical | counsel-approved | Unset: label is null. |
| `CAREFLOW_CLINIC_TIME_ZONE` | REQ | The clinic's IANA time zone name. | none | demo and clinical | the clinic's zone | Unset or malformed: the profile says null; never a guess. |
| `CAREFLOW_BREAK_GLASS_REASONS` | REQ | Break-glass reason words (owner-run seed). | none | demo and clinical | owner's list | Read by `scripts/seed_break_glass_reasons.py`; with none, break-glass cannot be opened. |
| `CAREFLOW_BREAK_GLASS_MINUTES` | REQ | Break-glass window length (1 to a cap), owner-run seed. | none | demo and clinical | owner's limit | Seed refuses out-of-range values. |
| `CAREFLOW_BREAK_GLASS_MAX_REOPENS` | REQ | Cap on re-opens per person, per patient, per 24 hours. | none | demo and clinical | owner's limit | As above. |
| `CAREFLOW_MESSAGE_MAX_PER_MINUTE` | LIMIT | Per-account patient-facing message limit per 60 s (1 to 10000). | none: no limit on staff routes; patient send needs it | demo and clinical | set before enabling patient messaging | Patient send answers 503 without it; a bad value means no limit enforced. |
| `CAREFLOW_PUBLIC_GRIEVANCE_IP_PER_HOUR` | LIMIT | Per-address hourly cap for the public grievance route. | none | demo and clinical | owner's cap | Unset: the public route answers 503. |
| `CAREFLOW_PUBLIC_READ_IP_PER_MINUTE` | LIMIT | Per-address per-minute cap for public reads (landing content). | none | demo and clinical | owner's cap | 503 until the content list AND this are set. |
| `CAREFLOW_PATIENT_VIEW_MAX_OPENS_PER_MINUTE` | LIMIT | Record opens per user per minute. | none | demo and clinical | owner's limit | Unset: opening a record is unavailable. |
| `CAREFLOW_PATIENT_VIEW_MAX_LABEL_ROWS_PER_MINUTE` | LIMIT | List rows per user per minute (labels and search hits). | none | demo and clinical | owner's limit | Unset: unavailable. |
| `CAREFLOW_PATIENT_VIEW_PAGE_LIMIT` | LIMIT | Rows per page when opening patient lists; can only be lowered from the hard cap of 25. | 25 (the hard cap) | demo and clinical | leave at 25 or lower | A value above 25 or below 1 is refused at load. Only `CAREFLOW_PATIENT_VIEW_` names are read (a generic page-limit name is deliberately not). |
| `CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS` | REQ | Voice transcript retention (spec: 30 days). | none: not assessed | demo and clinical | counsel/clinical lead | Unset: the purge does not run and the session route stays 503. |
| `CAREFLOW_RETENTION_CHECKPOINT_DAYS` | REQ | Checkpoint/trace retention (spec: 90 days). | none: not assessed | demo and clinical | owner's period | Unset: not assessed by the report-only job. |
| `CAREFLOW_RETENTION_CLINICAL_RECORD_DAYS` | REQ | Clinical record retention. | none: not assessed | demo and clinical | counsel/clinic policy | Unset: not assessed. |
| `CAREFLOW_COST_PLATFORM_FLOOR` | REQ | P in the cost canvas (platform floor). | none: owner-null | demo and clinical | owner's figure | Unset: the canvas reports it unknown. |
| `CAREFLOW_COST_REVIEWER_RATES` | REQ | F's per-role reviewer rate. | none: owner-null | demo and clinical | owner's figure | Unset: F and the total are null. |
| `CAREFLOW_SPEND_CAP_PER_RUN_USD` | REQ | Most one job run may spend through the gateway. | none | demo only (synthetic data) | owner's cap | Missing or bad: the provider is not built. |
| `CAREFLOW_SPEND_CEILING_VLM_USD` | REQ | Daily ceiling, vision role. | none | demo only (synthetic data) | owner's cap | Missing, bad, negative or infinite: the provider is not built. |
| `CAREFLOW_SPEND_CEILING_DECISION_USD` | REQ | Daily ceiling, decision role. | none | demo only (synthetic data) | owner's cap | As above. |
| `CAREFLOW_SPEND_CEILING_TRANSLATION_USD` | REQ | Daily ceiling, translation role. | none | demo only (synthetic data) | owner's cap | As above. |
| `CAREFLOW_SPEND_CEILING_EMBED_USD` | REQ | Daily ceiling, embedding role. | none | demo only (synthetic data) | owner's cap | As above. |
| `CAREFLOW_RETRIEVAL_MIN_DENSE_SIMILARITY` | REQ | Minimum dense similarity for a retrieved card. | none | demo and clinical | calibrated on a clinician-reviewed gold set | Unset: the retriever abstains with `no_calibrated_policy`. |
| `CAREFLOW_RETRIEVAL_MIN_BM25_RATIO` | REQ | Minimum BM25 ratio. | none | demo and clinical | calibrated | As above. |
| `CAREFLOW_CONTEXT_BUDGET_TOKENS` | REQ | Token budget of the context assembler. | none | demo and clinical | owner's budget | As above. |
| `CAREFLOW_RETRIEVAL_EVAL_K` | REQ | k for the retrieval quality report. | none | demo and clinical | clinical lead | Unset: the report says NOT MEASURED. |
| `CAREFLOW_RETRIEVAL_EVAL_MIN_RECALL` | REQ | Recall bar for the retrieval gate. | none | demo and clinical | clinical lead | As above. |
| `CAREFLOW_RETRIEVAL_EVAL_MIN_MRR` | REQ | MRR bar for the retrieval gate. | none | demo and clinical | clinical lead | As above. |
| `CAREFLOW_EVAL_RULES_FILE` | REQ | Path to the clinician-approved red-flag rules the gold-set runner scores. | none | demo and clinical | the approved rules file | Unset: the gold-set run is NOT MEASURED. |
| `CAREFLOW_EVAL_BAR_<GATE>` | REQ | Bar for a threshold gate, one variable per gate (prefix `CAREFLOW_EVAL_BAR_`). | none | demo and clinical | clinical lead | Unset: the gate is NOT MEASURED, never passed. |
| `CAREFLOW_EVAL_BASELINE_<GATE>` | REQ | Three comma-separated baseline runs for a threshold gate (prefix `CAREFLOW_EVAL_BASELINE_`). | none | demo and clinical | from three real runs | Unset: no variance band. |
