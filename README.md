# CareFlow

CareFlow is a back-office system for a single outpatient clinic. It helps front-desk staff, nurses and doctors with the work around a patient visit: turning a referral letter into a triage proposal, booking and re-filling appointment slots, answering patient messages, keeping track of results, care plans and follow-ups, and recording who did what. **People make every clinical and scheduling decision; the software prepares, checks and records.**

> ## Do NOT use real patient data
> **Nothing in this system is approved for real patients.** Every environment runs on made-up (synthetic) data. The clinical content (red-flag rules, protocol cards, intake questions, thresholds), the legal text (consent notices, grievance and breach wording, retention periods) and the production hosting are all missing on purpose, waiting for named clinicians, counsel and the owner. Until the pre-launch list below is closed, never put a real name, letter, message or result into it.

## Where things stand (2026-11-14)

- **All build phases are built to "mechanism level" on synthetic data, with fake components.** That means the database, the rules, the routes, the approvals, the audit trail and the tests exist and run; the clinical content and the real services behind them do not. Each phase's gap is listed in [`docs/gap-audit.md`](docs/gap-audit.md) (every item marked FAKE, NOT BUILT, DECISION or UNMEASURED, with a reason).
- **Built (mechanism):** staff sign-in with a required second factor; patient records with encrypted names and keyed lookups; two-person merge and erasure; scheduling with holds, bookings, a priority waiting list, cancellations and offers; the referral "Front Door" (upload, read, red-flag check, triage proposal, human review); the message and reply paths; review tasks, alerts and an audit log; break-glass access; the patient portal's registration, consent and rights flows; clinical results, notes, medications, care plans, care gaps and visit readiness (behind flags); a voice-session monitor (no voice engine); the two-doctor approval, authoring, withdrawal and two-person retirement of red-flag rules; a durable spend ledger; an evaluation runner with the 100% red-flag recall gate.
- **Not approved or not decided:** everything touching real patients. See "Before any real patient" below.
- The frontend (a React app under `frontend/`) is a separate workstream; this page is about the backend.

## Tools used

- **Backend:** Python 3.12, FastAPI, SQLAlchemy over asyncpg, Pydantic v2, LangGraph (the few long-running flows), `uv` for packages, `ruff` for lint, `pytest` for tests.
- **Database and sign-in:** a hosted Supabase project (Postgres and Auth); separate projects for development and CI. Row-level security does the clinic isolation. **No Docker anywhere.**
- **Frontend:** React, TypeScript, Vite, TanStack Query, Vitest and Playwright (`frontend/`).
- **CI:** GitHub Actions: lint, secret scanning (gitleaks), the OpenAPI snapshot check, migrations, an offline test suite and the live database tests in shards against the CI Supabase project, as the same restricted database role the app uses.

## The AI roles, and which are fake

Every model call goes through one gateway that checks the data policy, the budget and the audit trail. **Today every role is a fake (a fixed stand-in) by default.**

| Role | What it would do | State |
|---|---|---|
| Vision | read a scanned referral letter into text | fake; Gemini is wired for a SYNTHETIC demo only, off unless you set it up |
| Decision | pick between a few options (for example a triage band) for a person to confirm | fake; "Jev" through OpenRouter for a synthetic demo only |
| Translation | translate text (Tamil, English) | fake; Sarvam for a synthetic demo only |
| Language models, embeddings, reranking, judge, voice | draft, search, score, speak | fake or not built; no real model is approved for patient text |

The three hosted demo providers are refused outside the demo profile with synthetic data, are never marked approved for patient data, have a required spend cap, and are described step by step in [`docs/design/real-model-demo.md`](docs/design/real-model-demo.md). Nothing a model says is ever final: the deterministic red-flag rules always run first, and a model can only raise a case, never lower one the rules already flagged.

## The safety rules (short)

The full list is [`CLAUDE.md`](CLAUDE.md); the reasons are in [`BUILD-SPEC.md`](BUILD-SPEC.md). In plain words:

1. No one writes clinical content (a rule, a threshold, a question) except a named clinician; the build leaves a marked placeholder instead.
2. The red-flag rules run before any model, on every text and voice path; "could not check" is never "all clear".
3. A missing or unverified fact satisfies no rule; unknown is never true or false.
4. Writes carry an idempotency key; a step that pauses never also causes a side effect.
5. The database, not application code, keeps one clinic's data from another; there is no agent-owned memory of patients.
6. Patient data never leaves the approved boundary; every external service is listed and approved first.
7. Only human decisions can become training labels; nothing from a demo or synthetic deployment ever does.
8. Migrations are never edited once merged; every new route has its own role-matrix test.

## How to run it

- **First run and day-to-day commands:** [`docs/first-run-guide.md`](docs/first-run-guide.md) (the exact windows and commands; migrate before you start the app, which refuses to start on an un-migrated database).
- **For a developer:** [`docs/README-project.md`](docs/README-project.md) (architecture on a page, how the phases fit, where each rule is enforced, how to run everything, every setting, all migrations in order, what is fake or undecided).
- **Demo:** [`docs/demo-walkthrough.md`](docs/demo-walkthrough.md). **Tests:** `uv run ruff check .` and `uv run pytest -q tests --ignore-glob='*live*' --ignore=tests/test_api_role_matrix.py` for the offline suite; the live tests run in CI.

## Before any real patient (the pre-launch blockers)

The full numbered list, with an owner and closing evidence for each, is [`docs/design/pre-launch-checklist.md`](docs/design/pre-launch-checklist.md). The main ones:

- **Clinical content** written and approved by named clinicians: red-flag rules (two doctors approve each), protocol cards, intake questions, thresholds, care-gap and visit-readiness rules, medication codes; a gold set of reviewed cases that scores 100% on red-flag recall.
- **Legal text** from counsel: consent notices, the grievance and rights procedure, retention periods, breach wording. AI-prepared drafts and option sheets are in `docs/drafts/` and `docs/approval-pack/`; **none is approved.**
- **Real services chosen and approved:** an approved model route for any model that sees patient text, a malware scanner for uploads, SMS and e-mail providers, hosting in an India region with backups and drills, observability.
- **Owner and clinic decisions:** who holds which accounts (at least three doctors per clinic for authored rules; no one person holding two doctor accounts), the clinic's own settings and the privacy owner's written confirmations. The owner database credential must not be used for day-to-day work in production.
- **Tests with the real second factor and real accounts** that the demo could not exercise.

## Where to look

[`BUILD-SPEC.md`](BUILD-SPEC.md) (the specification), [`PATIENT-WORKFLOWS.md`](PATIENT-WORKFLOWS.md) (patient journeys), [`metrics-catalog.yaml`](metrics-catalog.yaml) (every metric and alert), [`docs/gap-audit.md`](docs/gap-audit.md) (what is not real yet), [`docs/design/`](docs/design/) (a design note per area, the threat model, the grants ledger), [`docs/runbooks/`](docs/runbooks/) (operating procedures).
