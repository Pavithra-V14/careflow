# Consent notices: draft wording

> **AI DRAFT. Not legal advice. For owner and counsel review. Not approved.**
> Facts below come from `BUILD-SPEC.md` (sections 2, 9.4, 11.6, 17.6) and the current build. Anything in square brackets is a blank only the owner or counsel can fill.

**Who acts:** the privacy owner confirms every factual statement is true of the deployed system; counsel supplies final legal wording. Then the owner publishes with `scripts/seed_consent_notices.py` (setting `CAREFLOW_CONSENT_NOTICES`, one `{version, body}` per purpose) and sets the short names in `CAREFLOW_CONSENT_LABELS`.

The three purposes are fixed by the system: `care_delivery`, `ai_processing`, `service_improvement`. A notice version is never edited once shown; a change is a new version (spec 9.4: every published version is kept).

## care_delivery  (label proposal: "Your care at [CLINIC NAME]")
[CLINIC NAME] keeps the information you give us and the records of your visits so that your care team can look after you: your identity details, appointments, messages with the clinic, results, documents and clinical notes. Only staff of [CLINIC NAME] whose role needs it can see them. Every time staff open or change your record, we keep a log of it. We keep your record for [OWNER/COUNSEL: retention period; see the retention proposal]. You can ask to see, correct or download your data, or raise a complaint, as described in [OWNER/COUNSEL: link to the published procedure].
Choosing "no" means [OWNER/COUNSEL: state exactly what the clinic can still do for a patient who declines; the system cannot give care without a record].

## ai_processing  (label proposal: "Computer help with your messages and documents")
Some of the work at [CLINIC NAME] is done with the help of computer software, including software that reads text. It is used to sort and summarise what you send and to prepare drafts. **A member of staff reviews its output before it affects your care** [OWNER: confirm; true while the review gate is on]. Software never decides on its own that a symptom is not urgent. Messages are first checked by fixed safety rules, and a message that matches an urgent pattern is sent to a person at once.
Where the software runs: [OWNER: state truthfully, per spec section 2 and the data-flow register 17.6: on the clinic's own machines only / also an approved outside service named ___].
Your messages and documents used this way are kept for [OWNER/COUNSEL: period]. Your choice does not change how fast urgent matters are escalated to a person.
If you say "no": [OWNER/COUNSEL: describe the manual path and its consequences].

## service_improvement  (label proposal: "Improving our service")
With your permission, [CLINIC NAME] may use information about how the service performs (for example how long replies take) to improve it. [OWNER/COUNSEL: say whether any patient text is ever used, and in what form. The build rule is that a computer-approved output is never used to teach the software; only a person's decisions are, and only with the permission described here.] Saying "no" does not affect your care.

## Open points for counsel
1. Whether consent is the right basis for each purpose, and the DPDP notice content required in your state.
2. Language and reading level; translations into [OWNER: languages].
3. Withdrawal: the system records a later "no" as a new decision, not a deletion. Confirm that is acceptable wording.
4. Children and guardians: not drafted; the system does not yet model guardian consent. [COUNSEL]
