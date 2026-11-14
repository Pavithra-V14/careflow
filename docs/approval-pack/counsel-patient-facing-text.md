# Patient-facing text for counsel's review (patient portal)

**AI-prepared. Not legal advice.** This file was prepared by an AI model from the strings in the patient portal as they are today (2026-11-14). It lists the exact English wording a patient sees and the questions counsel needs to answer. It decides nothing and approves nothing. The wording is a placeholder until counsel and the privacy owner have said otherwise. The Tamil text is AI-prepared and unreviewed and is not covered here (see `tamil-review.md`).

Where the text comes from: everything below is in the frontend's English string files, quoted exactly. The consent and voice notices are different: their text comes only from the API (the owner's `CAREFLOW_CONSENT_NOTICES` entry, or a labelled demo placeholder). The frontend holds none of it and shows it exactly as sent. What is listed for those is the wording the frontend puts AROUND the notice.

Not edited: the other files in this folder are owned by the backend and are untouched.

## 1. Requests about the record (erase, correct a detail, record a nominee)

What the patient sees:

- `portal.requests.title`: "Ask the hospital about your record"
- `portal.requests.body`: "You can ask for your record to be erased, for a detail to be corrected, or for a nominee to be recorded. The request has no free text. The hospital's front desk takes it from there."
- `portal.requests.kinds.erasure`: "Erase my record"
- `portal.requests.kinds.correction`: "Correct a detail"
- `portal.requests.kinds.nominee`: "Record a nominee"
- `portal.requests.confirmTitle`: "Send this request?"
- `portal.requests.confirmBody`: "This sends a request to the hospital. A correction names the detail only, never the new value. Nothing is changed by sending it."
- `portal.requests.sent`: "Your request was sent."
- `portal.requests.already`: "This request was already on record."
- `portal.requests.refused`: "This cannot be done for this account: it must be active and linked to a record at that hospital."
- `portal.requests.invalid`: "That request was not accepted. Check your choices. Nothing was sent."
- `portal.requests.status.open`: "Open"
- `portal.requests.status.acknowledged`: "Seen"
- `portal.requests.status.resolved`: "Resolved"
- `portal.requests.status.declined`: "Declined"

What the route does (from its description): the request carries no free text; a correction names which detail (name, date of birth or phone), never the value; the front desk takes it from there; erasure goes through the existing two-person flow; nothing is changed by the request itself.

Questions for counsel:

1. Does "Erase my record" describe the request correctly, given that erasure goes through a two-person flow and may not be possible in full (for example for records the hospital must keep)? Should the screen say anything about that, and if so, what?
2. Is "Nothing is changed by sending it." accurate and sufficient in every case, including erasure?
3. Should the patient be told how long the hospital takes to answer, or what happens next? The screen says nothing about timing today; it makes no promise.
4. "Declined" is shown when the hospital declines. Does counsel want a reason, a contact route or an appeal route shown with it?
5. Are "nominee" and "Record a nominee" the right words and the right scope for this hospital's law and practice?
6. Is a patient allowed to make these requests for a dependant (under 18) today? Nothing is offered for dependants yet.

## 2. The profile: the sex label and the details form

What the patient sees:

- `portal.profile.title`: "Your details"
- `portal.profile.name`: "Name on your record"
- `portal.profile.sex`: "Sex"
- `portal.profile.noSexList`: "The hospital has not set up a list of choices yet."
- `portal.profile.language`: "Preferred language"
- `portal.profile.saved`: "Your details were saved."
- `portal.profile.invalid`: "That choice is not on the hospital's list. Nothing was saved."
- `portal.profile.unavailable`: "This is not set up yet. Nothing was saved."

The words the patient chooses from for sex are NOT in the frontend: they come from the clinic's own list, shown as sent (underscores shown as spaces). The patient can set a value but cannot clear it (see backend request 32).

Questions for counsel and the privacy owner:

1. May the portal collect and store a "sex" value at all, and for what stated purpose? Does the label "Sex" match the wording of the clinic's list and the law's term for it?
2. Does the person need a purpose statement or a consent entry next to this field?
3. Who may see the value once saved, and should the screen say so?
4. Should the patient be able to remove a value they set? (The backend has not said what clearing does.)

## 3. Consent notices as shown

The notice text is shown exactly as the API sends it, in a plain text block with its version. The frontend's own words around it:

- `portal.consent.title`: "Notices and your decisions"
- `portal.consent.none`: "No notice has been published yet."
- `portal.consent.decision`: "Your decision"
- `portal.consent.granted`: "Granted"
- `portal.consent.withdrawn`: "Withdrawn"
- `portal.consent.noDecision`: "No decision yet"
- `portal.consent.grantTitle`: "Grant this?"
- `portal.consent.confirmBody`: "Your decision is recorded against the version shown above, with the time."
- `portal.consent.recorded`: "Your decision was recorded."
- `portal.consent.changed`: "The notice changed while you were reading. The page will refresh; please read the new version."
- `portal.consent.withdrawnDone`: "Your consent was withdrawn. You can grant it again at any time."

How it behaves: granting asks for a confirmation first. Withdrawing takes one click, is recorded at once, shows "Your consent was withdrawn. You can grant it again at any time." and offers "Grant again" (which asks for its confirmation). A decision is recorded against the version shown.

Questions for counsel:

1. Is "Your decision is recorded against the version shown above, with the time." enough to tell the patient what is kept?
2. Withdrawal is immediate and needs no confirmation. Does counsel want the patient told what withdrawal does not undo (data already used or kept)? The screen does not say.
3. Should the screen say what happens to services that depend on a withdrawn consent (for example care delivery)?
4. Does each purpose need its own wording about how long the decision is kept?

## 4. The voice notice and the voice screen

The voice consent notice itself comes only from the API (the notice whose purpose is `voice_session`). Until the API supplies one the screen says voice is not set up and the Open button stays disabled. In the demo, the backend supplies a labelled placeholder. The frontend's own words:

- `portal.voice.title`: "Voice"
- `portal.voice.intro`: "Voice lets you say a reply out loud instead of typing it. It is optional and it stays off until you turn it on."
- `portal.voice.on`: "Voice is on for your account."
- `portal.voice.off`: "Voice is off for your account."
- `portal.voice.confirmOnTitle`: "Turn voice on?"
- `portal.voice.confirmBody`: "This changes one setting on your account. You can change it back at any time."
- `portal.voice.workflow.slot_offer`: "Replying to an appointment offer"
- `portal.voice.workflow.intake_question`: "Answering a question before a visit"
- `portal.voice.workflow.readiness_item`: "Answering an item on a visit checklist"
- `portal.voice.agree`: "I have read the notice above and I agree to this voice session."
- `portal.voice.noAudio`: "This screen does not record or play sound yet. The audio connection is not set up."
- `portal.voice.notSetUp`: "Voice sessions are not set up yet. Nothing can be opened."
- `portal.voice.unavailable`: "Voice is not available at this hospital right now. No session was opened."

How it behaves: voice is off until the patient turns it on (with a confirmation). A session needs a chosen purpose (nothing is chosen for the patient) and an unticked agreement. The screen opens the session record only. It does not record or play any sound today.

Questions for counsel:

1. Is the sentence "I have read the notice above and I agree to this voice session." valid agreement for a voice session, or does it need to be a recorded decision against a notice version (as for the other consents)?
2. The notice text must be written by counsel (the owner's `CAREFLOW_CONSENT_NOTICES`). What must it say about recording, retention, who can listen, the purposes of the three session types, and the safety screening of what is said?
3. Is a patient under 18 allowed to open a voice session? Today nothing in the screen stops them or asks for a guardian.
4. Does counsel want the patient told that the audio connection is not set up (current wording), or should the screen say nothing until it is?
5. Does anything have to be shown to a patient about third-party processing of audio (for example a model provider) before a real connection is approved?

## 5. The complaint forms (signed in, and without signing in)

What the patient sees, signed in:

- `portal.grievance.title`: "Send a complaint"
- `portal.grievance.body`: "Use this to send a complaint. To ask for your record to be erased, a detail corrected or a nominee recorded, use the section above. A person at the hospital reads it."
- `portal.grievance.category`: "Category"
- `portal.grievance.categoryHint`: "A code from the hospital's list (lowercase letters, digits and underscores). The hospital tells you which codes exist."
- `portal.grievance.text`: "What you want to tell us"
- `portal.grievance.confirmBody`: "This sends your message to the hospital. It is stored encrypted."
- `portal.grievance.filed`: "Your message was sent."
- `portal.grievance.categoryRefused`: "That category is not on the hospital's list. Nothing was sent."
- `portal.grievance.refused`: "This cannot be done for this account: it must be active and linked to a record of that clinic."
- `portal.grievance.unavailable`: "This is not available right now (the category list or the encryption is not set up). Nothing was sent."
- `portal.grievance.status.open`: "Open"
- `portal.grievance.status.acknowledged`: "Seen"
- `portal.grievance.status.resolved`: "Resolved"

Without signing in (on the sign-in page):

- `portal.publicGrievance.title`: "Send a complaint without signing in"
- `portal.publicGrievance.body`: "This goes to the hospital without signing in. A person at the hospital reads it."
- `portal.publicGrievance.confirmBody`: "This sends your message to the hospital. It is stored encrypted."
- `portal.publicGrievance.filed`: "Your message was sent."
- `portal.publicGrievance.noClinic`: "No hospital is set up to receive this right now. Nothing was sent."
- `portal.publicGrievance.unavailable`: "This is not available right now. Nothing was sent."
- `portal.publicGrievance.refused`: "This cannot be done right now. Nothing was sent."

Questions for counsel:

1. Is "It is stored encrypted." the right thing to say, or should it say who reads the complaint and how long it is kept?
2. The complaint without signing in has no identity attached. Should the screen say the hospital cannot reply, or cannot confirm who sent it? Today it says only that it goes to the hospital without signing in.
3. Is there a statutory grievance route, form or time limit that the screen must mention or follow?
4. The category is a code from the clinic's list. The signed-out form has no list to choose from yet (backend request 26), so the patient types a code. Is a typed code acceptable until a public list exists, or should the signed-out form wait?
5. The complaint text may contain health information. Does counsel want a warning about what to include?
6. Should a patient be told, after sending, what happens next and when to expect an answer? Today the screen says only that the message was sent.

## 6. Questions that apply to everything above

1. Is the emergency wording shown on every patient page (a placeholder owned elsewhere) enough next to the messages and complaint forms, or should the forms carry their own?
2. Is a single plain-language register acceptable, or does counsel need a different register for any of these texts?
3. Who approves a change to any of this wording after counsel signs it off, and how is a change recorded?
