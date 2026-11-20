# CareFlow handover: the one index

For the owner or a new engineer. This page is an **index**: it points at the documents that hold the detail and gives the commands you will actually type. **Nothing here is approved for real patients**; what must be true first is in `docs/design/pre-launch-checklist.md`. Where this page and the code disagree, the code wins and this page is wrong: say so.

## 1. What the product is

- A backend for one outpatient clinic that reads referral letters, screens them for danger with deterministic rules a clinician authors, proposes a protocol match, schedules and re-prioritises appointments, and follows care plans.
- It never decides anything a wrong answer could hurt someone over: every clinical judgement is a clinician-written rule or a human decision, and the safety rules always run before any model.
- Clinics are isolated by the database (row-level security), not by application code; every write is idempotent and audited.
- Today it is a **synthetic demo**: no clinical content ships, hosted models are demo-only, and nothing has been through a drill or a launch gate (the honest answer of the scorecard is NO-GO).
- Specification: `BUILD-SPEC.md`; the 13 rules every change is held to: `CLAUDE.md`; the whole picture: `docs/architecture-overview.md`.

## 2. The documents

| Path | What it is for | Who reads it |
|---|---|---|
| `docs/handover.md` | This index | Everyone |
| `docs/architecture-overview.md` | Components, the letter-to-decision flow, where each of the 13 rules is enforced, roles, demo versus clinical | New engineer, auditor |
| `docs/README-project.md` | The earlier developer handover (map of the code) | New engineer |
| `docs/status-now.md` | What changed lately, newest last | Owner, engineer |
| `docs/spec-sweep.md` | Every requirement of the specification with its honest status and the recorded decisions | Owner, auditor |
| `docs/spec-compliance.md` | The earlier requirement-by-requirement review (the sweep supersedes it where they differ) | Auditor |
| `docs/gap-audit.md` | The short list of what is not real | Owner |
| `docs/design/pre-launch-checklist.md` | Everything that must be true before real patients, row by row, with the owner of each | Owner, everyone |
| `docs/design/threat-model.md` | What was reviewed, what was fixed, what was accepted and why | Auditor, engineer |
| `docs/design/grants-ledger.md` | Every database privilege each migration added | Auditor |
| `docs/design/` (the rest) | One design note per phase and per decision (`phase*-*.md`, `retrieval-pgvector.md`, `hosted-providers.md`, `malware-scanner-options.md`, `real-model-demo.md`) | Engineer |
| `docs/config-reference.md` | Every setting and secret NAME the code reads: meaning, default, profile, what refuses. Tested for completeness | Engineer, operator |
| `docs/demo-runbook.md` | The demo, step by step, with what you should see | Owner, demonstrator |
| `docs/demo-placeholders.md` | What is entered as a labelled placeholder, what stays blocked, and by whom | Owner |
| `docs/mfa-live-pass.md` | The second-factor live pass: enrolment, expected 200s and 403s | Owner, tester |
| `docs/ops-runbook.md` | Backup and restore, key rotation, provider outages, the audit log, the first-patient checks | Operator |
| `docs/runbooks/` | Incident runbooks (breach, overdue red flag, dead letters, model outage, wrong merge, key rotation, restore and rollback drills, SMS registration, staff accounts); drafts, none rehearsed | Operator, on-call |
| `docs/first-run-guide.md` | First setup of the run folder and the dev project | Owner |
| `docs/user-guide-staff.md`, `docs/user-guide-patient.md` | What each screen does, as built | Staff, patients, trainers |
| `docs/frontend-backend-requests.md` | What the frontend asked of the backend and what it must build for each backend change (numbered items) | Frontend, backend |
| `docs/frontend-status.md`, `docs/frontend-live-checklist.md`, `docs/frontend-live-findings.md`, `docs/frontend-security-review.md`, `docs/frontend-spec-gap.md` | The frontend's own status, live passes, findings and review | Frontend, owner |
| `docs/approval-pack/` | The proposals and questions each approver answers (section 5) | The five approvers |
| `docs/drafts/` | AI drafts of legal and policy text for counsel and the privacy owner; none is approved | Counsel, privacy owner |
| `metrics-catalog.yaml`, `data-flow-register.yaml` | Every metric defined once; every external call with its data class | Engineer, auditor |

## 3. Commands

All PowerShell, from the run folder. The clinic id below is the dev clinic; replace it if yours differs. Every script that writes asks you to TYPE the project host and refuses outside dev + demo + synthetic. Nothing prints a key.

**Update and migrate** (window A):
```powershell
cd E:\Claude\careflow-run
git fetch origin
git checkout --detach origin/main
.\.venv\Scripts\python.exe -m careflow.migrate --check
.\.venv\Scripts\python.exe -m careflow.migrate
```

**Start the demo backend** (window B; port 8000; refuses unless the synthetic dev demo):
```powershell
cd E:\Claude\careflow-run ; .\scripts\run_demo_backend.ps1
```

**Smoke check** (read-only; PASS or FAIL lines and counts):
```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\demo_smoke.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
```

**Seed scripts** (each has `--dry-run`; the placeholder ones also `--remove`; details in `docs/demo-runbook.md` sections 3, 4, 13 to 15):
```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\seed_dev_staff.py --email nurse@example.test --role nurse --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_dev_demo_data.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_consent_notices.py
.\.venv\Scripts\python.exe scripts\seed_break_glass_reasons.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_demo_placeholders.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_demo_family_rules.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\seed_dev_portal_patient.py --account-id <THE-USER-UID> --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\reset_dev_portal_account.py --account-id <THE-USER-UID> --dry-run
.\.venv\Scripts\python.exe scripts\index_demo_cards.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
.\.venv\Scripts\python.exe scripts\dev_clinic.py publish --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2
```

**The real-model demo** (hosted providers, synthetic letters only; the keys are set once with `scripts\set_provider_keys.ps1`, which never shows a key; see `docs/design/real-model-demo.md`):
```powershell
cd E:\Claude\careflow-run
.\.venv\Scripts\python.exe scripts\demo_real_models.py --clinic-id b230e20e-655f-410d-8398-d2eadcb44dd2 --fresh
```

**Tests** (engineer; offline needs no database): `uv run pytest -q`. The live tests run in CI against the CI project.

## 4. Switches you may turn on in `.env`

Each is exactly `on` and needs a backend restart. Meanings, defaults and what refuses: `docs/config-reference.md`. Which screen each shows: `docs/demo-runbook.md` section 14.

## 5. The five approval files

Generated by `python scripts/export_approval_pack.py <name>`, kept in `docs/approval-pack/export/`. Each person ticks Approve or writes a change, signs the block at the end and returns the file. **Nothing in them is approved until the named person signs.** Until then the software keeps its safe default.

| File | Who | What they decide |
|---|---|---|
| `docs/approval-pack/export/priya-clinician.docx` | The clinician (Priya) | The clinician items of the sheet: red-flag rules, protocol cards and lifecycle, intake and readiness questions, thresholds, the retrieval gold set, and the pre-visit brief question (item 40) |
| `docs/approval-pack/export/counsel.docx` | Counsel | The wording of the consent notices, grievance and rights procedure, retention schedule, breach notice and patient-facing text; the family-link rules; the questions in the cover note |
| `docs/approval-pack/export/privacy-owner.docx` | The privacy owner | Whether each pre-filled statement about where data goes and how it is kept is correct, and the questions marked QUESTION |
| `docs/approval-pack/export/clinic-owner.docx` | The clinic owner | The proposed values: break-glass reasons and limits, grievance categories, retention periods, the time zone |
| `docs/approval-pack/export/tamil-reader.docx` | The Tamil reader | Each Tamil string, one by one; 46 are flagged for attention; nothing becomes reviewed until they say so |

## 6. Demo-only versus needs a decision before real patients

- **Demo only (refused or off in the clinical profile):** hosted model providers, the demo packs and placeholder rows, the e-mail sign-in, the vector rebuild, the password-only second-factor setting, real uploads. The application refuses to start when one is present outside the synthetic demo. Table: `docs/architecture-overview.md` section 6; inventory: `docs/demo-placeholders.md`.
- **Needs a decision or content before real patients:** the full list with owners is `docs/design/pre-launch-checklist.md` (rows 21 to 25 clinical content, 60 to 62 and 84 to 91 counsel and the privacy owner, 27 and 28 hosting and drills, 29 and 55 SMS, 2 and 18 uploads and the scanner, 76 to 78 second factor, 92 to 96 this month's decisions). Every requirement and its status: `docs/spec-sweep.md`. The answer today is NO-GO.

## 7. Open external-service decisions

Each is a new service or dependency, so by `CLAUDE.md` rule 13 none is added without the owner's yes. One line each:

- **SMS provider and an approved sender (India DLT registration).** Unlocks real patient sign-in by phone code and appointment nudges; needs your yes because it is a paid third party that carries patient phone numbers (`docs/runbooks/sms-dlt-registration.md`, checklist rows 29, 55, 96).
- **E-mail sender.** Unlocks e-mail nudges and the second delivery channel for alerts; needs your yes because it is a third party that sends on the clinic's behalf (spec 18.3; only a port and a fake exist).
- **Voice engine (Gemini Live).** Unlocks the in-app spoken channel; needs your yes because patient speech would leave the clinic for Google, which also needs a data agreement before real patients (`docs/design/phase8-voice.md`).
- **Production hosting in an India region, a secret manager, backups with point-in-time recovery.** Unlocks a real deployment and the drills; needs your yes because it is the largest commitment and the region cannot change later (`docs/ops-runbook.md`, checklist rows 27, 28, 54, 75).
- **Hosted observability (Langfuse, Grafana) and alert delivery (chat, e-mail).** Unlocks traces and dashboards beyond the in-app watchdog; needs your yes because they are third parties that must receive metadata only (spec 13; today the in-app alerts are real and the rest are fakes).
- **A malware scanner for uploads (self-operated ClamAV).** Unlocks real document uploads; needs your yes on where the worker machine lives and a new component to install and keep updated (`docs/design/malware-scanner-options.md`, checklist rows 2 and 18).

Also decided by you rather than built: an approved LLM and vision route for the clinical profile (`docs/design/hosted-providers.md`, `docs/design/phase10-pre-launch.md`), and a local decision model.
