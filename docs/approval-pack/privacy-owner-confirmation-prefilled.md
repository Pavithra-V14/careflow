# Privacy owner: statements to confirm, pre-filled from the code

> **AI-prepared, not approved by the privacy owner.** Prepared 2026-11-13. Not legal advice. Never loaded by the application; never marked approved. Each statement below describes what the system does TODAY, taken from the code and the design notes named in the "Source" column, so the privacy owner can confirm it is true, or ask for a change. It extends `docs/design/privacy-owner-confirmation.md` (which stays as it is). A statement the assistant could not verify from the code is marked **QUESTION** and carries no tick for "true". Wording of notices and retention periods is counsel's, not this sheet's.

How to use: for each row tick **Confirmed** or **Change asked** (and write the change). Leave a row unticked if you cannot tell. Sign at the end. A signed row is what the pre-launch checklist (`docs/design/pre-launch-checklist.md`, rows 17, 18, 61, 62, 66) is waiting for.

## A. Personal data the system holds

| # | Statement | Source | Confirmed | Change asked |
|---|---|---|---|---|
| A1 | A patient's name, date of birth and phone number are stored only as ciphertext (AES-256-GCM, a fresh random value per encryption). The database never holds them in the clear. The keys come from the environment, not from the code or a file. | `careflow/crypto.py` | [ ] | [ ] |
| A2 | Looking a patient up works through keyed hashes (HMAC-SHA256) of the identity details, not through a readable index. A hash is kept even after erasure, and matching ignores an erased record. | `careflow/crypto.py`; `docs/design/patient-erasure-and-update-narrowing.md` | [ ] | [ ] |
| A3 | The e-mail address typed when an admin invites a staff member is sent to the sign-in service (Supabase Auth) to create the sign-in. It is not stored in our tables, not logged, not in an audit row and not returned by any route. While no Auth admin adapter is wired, the invite route answers 503 and sends nothing. | `docs/design/privacy-owner-confirmation.md` section 1 | [ ] | [ ] |
| A4 | Staff see a patient's name, birth year and last four digits of the phone number in list rows only when the profile is demo and the data is synthetic. Outside that, the list-row view is not produced. | `careflow/app.py` (patient view); `docs/design/patient-view-fields.md` | [ ] | [ ] |
| A5 | A grievance's text, a message body and document text are stored encrypted. Grievance text is visible only to the staff role that handles grievances (an admin of the clinic). **QUESTION:** which roles may read message bodies and documents is set by the role rules in the database; the privacy owner should confirm the list against `docs/design/patient-view-fields.md` and the role matrix. | `careflow/app.py` (grievances); `docs/design/` | [ ] | [ ] |

## B. Who can see what, and how it is recorded

| # | Statement | Source | Confirmed | Change asked |
|---|---|---|---|---|
| B1 | Each clinic's data is separated by database row-level security, not by application code alone. Staff of one clinic cannot read another clinic's rows. | `CLAUDE.md` rule 8; migrations | [ ] | [ ] |
| B2 | Every staff route requires a verified second factor. In production this must be on at the app AND the database; the app check may be switched off only in the demo profile with synthetic data (it was off for the live demo pass of 2026-11-13). | `careflow/app.py`; `docs/design/pre-launch-checklist.md` rows 76 and 78 | [ ] | [ ] |
| B3 | Reading or changing patient-related records writes an audit row recording who, what kind of action, which record and the time, using ids and fixed words only, never patient text. The audit log is append-only and is never deleted. | `careflow/audit.py`; `docs/design/retention-and-erasure.md` | [ ] | [ ] |
| B4 | Break-glass access to a patient outside a role's normal reach needs a second factor, a reason from the clinic's closed list, is limited in length and in how often one person may reopen it for the same patient in 24 hours, and is reviewed afterwards. The limits live in the database, not only in the app. | migrations 0058, 0062, 0066, 0070 | [ ] | [ ] |
| B5 | Voice transcripts are stored only as ciphertext with an expiry date set by the retention setting. Only an admin can read one, only inside the window, and every read is an audit row. | `docs/design/privacy-owner-confirmation.md` section 1 | [ ] | [ ] |

## C. Where data goes (the data-flow register, today)

| # | Statement | Source | Confirmed | Change asked |
|---|---|---|---|---|
| C1 | No real language model, vision model or voice engine is wired. In the demo the model roles are fixed fake components, and voice has no engine, so no real speech or letter text goes to any outside model. | `careflow/adapters/fake.py`; `docs/design/phase8-voice.md` | [ ] | [ ] |
| C2 | The database and sign-in run on a hosted Supabase project. The development project is in Tokyo and holds synthetic data only. **QUESTION:** the specification requires the production project to be in an India region; the privacy owner confirms the region at launch. | `BUILD-SPEC.md` 17 | [ ] | [ ] |
| C3 | File uploads are refused outside the demo profile until a malware scanner is wired and shown to answer a test file. The scanner client exists but nothing is installed or wired. | `careflow/adapters/clamav.py`; `careflow/app.py` | [ ] | [ ] |
| C4 | In the clinical profile, the system reports not ready if a required model role has no route approved for patient data. A route marked synthetic-only is never used for real patient data. | `BUILD-SPEC.md` section 2; `careflow/gateway.py` | [ ] | [ ] |
| C5 | Patient text never goes to a new outside service without a listed, approved route (a standing project rule). | `CLAUDE.md` rule 7 | [ ] | [ ] |

## D. Keeping, erasing and learning

| # | Statement | Source | Confirmed | Change asked |
|---|---|---|---|---|
| D1 | Erasing a patient is a two-person act: one front-desk person requests it, a different front-desk person approves it. It clears the encrypted name, date of birth and phone and marks the patient erased. It keeps the audit trail (with the identity left as a bare account id), the identity hashes and the identity-confirmation history. | `docs/design/patient-erasure-and-update-narrowing.md` | [ ] | [ ] |
| D2 | **QUESTION:** what erasure keeps of results, messages and voice chunks. The checklist row 61 states: result code, verified flag and times; identity tokens; audit rows; message and thread rows without bodies. Voice chunks are deleted at once. Please confirm against the current erasure functions (migrations 0056, 0057, 0059, 0061, 0065). | `docs/design/pre-launch-checklist.md` row 61 | [ ] | [ ] |
| D3 | Nothing is deleted automatically except by an admin running the audited voice-transcript purge. The scheduled retention job only REPORTS (one audit row per clinic per day) and deletes nothing. Retention periods are not set; they are for counsel and the clinical lead. | migration 0071; `docs/design/retention-and-erasure-purge.md` | [ ] | [ ] |
| D4 | Agent checkpoints are append-only and are not pruned. This is an accepted residual (owner, 2026-11-11). | `docs/design/pre-launch-checklist.md` row 74 | [ ] | [ ] |
| D5 | Only a person's review decision can become a training label. A computer-approved output never does. Nothing from a test, demo or synthetic-only deployment can, even if tagged live. Reviewer identities in any export are pseudonymised with a required secret. | `careflow/learning_labels.py`; `tests/test_phase4_modules.py` | [ ] | [ ] |
| D6 | A patient's own data download does not include clinical notes (decided 2026-11-08). Whether an automatically claimed patient account may see the care topic in its download is still open (row 66). | `docs/design/privacy-owner-confirmation.md` section 2 | [ ] | [ ] |
| D7 | Consent: three fixed purposes (care delivery, AI-assisted processing, service improvement). Every published notice version is kept, and a later "no" is recorded as a new decision, not a deletion. | `careflow/consent_settings.py`; `docs/drafts/consent-notices-draft.md` | [ ] | [ ] |

## E. Questions the code cannot answer
1. Who is the named privacy owner, the incident lead and the grievance officer?
2. Which backups exist, how long do they keep deleted rows, and who can restore them?
3. Which people at the hosting and database providers can reach the data, and under what agreement?
4. Is there a data-processing agreement with each provider named above?

## Signature block (left blank)

| Section | Confirmed / change asked | Name | Role | Date | Signature |
|---|---|---|---|---|---|
| A. Personal data held | | | | | |
| B. Who sees what | | | | | |
| C. Where data goes | | | | | |
| D. Keeping, erasing, learning | | | | | |
| E. Answers to the questions | | | | | |
