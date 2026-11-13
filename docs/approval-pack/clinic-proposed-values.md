# Clinic values: proposed, for the clinic owner to approve or change

> **AI-prepared, not approved by the clinic owner.** Prepared 2026-11-13. Not clinical or legal advice. Never loaded by the application; never marked approved. These are PROPOSALS with a reason each. The application reads none of this file. The demo pack (`config/demo-phase4.yaml`) holds only labelled placeholders for these settings (for example `demo_reason`, `demo_category`) and none of the proposals below was added to it or to any `.env`. When the owner approves a value, the owner sets it in the clinic's own settings; until then the software refuses the feature or stays off (unknown is not a value).

For each row: tick **Approve** or **Change to** and sign at the end. Anything not known from the code is written as a question.

## 1. Break-glass (emergency access to a patient outside your normal reach)

What it is: a person with a second factor opens a time-limited, audited grant for one patient, picking a reason from the clinic's closed list; it is reviewed afterwards. With no settings row for the clinic, no grant can be opened. The limits live in the database (`iam.break_glass_settings`) and are set by the owner-run script `scripts/seed_break_glass_reasons.py`; they are not runtime values the app can raise.

| Setting | Proposed value | Reason | Approve | Change to |
|---|---|---|---|---|
| Reasons (closed list, no free text) | `emergency_care`, `covering_colleague`, `urgent_question`, `other` | Four plain words cover the cases a clinic usually meets; a short closed list keeps patient details out of the reason. QUESTION: do these match how the clinic would describe its own cases, and is `other` acceptable or must every use fit a named reason? | [ ] | [ ] |
| Window (how long one grant lasts) | 60 minutes | One consultation-length window limits how long access stays open. The demo placeholder is also 60, which is not a recommendation. The database allows 1 to 240. QUESTION: how long does a real emergency review take at this clinic? | [ ] | [ ] |
| Re-opens (how many grants one person may open for the same patient in 24 hours) | 3 | Lets a person return a few times in a day while a pattern of repeated re-opening is stopped and shown. The database allows 1 to 50. QUESTION: what would a legitimate busy day look like? | [ ] | [ ] |
| Who reviews a grant afterwards, and within how long | (not proposed) | The review step exists, but who does it and the deadline are the clinic's. QUESTION. | [ ] | [ ] |

## 2. Grievance categories (the words a patient chooses when filing)

| Setting | Proposed value | Reason | Approve | Change to |
|---|---|---|---|---|
| Categories | `access_to_my_data`, `correction_of_my_data`, `erasure_of_my_data`, `nominee`, `complaint_about_care`, `complaint_about_staff`, `complaint_about_the_system`, `other` | Taken from the draft procedure (`docs/drafts/grievance-and-rights-procedure-draft.md`); they match the patient rights the system already supports. The system accepts only the words the clinic sets. QUESTIONS: does counsel want the rights requests (access, correction, erasure, nominee) handled as grievances or through a separate route? Who may see "complaint about staff"? | [ ] | [ ] |

## 3. Voice transcript retention

| Setting | Proposed value | Reason | Approve | Change to |
|---|---|---|---|---|
| Days a transcript stays readable (`CAREFLOW_VOICE_TRANSCRIPT_RETENTION_DAYS`, required) | 30 days | The project specification gives 30 days as a starting value for text that holds patient words ("highest risk, lowest long-term value"). Counsel confirms it. Voice has no real engine yet. QUESTION: is a shorter window preferred, or does any rule require longer? Also note the purge is run by an admin; there is no scheduled purge. | [ ] | [ ] |

## 4. Other retention periods

Source for every "proposed" cell: `BUILD-SPEC.md` section 9.4, which calls them "starting values, not fixed law". Nothing here has been checked against any state or professional-council rule. The clinical-record rows need the clinical lead's sign-off (`# CLINICAL-PLACEHOLDER: needs clinician sign-off`).

| Record | Proposed | Reason | Approve | Change to |
|---|---|---|---|---|
| Clinical record, adult | 8 years after the last encounter | Spec starting value. QUESTIONS: which rule applies in the clinic's state; what counts as an encounter (clinician sheet item 34). | [ ] | [ ] |
| Clinical record, minor | until age 21, at least 8 years | Spec starting value; limitation periods commonly run from majority. QUESTION for counsel. | [ ] | [ ] |
| Pregnancy / childbirth record | 21 years or more | Spec starting value; claims can surface late. QUESTION for counsel and the clinical lead. | [ ] | [ ] |
| Consent records and notices | permanent | A consent means nothing without the exact text shown. Never deleted today. | [ ] | [ ] |
| Audit log | permanent (or at least 8 years) | Needed to reconstruct who did what; append-only. QUESTION: is permanent acceptable? | [ ] | [ ] |
| Agent checkpoints and run traces (metadata) | 90 days | Incident window. Today these are not pruned: an accepted residual (checklist row 74). The setting `CAREFLOW_RETENTION_CHECKPOINT_DAYS` only feeds a report. | [ ] | [ ] |
| Prompt and response bodies holding patient text | 30 days | Highest risk, lowest long-term value. No real model is wired today. | [ ] | [ ] |
| Clinical-record report period (`CAREFLOW_RETENTION_CLINICAL_RECORD_DAYS`) | (counsel to state in days) | The report counts records past this period and deletes nothing. | [ ] | [ ] |

## 5. Clinic time zone

| Setting | Proposed value | Reason | Approve | Change to |
|---|---|---|---|---|
| `CAREFLOW_CLINIC_TIME_ZONE` | `Asia/Kolkata` | The specification expects an India production deployment. This is the standard IANA name for Indian time (the demo placeholder uses the older alias `Asia/Calcutta`, which names the same zone). QUESTION: is the clinic in one time zone, and where? | [ ] | [ ] |

## Signature (left blank)

| Sections approved | Name | Role | Date | Signature |
|---|---|---|---|---|
| | | | | |
