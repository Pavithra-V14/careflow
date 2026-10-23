# CareFlow

An agentic patient-access and care-coordination platform for a single outpatient clinic.

This repository is built to a written specification. **Read these first, in order:**

1. [`CLAUDE.md`](CLAUDE.md) — the non-negotiable rules.
2. [`BUILD-SPEC.md`](BUILD-SPEC.md) — the full specification. Build order is Appendix D.
3. [`PATIENT-WORKFLOWS.md`](PATIENT-WORKFLOWS.md) — every patient-facing journey.
4. [`metrics-catalog.yaml`](metrics-catalog.yaml) — every metric and alert, defined once.

## Status

Phase order is `BUILD-SPEC.md` Appendix D. **Phase 0 is complete; Phase 1 (deterministic core) is in progress.**
A phase is not started until the previous phase's gates pass.

**Phase 0 (foundations): done.** Checksummed migration runner, 11 schemas, forced RLS keyed on `auth.uid()`,
restricted `careflow_app` role, ES256 JWT verification via JWKS, per-request `set_config('request.jwt.claims')`,
role-matrix test harness, data-flow register. CI (`.github/workflows/ci.yml`) runs lint (`careflow`, `tests`,
`scripts`), in-runner secret scanning (gitleaks), the OpenAPI snapshot check, the migrations and the full pytest
suite against the CI Supabase project as `careflow_app`, with zero skips allowed.

**Phase 1 (deterministic core): library code built, HTTP surface partial.**
- Built: scheduling (holds, bookings, cancel, clinical-priority waitlist), MPI ingestion with AES-256-GCM and HMAC
  tokens, key rotation (runbook in `docs/runbooks/key_rotation.md`), two-person merge and unmerge, the red-flag rule
  engine (mechanism only), the Postgres queue and outbox.
- Routes that exist: `GET /v1/iam/me` and `/v1/staff/scheduling/*`. Every route has role-matrix coverage.
- Not built yet: routes for patient ingestion, merge/unmerge and red-flag screening; the `msg` tables and the
  `iam` consent, activation and grievance tables; the minimal patient portal (no frontend exists).
- Queue and outbox stay in-process: no HTTP routes for claim, acknowledge or dead-letter, and no `/internal` auth
  until the Phase 2 worker proposal (short-lived signed token per job, §18.4) is decided.

**Tracked blockers and open items**
- **Red-flag gold-set regression test: blocked on clinicians.** `kb.red_flag_rules` holds only an inert
  `draft_unreviewed` stub. No rule or phrase may be written by an engineer (`CLAUDE.md` rule 1). The test cannot be
  written, and Phase 1 cannot be called complete, until a clinical lead supplies and approves rules.
- **Patient identity match rule (`STRONG_IDENTIFIERS`, `careflow/mpi.py`): pending sign-off** by Priya, Health-information
  lead. It stays a single constant until then.
- **No emergency auto-reply wording exists.** The immediate nurse task is raised on a red flag; the patient-facing
  send is pending a clinician-approved template.
- **Phase 2 acceptance item: the §7.3 ceilings** (12 model calls, 25 tool calls, 40 steps, 40 s per tick) have no
  runner yet and are not faked. Phase 2 is not done until a runner enforces and tests them.
- `kb/cards/` is empty: protocol cards are clinician-authored and none have been supplied.
- **Deferred to the portal phase (decided 2026-10-23, found in the msg/iam design review).** None of these tables or
  patient-side policies exist yet; each item below is a problem to solve when they are built:
  - Accounts with no `mpi.patients` row cannot have a message thread (threads are keyed by patient). Needs a portal-phase
    design (BUILD-SPEC 17.5 says `safety` is visible to every registered patient).
  - `iam.account_stage()` must derive from `auth.uid()` only (never an arbitrary account id), must be clinic-scoped (an
    approval in clinic A must not unlock care in clinic B), must be none unless `iam.accounts.status = 'active'`, and needs a
    revocation path.
  - `iam.consent_records` must order by a bigint identity, not a client-settable timestamp; needs a `recorded_by` for staff or
    guardian entries; guardian and dependent consent needs counsel (BUILD-SPEC 20.1); nothing yet enforces consent before AI
    processing or external calls.
  - `iam.consent_notices` (every published notice version kept, 9.4) needs a two-person publish and a clinic-scoped
    definition of "current"; the notice wording is legal content and is never written by an engineer.
  - `iam.grievances` cannot take an unauthenticated insert through `careflow_app` under RLS: use a narrow SECURITY DEFINER
    insert with rate limiting; the description is treated as patient data; no response deadline is invented.
  - `iam.activation_requests`: the approver must be forced to `auth.uid()` and be clinical staff; a decision must not be
    flippable; who triggers `pending_activation` "after first visit" is undecided (no attendance data exists).
  - Patient-side `msg` policies must check that the thread's patient has `account_id = auth.uid()`, gate the topic on both
    threads and messages, and add a deferred constraint requiring a screening row for every patient-authored message.
  - `msg.reads` is skipped until a route needs it.

Claude Code tooling (skills, reviewer agents, hooks) is documented in [`.claude/README.md`](.claude/README.md).

## Setup (development)

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/).
- **No Docker anywhere** (`CLAUDE.md` rule 12). Development and CI each use their own hosted
  Supabase project (Tokyo, `ap-northeast-1`; synthetic data only — production must be an India region,
  see `BUILD-SPEC.md` §16.5).

```
uv sync                       # install from pyproject.toml / uv.lock
cp .env.example .env          # then fill in your dev project's values; .env is gitignored
```

`requirements.txt` (runtime) and `requirements-dev.txt` (runtime + dev) are exported from
`uv.lock` for tools that need them: `uv export --no-hashes --no-emit-project -o requirements-dev.txt`.

## Safety note

This system does not diagnose, recommend treatment, or answer clinical questions. Every clinical
threshold, rule and template is clinician-authored; nothing in this repository is clinical advice.
