# Grievance and patient-rights procedure: draft

> **AI DRAFT. Not legal advice. For owner and counsel review. Not approved.**
> Describes what the system does today. Every time limit and every escalation contact is a blank for the owner and counsel.

**Who acts:** the privacy owner names the responsible person and fills the blanks; counsel checks it against the DPDP Act and any state or professional-council rules. Then the owner sets the category list in `CAREFLOW_GRIEVANCE_CATEGORIES` (the demo pack holds a labelled placeholder list) and publishes this text where patients can read it.

## What a patient can do (already built)
1. **File a grievance** from the portal, choosing a category from the clinic's list. The text is stored encrypted and is visible only to the clinic staff roles that handle grievances.
2. **Ask for a copy of their data.** The portal download covers the patient's own record. It does not include clinical notes (an owner decision); [COUNSEL: confirm this is lawful or say how notes are provided].
3. **Ask for correction or erasure.** A correction is reviewed by a doctor; erasure removes identifying fields and keeps the audit trail with the identity replaced (spec 9.4). [COUNSEL: confirm the statutory record-keeping exceptions to state to the patient.]
4. **Nominate a person** to act for them. [OWNER/COUNSEL: not drafted; define who may be a nominee and how identity is checked.]

## What the clinic does
- Every grievance has a status that staff update (the system records who and when). The patient sees the status.
- Acknowledge within [COUNSEL: period]. Resolve or give a reasoned reply within [COUNSEL: period].
- The reply is given by [OWNER: named role]. If the patient is not satisfied: [OWNER/COUNSEL: escalation contact, and the external body the law names].
- Urgent clinical content is not handled here: the portal tells the patient to contact the clinic directly for anything urgent. [CLINICAL-PLACEHOLDER: needs clinician sign-off for the exact wording.]

## Categories (proposal only; the clinic chooses its own)
Access to my data; Correction of my data; Erasure of my data; Nominee; Complaint about care; Complaint about staff; Complaint about the system; Other. [OWNER: confirm or replace. The system accepts only the words the clinic configures.]

## Open points
1. Who sees which category (a complaint about a staff member should not be seen by that member). [OWNER]
2. Version and date of the procedure itself. [OWNER]
