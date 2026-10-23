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
- Routes that exist: `GET /v1/iam/me`, `/v1/staff/scheduling/*`, `POST /v1/staff/messages/{id}/screen`,
  `POST /v1/staff/patients` (registration / identity resolution) and the merge routes `POST /v1/staff/merges`,
  `/v1/staff/merges/{id}/approve` and `/v1/staff/merges/{id}/unmerge`. Every route has role-matrix coverage (the screening
  route's is in a documented separate harness, `docs/design/role-matrix-exceptions.md`).
- Built since: migrations 0011 to 0013 (encrypted message bodies, patient account binding, screenings).
- **Merge routes: step-up implemented, claim shape pending live verification.** They need a recently verified second factor
  (`CAREFLOW_STEP_UP_MAX_AGE_SECONDS`, required; 503 while unset). The token claim shape it reads has not been checked against
  a real token: see [`docs/design/step-up-spike.md`](docs/design/step-up-spike.md) for the spike the owner must run.
- **`STRONG_IDENTIFIERS` is reachable over HTTP** through `POST /v1/staff/patients`, and it is still **pending sign-off by Priya**
  (Health-information lead). **Do not use real patient data until that sign-off is recorded.**
- Not built yet: the staff message read route; the `iam` consent, activation and grievance tables; the minimal patient portal
  (no frontend exists).
- Queue and outbox stay in-process: no HTTP routes for claim, acknowledge or dead-letter, and no `/internal` auth
  until the Phase 2 worker proposal (short-lived signed token per job, §18.4) is decided.

**Tracked blockers and open items**
- **Red-flag gold-set regression test: blocked on clinicians.** `kb.red_flag_rules` holds only an inert
  `draft_unreviewed` stub. No rule or phrase may be written by an engineer (`CLAUDE.md` rule 1). The test cannot be
  written, and Phase 1 cannot be called complete, until a clinical lead supplies and approves rules.
- **Patient identity match rule (`STRONG_IDENTIFIERS`, `careflow/mpi.py`): pending sign-off** by Priya, Health-information
  lead. It stays a single constant until then. It is already reachable over HTTP (`POST /v1/staff/patients`), so no real
  patient data may be used until the sign-off is recorded.
- **No emergency auto-reply wording exists.** The immediate nurse task is raised on a red flag; the patient-facing
  send is pending a clinician-approved template.
- **Phase 2 acceptance item: the §7.3 ceilings** (12 model calls, 25 tool calls, 40 steps, 40 s per tick) have no
  runner yet and are not faked. Phase 2 is not done until a runner enforces and tests them.
- `kb/cards/` is empty: protocol cards are clinician-authored and none have been supplied.
- **Deferred items and open findings** are listed one per line, with what each waits for, in
  [`docs/design/deferred-items.md`](docs/design/deferred-items.md). The design for the `msg` tables, screenings and the
  `account_id` protection is in [`docs/design/msg-screening-binding-design.md`](docs/design/msg-screening-binding-design.md).

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
