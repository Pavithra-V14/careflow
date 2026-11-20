# From today to the first real patient

**Where we are: NO-GO.** Nothing here is approved for real patients. The software works end to end on synthetic data in the demo profile (`docs/demo-evidence.md` says exactly what was shown), and every open item below belongs to a named person. This page does not decide clinical scope: the clinical lead decides what the pilot covers, and nothing on this page is a clinical instruction.

**How to use it.** Work through the groups in the order of section 1. Each row gives the item, its row in `docs/design/pre-launch-checklist.md` (the full text and status live there), what it is **blocked by**, and what it **unblocks**. Rows marked "start now" have long waiting times. The detailed external-service choices are in `docs/external-services-decisions.md`; what is entered as a labelled demo placeholder, and what stays blocked, is in `docs/demo-placeholders.md`. The owner's single-file answer packs are in `docs/approval-pack/export/` (five `.docx` files; nothing in them is approved until the named person signs).

## 1. The order, in one view

1. **Start now, in parallel (long lead times):** the hospital's SMS registration (owner and hospital); the choice of host and region (clinic owner and user); counsel starts the consent, retention, erasure and breach wording; the clinician starts the red-flag rules and protocol cards; a Tamil reader is engaged.
2. **Decide and approve the clinical content:** nothing real can be switched on until the clinician's rules, cards and thresholds exist and are signed.
3. **Supply the clinic's own facts:** the clinic owner enters real settings and the public profile; counsel supplies the legal wording.
4. **Run the technical proofs on the real host:** drills, second-factor live passes, a clean production database, the scorecard.
5. **Then and only then:** a limited pilot in shadow mode (section 4).

## 2. Everything, grouped by owner

### Clinician (the clinical lead, Priya)

All clinical content is the clinician's. Engineering never writes a threshold, rule or protocol wording (`CLAUDE.md` rule 1). The sheet is `docs/design/clinician-sheet.md`; the file to return is `docs/approval-pack/export/priya-clinician.docx`.

| # | Item | Row | Blocked by | Unblocks |
|---|---|---|---|---|
| C1 | Red-flag rules, with two named approvers, and the 100% recall gate | 19, 21 | The clinician's signed rules | Any real screening; the rule that nothing passes as clear without them |
| C2 | Protocol cards, the retrieval gold set and its thresholds, the priority levels | 21, 48, 81 | Signed answers; reviewed held-out cases | Real card matching and search (the six demo cards are invented) |
| C3 | Strong identifiers sign-off and how an identity refusal is routed | 3 | Signed sheet entries | Real identity matching |
| C4 | Offer wording, acceptance threshold, expiry and counts, scheduling-task urgency | 22 | Signed answers | Real scheduling offers |
| C5 | Revoke reasons, who re-checks and how urgently; closing a red-flag review task; what a reviewer may edit | 23, 24 | Signed answers | A full review console |
| C6 | Visit-readiness and intake content, care-plan and readiness task priority, dead-letter task priority | 6, 51 | Clinician content | Patient visit preparation (off until content exists) |
| C7 | The clinical scope of the pilot, and which capabilities stay off | 57 | Items above | The pilot start |

### Counsel

Legal text and legal periods. The file to return is `docs/approval-pack/export/counsel.docx`. Drafts to start from are in `docs/drafts/` and are not approved.

| # | Item | Row | Blocked by | Unblocks |
|---|---|---|---|---|
| L1 | Consent notice texts (versioned, permanent) and the withdrawal, download-my-data, correction, nominee and grievance flows | 60 | Counsel's wording | Anything a patient can consent to; sign-up |
| L2 | What erasure keeps; retention periods for results, plan text, messages, voice transcripts, and the clinical record period | 61, 62, 73 | Counsel's decision, privacy owner | The scheduled deletion job and the retention settings |
| L3 | Breach runbook contacts and approved wording | 56 | Counsel and the owner | The breach runbook being walkable |
| L4 | Grievance category wording and handling; the wording behind the patient-facing settings | 64, 86 | Counsel and the privacy owner | A real complaint form |
| L5 | Family-link rules (age and consent), and the whole-set decisions left to the owner | 89 and `demo-placeholders.md` | Counsel's decision | Family links for real (every request is refused until rules exist) |
| L6 | **The privacy promise on the public home page** (the page shows "not available yet" until it exists) | 98 | Counsel's wording; recorded outside the system | The signed-out page's privacy section |
| L7 | A data-processing agreement for any external service that would touch patient data | 57, 94 | The decision to use one | Any hosted model, SMS, voice or similar route |

### Privacy owner

The file to return is `docs/approval-pack/export/privacy-owner.docx`.

| # | Item | Row | Blocked by | Unblocks |
|---|---|---|---|---|
| P1 | Written confirmation of the patient-view field list and of each statement in the data-flow register | 17 and `data-flow-register.yaml` | The privacy owner's reading | Patient routes going on |
| P2 | Document storage region and its written confirmation | 18 | The host decision (E3); scanner | Real uploads |
| P3 | What happens to a patient's profile fields (sex word and similar) | 84 | A written decision | The patient profile for real use |
| P4 | Whether the new patient routes may go on | 84 to 89 | The above | Booking, messaging and the rest, for real |

### Clinic owner

The file to return is `docs/approval-pack/export/clinic-owner.docx`. The owner must **replace** every demo placeholder with a real, owner-approved value; the application refuses to start outside the demo while a labelled placeholder row exists (row 95).

| # | Item | Row | Blocked by | Unblocks |
|---|---|---|---|---|
| O1 | Publish the clinic; supply the public profile (about, departments and the doctors' display names the hospital chooses to publish, address, opening hours, phone) | 72, 98 | Real details | The signed-out home page |
| O2 | Time zone; break-glass reasons, window and re-opens; grievance categories and who watches the list; the real spend, canary and hourly caps | 59, 63, 64, 80, 85 | The owner's decisions (proposals are in the pack) | Time-aware screens; emergency record access; the complaint form; paid calls |
| O3 | Which visit types a patient may book and how many future bookings each; who creates slots, providers and locations | 45, 88 | The owner's decision | Patient booking |
| O4 | At least three doctor accounts per clinic (rule authoring needs an author and two other approvers); admin and doctor roles held by different people; the autonomy sign-off left unset | 58, 58a, 83 | Real people | Rule authoring; keeping the autonomous mode out of reach |
| O5 | Break-glass reviewer and review deadline | 63 | The owner's answer | The after-the-fact review |
| O6 | Rotate every key that was in `.env.cli`; a production start procedure that never uses the demo scripts | 12, 82, 97 | The owner | A clean credential history |
| O7 | Remove every demo placeholder; use a clean production database, never a copy of the demo one | 95 | The above | Startup in the clinical profile |
| O8 | Sign the sheet of `clinical`-profile capability gaps (each row closed or accepted off) | 57 | The decisions in `docs/external-services-decisions.md` | A defined pilot capability list |
| O9 | Choose and assign the on-call person for the first 72 hours of each new stage | spec 16.4 | The owner | A safe stage change |

### Tamil reader

The file to return is `docs/approval-pack/export/tamil-reader.docx`.

| # | Item | Row | Blocked by | Unblocks |
|---|---|---|---|---|
| T1 | Review every Tamil string (46 are flagged for attention); nothing is marked reviewed until the reader says so | 43 | The reader's time | Showing Tamil to a patient |
| T2 | Review the Sarvam draft and the pre-translated safety wording before any Tamil is ever sent | 43, 52 | The clinician's approved wording first | Tamil messages and any voice use |

### The user and the technical side

| # | Item | Row | Blocked by | Unblocks |
|---|---|---|---|---|
| E1 | Confirm CI is green on the latest commit: run 44737235379 finished green (2026-11-20); the live tests of migrations 0099 and 0100 and the frontend's schema regeneration are still to be confirmed | `demo-evidence.md` section 3 | CI | Trust in the live tests |
| E2 | Second-factor live passes: nurse and doctor authenticators; walk the screens that need a second factor; deliberately trigger the step-up prompt; a real auditor account | 76, 77, 78 | Enrolling authenticators | Evidence that the second factor works end to end |
| E3 | Production hosting in an India region, in the same region as the database; secret manager; re-measure latency | 27, 75 | Host and region decision | The drills and a real deployment |
| E4 | Run the rollback and restore drills on real hosting and record their times | 28, 54 | E3 | Evidence for the go/no-go |
| E5 | Run the real-PDF spike on dummy documents; confirm no pre-0023 patient row is worker-created | 9, 11 | Dummy samples; a read-only query | Real referral reading |
| E6 | The gold run and every threshold gate wired with real evidence; the production-readiness rubric and its evidence file | 25, 49, 81 | The clinician's reviewed cases | A scorecard of at least 70 of 100 with evidence |
| E7 | Decide the owner-delete rule for append-only ledgers before the restore drill | 40 | The owner's decision | A meaningful restore drill |
| E8 | Send the five answer packs to their people and track what comes back | 91 | The owner | Everything above that waits on an answer |

### External services (each needs the user's written yes; see `docs/external-services-decisions.md`)

| # | Item | Row | Blocked by | Unblocks |
|---|---|---|---|---|
| X1 | SMS provider, with the hospital's DLT registration (**start the registration now**) | 29, 55, 96 | Hospital registration; provider approval | Real patient phone sign-in and nudges |
| X2 | Hosting and region | 27, 28, 75 | The owner | E3, E4, P2 |
| X3 | Malware scanner (the direction is ClamAV on an operated machine) | 2, 18 | Where the worker machine lives | Real uploads |
| X4 | A model route approved for patient data, per capability, or each accepted off | 57, 94 | An agreement and the privacy owner | Reading referrals by model, Tamil drafts, search index in the clinical profile |
| X5 | E-mail, voice engine, hosted observability | 26, 34, 52 | Decisions | Optional extras; none is needed for a first pilot |

## 3. Gates that are already code

These are checked by the software, so they cannot be skipped by forgetting: the application refuses to start outside the demo while a placeholder row, a demo switch or the e-mail sign-in is present; the second factor is required at two layers; the red-flag rules run before any model; a missing or unverified fact is never treated as true or false; every write carries an idempotency key; row-level security isolates clinics. The go/no-go (`careflow/predeploy.py`) says NO-GO until the evidence file is filled.

## 4. What the first week of a limited pilot should look like (in the product's own terms)

The clinical lead decides the scope, the people and the dates. This is the shape the specification already describes (`BUILD-SPEC.md` 16.4 rollout ladder; the built default mode is `shadow`, a deliberate decision recorded in `docs/spec-sweep.md`).

- **Mode: shadow.** The system proposes; **a human decides every action**. Nothing is approved automatically. The spec recommends at least one week in this mode before any automated action, and the autonomous mode stays out of reach (the sign-off setting is unset and the roles are held by different people; rows 58, 58a).
- **Human review of everything.** Every referral proposal, every match and every offer goes through the review console to a named person with the right role. Nothing clinical is ever approved by timeout. A person must act on every overdue red-flag task (`docs/runbooks/overdue-red-flag.md`).
- **Only the deterministic path is on.** Red-flag rules, scheduling and the review queue. Hosted models stay off in the clinical profile (the capabilities degrade visibly to manual steps: front desk keys facts in, photographed referrals become manual tasks, no Tamil drafts, no search index) unless a route was approved in X4.
- **Switches that stay off** unless the clinical lead and the owner have approved their content and settings: `FEATURE_VOICE_CHANNEL` (voice), `FEATURE_CARE_JOURNEY`, `FEATURE_VISIT_READINESS`, `CAREFLOW_PATIENT_VISIT_PREP` (until real checklist content exists), `CAREFLOW_FAMILY_LINKS` (until counsel's rules exist), `CAREFLOW_KNOWLEDGE_EDITOR` and `CAREFLOW_KNOWLEDGE_SEARCH` (until real cards are approved), and the demo switches (`CAREFLOW_DEMO_*`, never). Patient booking and messaging go on only after rows 87 and 88 and the privacy owner say so.
- **Who is watching.** A named on-call person for the first 72 hours (spec 16.4), checking the in-app alerts, the age of the oldest queued job, any dead-lettered job and any overdue red-flag task each day. The runbooks to hand: `docs/runbooks/` (dead letters, wrong merge, model outage, breach, key rotation).
- **What to record.** Every human decision is a real label (rule 9: only human decisions train anything; automated approvals never do). The spec's calibration and review-error figures need this data; a clinician sets the real bar.
- **What stops the pilot.** Any missed urgent case, a rubber-stamping pattern in the review statistics, an unreviewed overdue red flag, or a failed check on the readiness page. Stopping is a switch back to the earlier stage, which the spec requires to exist.
- **Not in week one.** Moving to `assisted`, turning anything in the list above on, adding a service. Each is a change-control record with a named signer.

## 5. What this page does not say

It does not say the product is safe, approved or ready. It does not set clinical scope. It names no vendor as chosen and no price. Every "unblocks" is a precondition, not a promise: a real launch needs all the preconditions together, and the answer today is NO-GO.
