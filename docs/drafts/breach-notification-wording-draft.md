# Breach notification: wording and contacts

> **AI DRAFT. Not legal advice. For owner and counsel review. Not approved.**
> The technical steps are in `docs/runbooks/breach.md`. This draft only supplies the human-facing wording and the contact blanks. The spec (16.5) names the 72-hour notification window under the DPDP rules; counsel must confirm the current rule, to whom it applies, and what starts the clock.

**Who acts:** the owner names the people and fills the contacts; counsel approves the notices before an incident, not during one.

## Contacts (blank until filled)
- Incident lead: [OWNER: name, role, phone]. Deputy: [OWNER].
- Counsel: [OWNER: name, phone, out-of-hours route].
- Regulator notification route and form: [COUNSEL].
- Hosting and database provider security contacts: [OWNER].

## Clock
Record the time the clinic became aware in the incident register. Notify the regulator by [COUNSEL: deadline in hours from awareness; the spec says 72]. Notify affected patients [COUNSEL: when and how].

## Notice to the regulator (outline)
What happened, in plain words; when it happened and when we found out; which kinds of data (identity, contact, messages, documents, clinical notes) and roughly how many people [fill from the audit log]; what we did to contain it; what patients should do; who to contact. [COUNSEL: add every element the rule requires.]

## Notice to patients (outline)
"We are writing to tell you that [what happened] on [date]. The information involved was [kinds of data]. We have [what we did]. We recommend that you [OWNER/COUNSEL: advice]. You can contact [contact] for help. We are sorry." Keep it free of clinical advice; anything that tells a patient what a symptom means needs `# CLINICAL-PLACEHOLDER: needs clinician sign-off`.

## After the incident
Enter it in the incident register (it feeds the repair-time metric). Review what the audit and access logs showed. Record what changed.
