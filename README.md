# CareFlow

An agentic patient-access and care-coordination platform for a single outpatient clinic.

This repository is built to a written specification. **Read these first, in order:**

1. [`CLAUDE.md`](CLAUDE.md) — the non-negotiable rules.
2. [`BUILD-SPEC.md`](BUILD-SPEC.md) — the full specification. Build order is Appendix D.
3. [`PATIENT-WORKFLOWS.md`](PATIENT-WORKFLOWS.md) — every patient-facing journey.
4. [`metrics-catalog.yaml`](metrics-catalog.yaml) — every metric and alert, defined once.

## Status

Phase 0 (foundations) — in progress. See `BUILD-SPEC.md` Appendix D.

## Setup (development)

- Python 3.12 and [`uv`](https://docs.astral.sh/uv/).
- **No Docker anywhere** (`CLAUDE.md` rule 12). Development and CI each use their own hosted
  Supabase project (Mumbai region) with synthetic data only.

```
uv sync                       # install from pyproject.toml / uv.lock
cp .env.example .env          # then fill in your dev project's values; .env is gitignored
```

`requirements.txt` (runtime) and `requirements-dev.txt` (runtime + dev) are exported from
`uv.lock` for tools that need them: `uv export --no-hashes --no-emit-project -o requirements-dev.txt`.

## Safety note

This system does not diagnose, recommend treatment, or answer clinical questions. Every clinical
threshold, rule and template is clinician-authored; nothing in this repository is clinical advice.
