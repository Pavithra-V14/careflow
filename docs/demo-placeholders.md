# Demo placeholders: what is entered, what stays blocked, and by whom

**Nothing here is approved.** Every item below is a stand-in for a value that belongs to a named owner. Placeholders exist only so a screen or a flow can be walked in the DEV demo (profile `demo`, `CAREFLOW_DATA_POLICY=synthetic_only`) with synthetic data. They carry the label **"DEMO PLACEHOLDER, not approved by the clinic owner"** (or "DEMO ONLY - NOT A CLINICAL DECISION" for the settings in the demo pack, or "DEMO ONLY" at the start of a consent notice), in a field the screen shows or in the row's `provenance` note. No claim of counsel, privacy-owner, clinic or clinician approval is made anywhere, and no Tamil string is marked reviewed (43 stay flagged for the Tamil reader).

**Safety net.** The application REFUSES TO START outside profile=demo with synthetic data while any labelled row exists (`careflow/placeholder_check.py`, `ops.demo_placeholder_rows()` from migration 0096), and also when that cannot be checked. Take rows out with each script's `--remove`. A deployment can therefore never silently carry a placeholder into `clinical`.

**How placeholders get in.** Four routes, all demo-only, all owner-run, none in a migration or a repository default: (1) the demo pack under `CAREFLOW_DEMO_PHASE4=1` (settings in the environment, labelled); (2) `scripts/seed_demo_placeholders.py` (owner-managed database rows, this pass); (3) `scripts/seed_demo_family_rules.py` (family-link rules); (4) `scripts/seed_consent_notices.py` with the demo pack (consent notices; permanent by design, so a notice cannot be removed, and the refusal above protects the clinical profile). Exact commands: `docs/demo-runbook.md` section 14.

## Entered (yes): what a screen needs and no one has signed

| Item | Owner | State without the placeholder | Placeholder entered | Switch to see it |
|---|---|---|---|---|
| Break-glass reasons (`emergency_care`, `covering_colleague`, `urgent_question`, `other`) | Clinic owner | No reason list: no grant can be opened | Yes: `seed_demo_placeholders.py` (from `clinic-proposed-values.md` section 1) | none (always on) |
| Break-glass window 60 minutes and 3 re-opens | Clinic owner | No settings row: no grant can be opened | Yes: same script (the proposal's numbers) | none |
| Public grievance hourly cap | Privacy owner | No row: the public grievance route answers 503 | Yes: invented demo number 20, labelled so | `CAREFLOW_PUBLIC_GRIEVANCE_IP_PER_HOUR` (the demo pack supplies it) |
| Canary cap per day | Clinic owner / clinical lead | No row: no canary is injected | Yes: invented demo number 5 | `CAREFLOW_CANARY_RATE` (demo pack) |
| Per-call spend cap for session model calls | Owner | No row: session calls are refused | Yes: invented demo number 0.05 | none |
| Bookable visit types for patient booking, 2 future bookings each | Clinic owner | Nothing is bookable by a patient | Yes: every visit type of the dev clinic | `CAREFLOW_PATIENT_BOOKING=on` |
| Public home page: about, departments and one doctor name, address, opening hours, phone (migration 0100) | Hospital | All null: the page says "not available yet" | Yes: `seed_demo_placeholders.py`; every value starts with "DEMO", is plainly not a real address or number, and claims nothing (the test checks it); the response carries `placeholder: true` for a demo badge | none (always on) |
| Family-link rules (guardian 0 to 9, carer 30 to 130, consent required) | Counsel | Every request is refused | Yes: `seed_demo_family_rules.py` | `CAREFLOW_FAMILY_LINKS=on` |
| Consent notices and their patient-facing names | Counsel and privacy owner | Nothing can be consented to | Yes, DEMO ONLY wording: demo pack + `seed_consent_notices.py` | none |
| Review reason codes, task deadlines, watchdog limits, canary and sample rates, rubber-stamp thresholds, cost figures, sign-in link | Clinic owner and clinical lead | The review console, watchdog, canaries and cost canvas report "not configured" | Yes: demo pack, every value labelled | `CAREFLOW_DEMO_PHASE4=1` |
| Grievance categories, erase reasons, sex words, per-address limits, message rate limit | Privacy owner | Those routes answer 503 | Yes: demo pack (`demo_category`, `demo_reason`, `demo_word_a`) | `CAREFLOW_DEMO_PHASE4=1` |
| Voice transcript retention 30 days | Counsel | The purge does not run | Yes: demo pack (the proposal's 30) | `CAREFLOW_DEMO_PHASE4=1` |
| Clinic time zone | Clinic owner | The profile says null | Yes: demo pack (`Asia/Calcutta`) | `CAREFLOW_DEMO_PHASE4=1` |
| Visit checklist and intake placeholder questions; care-plan and readiness task priority | Clinician | Patient visit preparation says "Nothing to prepare" | Yes: demo pack, labelled "NOT A CLINICAL QUESTION" | `CAREFLOW_DEMO_PHASE4=1`, `CAREFLOW_PATIENT_VISIT_PREP=on` |
| Care-gap rule shape | Clinician | The care-gap routes answer 503 | Yes: demo pack (`demo_rule`, 30 and 5 days; invented) | `CAREFLOW_DEMO_PHASE4=1` |
| Retrieval thresholds and context budget | Clinician | Search abstains: `no_calibrated_policy` | Yes: demo pack, labelled | `CAREFLOW_DEMO_PHASE4=1` and `CAREFLOW_KNOWLEDGE_SEARCH=on` |
| The six invented demo cards | Clinician | Search has nothing to find | Yes (already exist; invented words) | `scripts/index_demo_cards.py` |
| Checkpoint retention 90 days (report only) | Owner | The retention report skips it | Optional: one `.env` line in `docs/demo-runbook.md` section 14, labelled in a comment | `CAREFLOW_RETENTION_CHECKPOINT_DAYS` |

## Not entered (no): what stays blocked, and why

| Item | Owner | Why it stays blocked | What it blocks |
|---|---|---|---|
| Red-flag rules, thresholds, emergency wording, the patient-facing safety template | Clinical lead (Priya) | Clinical content: CLAUDE.md rule 1. No placeholder is allowed | The safety screening passes nothing as clear (`unassessable`); the safety template shows `pending_clinician_template` |
| Real protocol cards, intake questions, readiness checklists, triage wording, priority levels, gold set, evaluation bars, dosing | Clinical lead | Clinical content | Real matching, the 14.3 gates and the scorecard (NO-GO) |
| Medication vocabulary (`CAREFLOW_MEDICATION_CODES`) | Clinician | A clinical vocabulary; there is no demo pack value | The medication routes answer 503 |
| Clinical-record retention period (`CAREFLOW_RETENTION_CLINICAL_RECORD_DAYS`) | Counsel and the clinical lead | The proposal leaves it for counsel to state in days; a legal period is not ours to guess | The retention report skips clinical records |
| Real consent wording, real grievance categories and wording, what erasure keeps, retention schedule | Counsel and privacy owner | Legal text and legal periods | A real launch; `docs/approval-pack` carries the proposals |
| Breach-notification templates and contacts | Owner and counsel | Wording and contacts are legal and personal; the runbook keeps blanks (checklist row 56). No screen reads them | The breach runbook cannot be walked end to end |
| The privacy promise on the public home page (`iam.clinic_public_profile.privacy_promise`) | Counsel | It is legal wording a patient reads as the hospital's promise: not ours to write, so the seed leaves it null | The home page shows its own "not available yet" line |
| Break-glass reviewer and review deadline | Clinic owner | The proposal leaves it as a question | The after-the-fact review has no named reviewer |
| Real family-link age limits and consent rule | Counsel | Counsel's decision (a placeholder exists for the demo only) | A real launch |
| Tamil strings reviewed | Tamil reader | Never marked reviewed by us; 43 flagged | The Tamil approval row |
| SMS (DLT), e-mail sender, malware scanner, hosted tracing, a local model, a real vision/embedding route in `clinical`, voice engine | Owner (new external services, CLAUDE.md rule 13) | Each needs approval before it is added | Real delivery, uploads, search in `clinical`, voice |
| Hosting region, backups, restore drill, on-call, key store | Owner | Decisions and drills, not data | The pre-deployment gate (`docs/ops-runbook.md`) |

## Rules this pass keeps

- Dev database, demo profile, `synthetic_only` only; each script refuses otherwise, asks you to type the project host, supports `--dry-run` and `--remove`, and prints counts only.
- Everything entered is labelled; the label is checked by tests (`tests/test_demo_placeholders.py`); the script writes nothing to a migration or a repository default.
- No clinical content, no new service, no new dependency.
