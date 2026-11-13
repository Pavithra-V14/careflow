# Cover note for counsel

> **AI-prepared, not approved by counsel. Not legal advice.** Prepared 2026-11-13. Never loaded by the application; never marked approved. The statutes and rules named below are pointers from the assistant's general knowledge, not a reading of their current text: counsel confirms the current law, its application in the clinic's state, and anything missing from this list.

## What this is
CareFlow is a clinic's scheduling, messaging and review system. It runs on synthetic data only today. Before any real patient data is used, four texts need counsel. The drafts live in `docs/drafts/` (already written, each marked as an AI draft); this note lists each one with the questions only counsel can answer. Nothing in the drafts is approved, and the software never loads them.

## The four drafted documents and the questions for counsel

### 1. Consent notices (`docs/drafts/consent-notices-draft.md`)
Three purposes: care delivery, AI-assisted processing, service improvement. A notice version is never edited once shown.
1. Is consent the right lawful basis for each of the three purposes, or is another basis available for some (for example care delivery)?
2. What must each notice contain, in what languages, and at what reading level?
3. The system records a later "no" as a new decision, not a deletion. Is that acceptable?
4. Children and guardians: the system does not model guardian consent. What must it do, and what is the age threshold?
5. What may the clinic still do for a patient who declines each purpose?
6. Is the statement that staff review the software's output before it affects care accurate enough to put in a notice, and who confirms it is true?

### 2. Grievance and patient-rights procedure (`docs/drafts/grievance-and-rights-procedure-draft.md`)
1. Time limits for acknowledging and for resolving a grievance, and who must reply.
2. Which external body a dissatisfied patient is referred to.
3. The portal download excludes clinical notes (an owner decision). Is that lawful, or how must notes be provided?
4. Correction and erasure: which record-keeping duties limit erasure, and what must the patient be told?
5. The nominee right: who may be a nominee, and how is identity checked?
6. Who may see which grievance category (a complaint about a staff member should not be seen by that member)?

### 3. Retention schedule (`docs/drafts/retention-schedule-proposal-draft.md`, and `docs/approval-pack/clinic-proposed-values.md`)
The periods are copied from the project's own specification as "starting values, not fixed law".
1. Which rules set the retention period for an adult clinical record, a minor's record and a pregnancy or childbirth record in the clinic's state?
2. Does a legal hold or a pending grievance suspend deletion, and who records it?
3. Is "permanent" acceptable for consent records and the audit log given data-minimisation duties?
4. Backups: how long may deleted rows survive, and what is a patient told?
5. The deletion job today only reports (it deletes nothing). Is a report-only job acceptable until the periods are settled?

### 4. Breach notification wording (`docs/drafts/breach-notification-wording-draft.md`)
1. The notification deadline, to whom it is owed, and what starts the clock. The specification names 72 hours; counsel confirms.
2. Every element the regulator notice must contain, and the form.
3. When and how affected patients must be told.
4. The incident lead and out-of-hours contact are the owner's, not counsel's.

## Indian-law points to check (pointers only; verify the current text)
- **Digital Personal Data Protection Act, 2023, and the rules made under it.** Check: notice and consent content; the duties of a data fiduciary; consent withdrawal; rights of access, correction, erasure and nomination; grievance redressal; children's data and verifiable guardian consent; breach intimation (to the regulator and to affected people); duties on retention and erasure when the purpose ends; any status as a "significant" fiduciary; transfers outside India. Which provisions are in force today, and from what date?
- **Clinical establishment record rules.** Check whether the Clinical Establishments (Registration and Regulation) Act, 2010 applies in the clinic's state, and what its rules or state rules say about keeping medical records and giving a patient a copy.
- **Professional-council rules on medical records** (the medical council's code of conduct and any state council rules): record-keeping period, patient access to records, and electronic records.
- **Information Technology Act, 2000 and rules made under it** (including any rules on sensitive personal data still in force): is anything there that sits alongside the DPDP Act for this clinic?
- **Telemedicine practice guidelines** and any rules on teleconsultation records, if voice or remote consultation is offered.
- **Health-data frameworks** (for example any national digital health mission rules on health data): do they apply to this clinic?
- **Limitation periods** for medical negligence claims, which often drive how long records are kept.
- **Residency:** the specification says production must be in an India region. Does any rule also require, or forbid, particular handling of health data crossing borders?

## What the system does that counsel should know (from the code, not an opinion)
Patient name, date of birth and phone are stored only as ciphertext. Staff access is logged. Erasure is a two-person act and keeps the audit trail with the identity replaced. Voice has no real engine today. The system never lets a computer's output become a training label: only a person's decision can, and not from a demo or synthetic-only deployment. The full list for the privacy owner is `docs/approval-pack/privacy-owner-confirmation-prefilled.md`.

## What counsel returns
For each document: the approved wording or the changes, any extra questions, the date, and counsel's name. Please mark anything that needs a state-specific answer.
