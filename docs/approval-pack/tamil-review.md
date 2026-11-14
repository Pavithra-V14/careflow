# Tamil review pack (AI-prepared, NOT reviewed by a native speaker)

Prepared 2026-11-14. Every Tamil string in the staff console and the patient portal, next to its English, with a second-pass check by an AI model (not a native speaker) and a column for a native reviewer. **No string in this file is marked as reviewed.** The sign-off list (`frontend/src/shared/i18n/ta-reviewed.json`) is still empty, and every one of the 1614 Tamil strings stays on the unreviewed list (`frontend/TAMIL-UNREVIEWED.md`) until a native reviewer signs it off. Read the README first: [tamil-review-README.md](tamil-review-README.md).

**Regenerated 2026-11-14** by `npm run i18n:pack` from the strings as they are now, so it covers every current string, including the newest screens (patient voice setting, consent withdrawal and history, activating a staff account, withdrawing and retiring a red-flag rule). Each is written and second-passed in the same way and is just as unreviewed.

## What the second pass looked at

Meaning errors, register for patients, medical or safety terms, unsafe ambiguity, inconsistent terms. Findings are graded:

- **A** safety, privacy or patient-rights meaning could be misread. Needs a native speaker, ideally one who works in a clinic.
- **B** wrong or odd meaning, wrong register, or an inconsistent term. Needs a native speaker.
- **C** minor wording, or an administrator-only technical label.

How it was done, honestly: all 1614 pairs were checked by script (placeholders, empty strings, untranslated Latin words, informal pronouns), and the strings that carry meaning (sentences of four words or more, and short strings that differ from their English) were read by an AI model. The AI model is not a native speaker, so the lists below are the places where it was unsure or found a likely problem; a string marked "no issue found" has not been cleared by a person.

Automatic checks: 1614 strings; 0 empty; the {{placeholders}} are identical in English and Tamil in every string; 0 informal pronouns (நீ, உன்) found; 31 Tamil strings equal their English on purpose (Part 6).

Notes: 64 grade A, 94 grade B, 47 grade C (205 rows with a note; some notes cover a group of similar strings, named in the note).

## Part 1. Strings that most need a native speaker (safety first)

### 1. Red flags, screening outcomes and escalation (patient safety)

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `backfill.reply.enterBody` | Records the stored message you picked as the patient's reply. The red-flag rules screen it first; a red flag or a reply they cannot assess raises the nurse task and ends the run. | நீங்கள் தேர்ந்தெடுத்த சேமிக்கப்பட்ட செய்தியை நோயாளியின் பதிலாகப் பதிவு செய்கிறது. சிவப்புக் கொடி விதிகள் முதலில் பரிசோதிக்கும்; சிவப்புக் கொடி அல்லது மதிப்பிட முடியாத பதில் செவிலியர் பணியை எழுப்பி ஓட்டத்தை முடிக்கும். | "Red flag" is rendered literally as சிவப்புக் கொடி ("red flag", a flag that is red). It is the central safety term; a native clinician should confirm the phrase patients and nurses actually use (for example an "alarm sign" wording). Same term in backfill.reply.note, backfill.reply.reasons.red_flag, backfill.reply.savedOutcome.red_flag and others. |
| `backfill.reply.reasons.red_flag` | The rules found a red flag | விதிகள் சிவப்புக் கொடியைக் கண்டன | Red flag wording, see backfill.reply.enterBody. |
| `backfill.reply.savedOutcome.red_flag` | a red flag was found; a nurse task was raised | சிவப்புக் கொடி கண்டறியப்பட்டது; செவிலியர் பணி எழுப்பப்பட்டது | Red flag wording, see backfill.reply.enterBody. Also "raised a nurse task" is rendered செவிலியர் பணி எழுப்பப்பட்டது ("a nurse job was woken"): check that it reads as "a task was created for a nurse". |
| `readiness.statuses.nurse_escalated` | Escalated to a nurse | செவிலியருக்கு அனுப்பப்பட்டது | "Escalated to a nurse" is rendered செவிலியருக்கு அனுப்பப்பட்டது ("sent to a nurse"), which drops the sense of raising urgency. Same in readiness.statuses.red_flag_escalated and the dashboard and alert strings that use "escalation". |
| `readiness.statuses.red_flag_escalated` | Red flag: escalated to a nurse | சிவப்புக் கொடி: செவிலியருக்கு அனுப்பப்பட்டது | Red flag wording (see backfill.reply.enterBody) plus the "escalated = sent" loss of urgency. |
| `backfill.reply.screenedNote` | The rules did not clear this reply. Entering it raises the nurse task and ends the run. | விதிகள் இந்தப் பதிலைத் தெளிவானது எனக் குறிக்கவில்லை. இதை உள்ளிட்டால் செவிலியர் பணி எழுப்பப்பட்டு ஓட்டம் முடியும். | "The rules did not clear this reply": "clear" (screening found no red flag) is rendered தெளிவானது ("clear" as in obvious or understandable). Safety outcome word; the same "clear" appears in the saved/outcome strings. |
| `backfill.reply.outcome.clear` | Clear | தெளிவு | Safety screening outcome "Clear" (the rules found no red flag) is rendered தெளிவு ("clarity"); the lower-case form (backfill.reply.savedOutcome.clear) is the same. A nurse could read it as "the reply is clear / easy to understand" instead of "no red flag found". Highest priority for a native clinician. |
| `backfill.reply.outcome.red_flag` | Red flag | சிவப்புக் கொடி | Red flag wording (see backfill.reply.enterBody). |
| `alerts.class.red_flag_overdue` | Red-flag task overdue | சிவப்புக் கொடிப் பணி தாமதமானது | "Red-flag task overdue" (an overdue safety task) uses the literal சிவப்புக் கொடி term; an overdue red-flag alert must be unmistakable. |
| `care.plans.status.screening` | Being screened | பரிசோதனையில் உள்ளது | "being screened" (the rules read the text for red flags) is rendered பரிசோதனையில் உள்ளது; பரிசோதனை is the word for a medical examination or test. A clinician could read that the patient is being tested. The same word appears in backfill.reply.screening and the "rules screen" strings. |
| `backfill.reply.screening` | Rules screening | விதிகள் பரிசோதனை | "Rules screening" rendered விதிகள் பரிசோதனை; see care.plans.status.screening. |
| `dashboard.metric.red_flag_recall` | Red-flag recall | சிவப்புக் கொடி மீட்பு | "Red-flag recall" (how many red flags the system catches, i.e. sensitivity) is rendered சிவப்புக் கொடி மீட்பு; மீட்பு means rescue or retrieval. A safety metric an administrator reads must not be ambiguous. Same in urgent_case_recall. |
| `dashboard.metric.urgent_case_recall` | Urgent case recall | அவசர நிகழ்வு மீட்பு | "recall" (sensitivity) rendered மீட்பு (rescue); see dashboard.metric.red_flag_recall. |

### 2. Clinical results, decisions, medications and red-flag rules

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `care.gaps.recheckBody` | This records that you re-checked the sample, under your name. It can be done once. | நீங்கள் மாதிரியை மீண்டும் சரிபார்த்தீர்கள் என்று உங்கள் பெயரில் பதிவாகும். இதை ஒரு முறை மட்டுமே செய்ய முடியும். | "sample" (a sampled decision to be re-checked by a second person) is rendered மாதிரி, which a reader will take as a laboratory specimen. Meaning risk in a clinical setting. |
| `care.gaps.recheckTitle` | Mark this sample re-checked? | இந்த மாதிரியை மீண்டும் சரிபார்க்கப்பட்டதாகக் குறிக்கவா? | Same "sample" = specimen ambiguity. |
| `care.gaps.rechecked` | The sample was marked re-checked. | மாதிரி மீண்டும் சரிபார்க்கப்பட்டதாகக் குறிக்கப்பட்டது. | Same "sample" = specimen ambiguity. |
| `care.gaps.sameDecider` | You made the decision this sample was drawn from, so someone else must re-check it. Nothing was recorded. | இந்த மாதிரி எடுக்கப்பட்ட முடிவை நீங்களே எடுத்தீர்கள்; எனவே வேறொருவர் இதை மீண்டும் சரிபார்க்க வேண்டும். எதுவும் பதிவாகவில்லை. | Same "sample" ambiguity; the sentence இந்த மாதிரி எடுக்கப்பட்ட முடிவை நீங்களே எடுத்தீர்கள் is hard to follow ("you made the decision this sample was taken from"). |
| `care.results.body` | This records a result for the patient. It cannot be edited afterwards. | இது நோயாளிக்கான ஒரு முடிவைப் பதிவு செய்கிறது. பின்னர் இதைத் திருத்த முடியாது. | "result" (a clinical result) is rendered முடிவு, the same word used for "decision" everywhere else. A reader cannot tell a test result from a decision. |
| `care.results.changeHint` | Only when this result replaces another. An amendment is a new measurement; a correction means the earlier value was wrong and starts a review. | இந்த முடிவு மற்றொன்றை மாற்றும்போது மட்டும். மேம்படுத்தல் என்பது புதிய அளவீடு; திருத்தம் என்பது முந்தைய மதிப்பு தவறானது என்று பொருள்படும், மதிப்பாய்வைத் தொடங்கும். | "amendment" is rendered மேம்படுத்தல் ("improvement / upgrade"), which is not what an amended result means. The difference between an amendment (a new measurement) and a correction (the earlier value was wrong, starts a review) is safety-relevant. |
| `care.results.done` | The result was recorded. | முடிவு பதிவானது. | "result" = முடிவு, same ambiguity as care.results.body. |
| `care.results.changes.amendment` | Amendment | மேம்படுத்தல் | See care.results.changeHint: amendment rendered மேம்படுத்தல் ("improvement"). |
| `referrals.proposal` | Triage proposal | வகைப்படுத்தல் முன்மொழிவு | "Triage" (sorting patients by clinical urgency) is rendered வகைப்படுத்தல் ("classification"), which drops the urgency meaning. The task kind triage_proposal itself stays in English by design; a native clinician should choose the Tamil for triage. |
| `admin.rules.approve` | Approve | ஒப்புக்கொள் | "Approve" is rendered ஒப்புக்கொள், the same verb that is also used for "acknowledge" (alerts) and that can mean "admit". For a two-doctor safety approval a clinician must choose the verb (for example ஒப்புதல் அளி). Same issue in approveTitle, approveBody and the result strings. |
| `admin.rules.approveTitle` | Approve this red-flag rule version? | இந்தச் சிவப்புக் கொடி விதிப் பதிப்பை ஒப்புக்கொள்ளலாமா? | Same verb issue as admin.rules.approve: this is the confirmation question before a doctor approves a red-flag rule. |
| `admin.rules.approveBody` | You are approving exactly the content shown below. The version becomes approved only if a second, different doctor also approves it. The person who wrote a version cannot decide it. | கீழே காட்டப்பட்டுள்ள உள்ளடக்கத்தையே நீங்கள் ஒப்புக்கொள்கிறீர்கள். இரண்டாவது, வேறொரு மருத்துவரும் ஒப்புக்கொண்டால் மட்டுமே இந்தப் பதிப்பு அங்கீகரிக்கப்படும். ஒரு பதிப்பை எழுதியவர் அதில் முடிவெடுக்க முடியாது. | Safety text for the approval confirmation. Check that it is unmistakable that a SECOND, different doctor is also needed, and that "the person who wrote it cannot decide it" reads as a rule, not a suggestion. |
| `admin.rules.rejectBody` | One rejection closes this version for good. It cannot be approved afterwards. | ஒரு நிராகரிப்பு இந்தப் பதிப்பை நிரந்தரமாக மூடிவிடும். அதன் பிறகு இதை ஒப்புக்கொள்ள முடியாது. | "One rejection closes this version for good" (நிரந்தரமாக மூடிவிடும்): check that the irreversibility is clear and that "close" is not read as "pause". |
| `admin.rules.twoDoctors` | A draft version becomes approved only when two different doctors approve it, each with a fresh authenticator code. One rejection closes the version. Nothing is approved by default. | வரைவுப் பதிப்பு, இரண்டு வெவ்வேறு மருத்துவர்கள் ஒவ்வொருவரும் புதிய அங்கீகார குறியீட்டுடன் ஒப்புக்கொண்டால் மட்டுமே அங்கீகரிக்கப்படும். ஒரு நிராகரிப்பு அந்தப் பதிப்பை மூடிவிடும். இயல்பாக எதுவும் அங்கீகரிக்கப்படுவதில்லை. | States the two-doctor rule and "nothing is approved by default" (இயல்பாக எதுவும் அங்கீகரிக்கப்படுவதில்லை). Check that இயல்பாக reads as "by default" and not "normally". |
| `admin.rules.draftNote` | This is a draft. It is never active. It becomes approved only when two different doctors approve it, and its author cannot be one of them. | இது ஒரு வரைவு. இது ஒருபோதும் செயலில் இருக்காது. இரண்டு வெவ்வேறு மருத்துவர்கள் ஒப்புக்கொண்டால் மட்டுமே இது அங்கீகரிக்கப்படும்; அதை எழுதியவர் அவர்களில் ஒருவராக இருக்க முடியாது. | Says a draft is never active. A wrong word here could make a draft look live. Check "never" (ஒருபோதும்) and "active" (செயலில்). |
| `admin.rules.statuses.draft_unreviewed` | Draft, not approved | வரைவு, அங்கீகரிக்கப்படவில்லை | "Draft, not approved": check it cannot be read as "approved draft". The word அங்கீகரிக்கப்பட்டது is used for the status "Approved" but the button verb is ஒப்புக்கொள்: two words for one idea. |
| `admin.rules.result.recorded` | Your approval was recorded. The version is not approved yet: a second, different doctor must approve it. | உங்கள் ஒப்புதல் பதிவு செய்யப்பட்டது. இந்தப் பதிப்பு இன்னும் அங்கீகரிக்கப்படவில்லை: இரண்டாவது, வேறொரு மருத்துவர் ஒப்புக்கொள்ள வேண்டும். | "Your approval was recorded. It is not approved yet: a second, different doctor must approve it." Check that the two halves cannot be read as "approved". |
| `admin.rules.result.approved` | Your approval was recorded and this version is now approved. | உங்கள் ஒப்புதல் பதிவு செய்யப்பட்டது; இந்தப் பதிப்பு இப்போது அங்கீகரிக்கப்பட்டுள்ளது. | "This version is now approved": check the word is the same as the status word and cannot be read as "recorded only". |
| `admin.rules.result.author_cannot_decide` | You wrote this version, so you cannot decide it. A different doctor must. | இந்தப் பதிப்பை நீங்கள் எழுதினீர்கள், எனவே இதில் முடிவெடுக்க முடியாது. வேறொரு மருத்துவர் முடிவெடுக்க வேண்டும். | Tells the author they cannot decide their own rule. Check it is firm and not polite advice. |
| `admin.rules.author.confirmBody` | This saves exactly what is shown below as a draft. A draft is never active, and it is approved only when two different doctors approve it. You cannot approve a draft you wrote. | கீழே காட்டப்பட்டுள்ளதையே இது வரைவாகச் சேமிக்கிறது. வரைவு ஒருபோதும் செயலில் இருக்காது; இரண்டு வெவ்வேறு மருத்துவர்கள் ஒப்புக்கொண்டால் மட்டுமே அது அங்கீகரிக்கப்படும். நீங்கள் எழுதிய வரைவை நீங்களே அங்கீகரிக்க முடியாது. | Says the draft is never active and needs two different doctors, and that you cannot approve your own. Same wording checks as admin.rules.draftNote. |
| `admin.rules.author.saved` | The draft was saved. It is not approved and it is not active. | வரைவு சேமிக்கப்பட்டது. அது அங்கீகரிக்கப்படவில்லை, செயலிலும் இல்லை. | "Not approved and not active": check both halves are unmistakable. |
| `medications.noEnd` | No end recorded | முடிவு நேரம் பதிவு செய்யப்படவில்லை | "No end recorded" rendered முடிவு நேரம் பதிவு செய்யப்படவில்லை: it must never read as "ongoing". Check. |
| `medications.correct` | Correct this entry | இந்தப் பதிவைத் திருத்து | "Correct this entry" rendered இந்தப் பதிவைத் திருத்து: for a recording mistake only. Check a clinician would read it as "I recorded this wrongly" and not as "change the treatment". Wording is item 35 on docs/design/clinician-sheet.md. |
| `medications.stop` | Stop this medicine | இந்த மருந்தை நிறுத்து | "Stop this medicine" rendered இந்த மருந்தை நிறுத்து: the patient stopped taking it. Check it cannot be read as "stop giving it now" as an instruction to the patient. Wording is item 35 on docs/design/clinician-sheet.md. |
| `medications.stopTitle` | Record that this medicine was stopped? | இந்த மருந்து நிறுத்தப்பட்டது எனப் பதிவு செய்யலாமா? | "Record that this medicine was stopped?" rendered இந்த மருந்து நிறுத்தப்பட்டது எனப் பதிவு செய்யலாமா?: it records a fact, it does not order a stop. Check that is clear. |
| `medications.validToHint` | An empty end is saved as "no end recorded". It is not read as "still taking". | காலியான முடிவு "முடிவு பதிவு செய்யப்படவில்லை" என்று சேமிக்கப்படும். அது "இன்னும் எடுத்துக்கொள்ளப்படுகிறது" என்று கருதப்படாது. | The "still taking" statement was reworded without a gendered verb (இன்னும் எடுத்துக்கொள்ளப்படுகிறது, passive). A native clinician should confirm it reads naturally and clearly says an empty end is NOT read as ongoing. Item 36 on docs/design/clinician-sheet.md. |
| `admin.rules.withdrawBody` | A withdrawn draft is closed for good. It can never be approved and it is never in force. A withdrawn version is not the same as a retired one. Only a draft can be withdrawn. To change a rule, write a new version. | திரும்பப் பெறப்பட்ட வரைவு முற்றிலும் மூடப்படும். அதை ஒருபோதும் ஒப்புக்கொள்ள முடியாது; அது ஒருபோதும் நடைமுறையில் இருக்காது. திரும்பப் பெறுதல் என்பது நிறுத்துதலிலிருந்து வேறுபட்டது. வரைவை மட்டுமே திரும்பப் பெற முடியும். ஒரு விதியை மாற்ற, புதிய பதிப்பை எழுதுங்கள். | Says a withdrawn draft is closed for good and is NOT the same as retired. "திரும்பப் பெறுதல்" and "நிறுத்துதல்" are close in everyday Tamil, so the contrast may not come across. Clinician sheet item 37. |
| `admin.rules.retireBody` | Your retire decision is recorded now, and nothing is retired until a second, different doctor also records one. A retired version never comes back: a new version is the way back. If this is the newest version of its rule code, the whole rule code stops being in force. | உங்கள் நிறுத்த முடிவு இப்போது பதிவாகும்; வேறொரு மருத்துவரும் பதிவு செய்யும் வரை எதுவும் நிறுத்தப்படாது. நிறுத்தப்பட்ட பதிப்பு மீண்டும் வராது; புதிய பதிப்பே திரும்ப வழி. இது இந்த விதிக் குறியீட்டின் புதிய பதிப்பாக இருந்தால், முழு விதிக் குறியீடும் நடைமுறையில் இருக்காது. | Says nothing is retired until a second, different doctor records it, and that a retired newest version switches off the whole rule code. A wrong reading could leave a rule in force or switch one off. A native speaker who is a clinician should check. Clinician sheet item 37. |
| `admin.rules.retireTitle` | Retire this approved red-flag rule version? | ஒப்புக்கொள்ளப்பட்ட இந்தச் சிவப்புக் கொடி விதிப் பதிப்பை நிறுத்தவா? | Asks whether to retire an approved red-flag rule version; "நிறுத்தவா" can read as "stop it now" although one doctor alone cannot retire it. |
| `admin.rules.retireCount` | Retire decisions recorded so far: {{count}}. Two different doctors are needed. | இதுவரை பதிவான நிறுத்த முடிவுகள்: {{count}}. இரண்டு வெவ்வேறு மருத்துவர்கள் தேவை. | Says how many retire decisions exist and that two different doctors are needed. A count read wrongly could make a person think a rule is already retired. |
| `admin.rules.statuses.withdrawn` | Withdrawn | திரும்பப் பெறப்பட்டது | A status word. "திரும்பப் பெறப்பட்டது" (withdrawn) must not be read as "retired" (நிறுத்தப்பட்டது), since the two mean different things for whether a rule is in force. |
| `admin.rules.result.retired` | Your retire decision was recorded and this version is now retired. | உங்கள் நிறுத்த முடிவு பதிவானது; இந்தப் பதிப்பு இப்போது நிறுத்தப்பட்டது. | Tells a doctor a rule version is now retired. Must be unambiguous that it is out of use. |
| `admin.rules.result.withdrawn` | This draft is withdrawn. It is closed for good and is not in force. | இந்த வரைவு திரும்பப் பெறப்பட்டது. அது முற்றிலும் மூடப்பட்டது; நடைமுறையில் இல்லை. | Tells a doctor a draft is closed and not in force; also shown when a repeat request reaches an already withdrawn version. |
| `admin.rules.result.not_approved` | This version is not approved, so it cannot be retired. Nothing was recorded. | இந்தப் பதிப்பு ஒப்புக்கொள்ளப்படவில்லை, எனவே நிறுத்த முடியாது. எதுவும் பதிவு செய்யப்படவில்லை. | Says nothing was recorded because the version is not approved; a reader must not take it as retired. |

### 3. Consent, privacy, erasure and patient rights

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `erasure.keepsBody` | It keeps the request and its audit rows (they are never deleted), the patient's record number, and the keyed fingerprints used for matching, which ignore an erased record. Messages are not erased by this step. | இது கோரிக்கையையும் அதன் தணிக்கை வரிகளையும் (அவை ஒருபோதும் நீக்கப்படாது), நோயாளியின் பதிவு எண்ணையும், பொருத்தத்துக்கான சாவி கைரேகைகளையும் வைத்திருக்கும்; பொருத்தம் அழிக்கப்பட்ட பதிவைப் புறக்கணிக்கும். இந்தப் படியில் செய்திகள் அழிக்கப்படாது. | Privacy meaning: "keyed fingerprints" (a hashed match key) is rendered சாவி கைரேகைகள், which reads as biometric fingerprints being kept. It must say that a one-way code used only for matching is kept, and that it ignores an erased record. |
| `portal.activate.consent_required` | The care-delivery notice has not been granted yet, so the account was not activated. | பராமரிப்பு வழங்கல் அறிவிப்பு இன்னும் வழங்கப்படவில்லை; எனவே கணக்கு செயல்படுத்தப்படவில்லை. | Consent: "the care-delivery notice has not been granted" is rendered பராமரிப்பு வழங்கல் அறிவிப்பு ... வழங்கப்படவில்லை. A notice is not "granted"; consent wording needs the hospital's counsel and a native reviewer. |
| `placeholder.privacyNotice` | [PLACEHOLDER: privacy notice. To be supplied by the hospital and approved by its legal counsel.] | [PLACEHOLDER: privacy notice. To be supplied by the hospital and approved by its legal counsel.] | By design this string (and every placeholder.* string) is still ENGLISH in the Tamil interface: privacy notice, privacy promise, grievance contact, FAQ answers, erasure wording, identity checks. A Tamil-speaking patient sees English until the hospital and its counsel supply real Tamil text. This is a launch blocker for the Tamil patient pages, not a translation to fix here. |
| `portal.grievance.body` | Use this for access, correction, erasure, a nominee, or any other request. A person at the hospital reads it. | அணுகல், திருத்தம், அழித்தல், பரிந்துரைக்கப்பட்டவர், அல்லது வேறு எந்தக் கோரிக்கைக்கும் இதைப் பயன்படுத்துங்கள். மருத்துவமனையில் ஒருவர் இதைப் படிப்பார். | "nominee" (a person a patient names to act for them) is rendered பரிந்துரைக்கப்பட்டவர் ("a person who was recommended"). The usual legal word for a nominee differs; this is a patient-rights term, send to the hospital's counsel and a native reviewer. |
| `portal.voice.agree` | I have read the notice above and I agree to this voice session. | மேலே உள்ள அறிவிப்பைப் படித்தேன்; இந்தக் குரல் அமர்வுக்கு ஒப்புக்கொள்கிறேன். | A patient agrees to a voice session with this sentence. "ஒப்புக்கொள்கிறேன்" (I agree) shares its verb with "approve" and "admit"; a reviewer should confirm it reads as consent, in a polite patient register. |
| `portal.consent.withdrawnDone` | Your consent was withdrawn. You can grant it again at any time. | உங்கள் ஒப்புதல் திரும்பப் பெறப்பட்டது. எப்போது வேண்டுமானாலும் மீண்டும் வழங்கலாம். | Tells the patient their consent was withdrawn, at once, with no confirmation step. It must read as done and reversible ("மீண்டும் வழங்கலாம்"). "திரும்பப் பெறப்பட்டது" shares its verb with revoke and cannot-be-undone elsewhere (Part 4). |
| `portal.myData.purpose.care_delivery` | Care delivery | சிகிச்சை வழங்கல் | The name of a consent purpose a patient sees in their own history; "சிகிச்சை வழங்கல்" should match how the notice names it. |
| `portal.myData.purpose.ai_processing` | AI-assisted processing | செயற்கை நுண்ணறிவு உதவியுடன் செயலாக்கம் | "செயற்கை நுண்ணறிவு உதவியுடன் செயலாக்கம்" is long and technical for a patient; a reviewer should check it is understood, and that it matches the notice wording. |
| `portal.myData.purpose.service_improvement` | Service improvement | சேவை மேம்பாடு | A consent purpose name; "சேவை மேம்பாடு" (service improvement) should match the notice, which also says the data is de-identified. |

### 4. Identity: merge, link, revoke and undo

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `identity.approveBody` | This links the two records. It asks for a fresh authenticator code first. | இது இரண்டு பதிவுகளையும் இணைக்கிறது. முதலில் புதிய அங்கீகாரக் குறியீடு கேட்கப்படும். | "merge" of two patient records and "link" use the same word (இணை / இணைப்பு) in identity.approveBody, identity.approved, identity.unmergeBody, identity.flagged and the portal claim strings. Merging two patients is high-consequence; the wording must make clear it is joining two records into one, not a link or connection. |
| `identity.unmergeBody` | This breaks the link between the two records. It asks for a fresh authenticator code first. | இது இரண்டு பதிவுகளுக்கிடையிலான இணைப்பை உடைக்கிறது. முதலில் புதிய அங்கீகாரக் குறியீடு கேட்கப்படும். | "breaks the link" (இணைப்பை உடைக்கிறது): same merge/link ambiguity, and உடை ("break") may read as damage rather than undo. |
| `patients.revoke.title` | Revoke this identity confirmation? | இந்த அடையாள உறுதிப்படுத்தலைத் திரும்பப் பெறவா? | "Revoke / take back" is rendered திரும்பப் பெறு, and "cannot be undone / cannot bring back" is rendered திரும்பப் பெற முடியாது in other strings. The same verb means two different things; a reader could confuse "revoke" with "cannot be reversed". |
| `alerts.class.identity_revoked_suspect` | An identity confirmation was revoked as suspect | அடையாள உறுதிப்படுத்தல் சந்தேகத்தின் பேரில் திரும்பப் பெறப்பட்டது | "An identity confirmation was revoked as suspect" rendered அடையாள உறுதிப்படுத்தல் சந்தேகத்தின் பேரில் திரும்பப் பெறப்பட்டது. திரும்பப் பெறு is the same verb as "cannot be undone" elsewhere, and சந்தேகம் may read as suspicion of the person. Needs a native reviewer. |

### 5. "Acknowledge" versus "approve", and irreversible actions

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `alerts.acknowledged` | The alert was acknowledged. | எச்சரிக்கை ஏற்கப்பட்டது. | "acknowledged" is rendered ஏற்கப்பட்டது ("accepted"); an alert that is only SEEN could be read as accepted or agreed with. Check the verb; the same verb is used in alerts.already and on the alert buttons. |
| `admin.voice.purgeBody` | Deletes expired transcript chunks of this clinic. An unexpired chunk is never deleted. This cannot be undone. | இந்த மருத்துவமனையின் காலாவதியான பதிவுத் துண்டுகளை நீக்குகிறது. காலாவதியாகாத துண்டு ஒருபோதும் நீக்கப்படாது. இதைத் திரும்பப் பெற முடியாது. | Irreversible deletion. The Tamil says it cannot be undone (திரும்பப் பெற முடியாது), which also means "cannot be taken back / withdrawn"; confirm it reads as "cannot be undone". |
| `alerts.acknowledge` | Acknowledge | ஏற்றுக்கொள் | Button "Acknowledge" is rendered ஏற்றுக்கொள் ("accept / agree to"), the same verb as Approve (ஒப்புக்கொள்) in many places. Acknowledging an alert only records that you have seen it. Also in admin.grievances.acknowledge and tasks. Check the verb. |

### 6. Other safety-related strings

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `alerts.already` | This alert was already acknowledged. The list will refresh. | இந்த எச்சரிக்கை ஏற்கனவே ஏற்கப்பட்டது. பட்டியல் புதுப்பிக்கப்படும். | Same verb issue as alerts.acknowledged. |

## Part 2. Meaning, register and term problems (grade B)

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `alerts.confirmBody` | This records that you have seen it, under your name. It can be done once. | இதைப் பார்த்தீர்கள் என்று உங்கள் பெயரில் பதிவாகும். இதை ஒரு முறை மட்டுமே செய்ய முடியும். | Says "records that you saw it"; check it matches the verb used for the button. |
| `admin.stepUp` | This needs a fresh authenticator code. You will be asked for it. | இதற்குப் புதிய அங்கீகார குறியீடு தேவை. அது உங்களிடம் கேட்கப்படும். | "fresh" code is rendered புதிய ("new"). It means a RECENTLY entered code, not a never-used one; a reviewer should pick wording that says "recent". |
| `admin.autonomy.changeBody` | Sets the clinic to shadow or assisted. Switching to autonomous is only done through a proposal that a second person approves. | மருத்துவமனையை நிழல் அல்லது உதவியுடன் முறைக்கு அமைக்கிறது. தன்னாட்சிக்கு மாற்றுவது, இரண்டாம் நபர் ஒப்புக்கொள்ளும் முன்மொழிவு மூலம் மட்டுமே செய்யப்படும். | "shadow" and "assisted" are mode names rendered as நிழல் and உதவியுடன்; system terms, confirm they are understood by clinic staff and used the same way everywhere. |
| `admin.recovery.completeBody` | Resets the account's second factor now. The person sets up a new one at their next sign-in. | கணக்கின் இரண்டாம் காரணியை இப்போது மீட்டமைக்கிறது. அடுத்த உள்நுழைவில் அந்த நபர் புதியதை அமைப்பார். | Safety-relevant: says the second factor is reset NOW. Check the tense and that "reset" (மீட்டமை) is not read as "restore". |
| `backfill.reply.saveBody` | Stores what you typed as a message from the patient, encrypted, in their appointments thread. The red-flag rules read it first. Nothing is sent to the patient. Saving it does not yet enter it into the offer run. | நீங்கள் தட்டச்சு செய்ததை நோயாளியிடமிருந்து வந்த செய்தியாக, குறியாக்கம் செய்து, அவரது சந்திப்புகள் இழையில் சேமிக்கிறது. சிவப்புக் கொடி விதிகள் முதலில் அதைப் படிக்கும். நோயாளிக்கு எதுவும் அனுப்பப்படாது. சேமிப்பது இன்னும் வாய்ப்பு ஓட்டத்தில் அதை உள்ளிடாது. | "offer run" is rendered வாய்ப்பு ஓட்டம் ("opportunity flow"); "offer" and "run" are system terms with no everyday meaning here. Confirm the wording. |
| `care.results.doneWake` | The result was recorded. A waiting care plan will be woken. | முடிவு பதிவானது. காத்திருக்கும் பராமரிப்புத் திட்டம் எழுப்பப்படும். | "A waiting care plan will be woken" rendered literally (எழுப்பப்படும், "will be woken up"). |
| `care.results.note` | Results are append-only. To correct one, record a new result that names the one it replaces. | முடிவுகள் சேர்க்க மட்டுமே முடியும். ஒன்றைத் திருத்த, அது மாற்றும் முடிவைக் குறிப்பிடும் புதிய முடிவைப் பதிவு செய்யுங்கள். | "append-only" rendered சேர்க்க மட்டுமே முடியும் ("can only be added"); the idea that nothing is overwritten should be clear. |
| `claims.approveBody` | Links the person to the confirmed record you choose. It is recorded under your name. | நீங்கள் தேர்ந்தெடுக்கும் உறுதிசெய்யப்பட்ட பதிவுடன் அந்த நபரை இணைக்கிறது. இது உங்கள் பெயரில் பதிவாகும். | "Approve onto the record" rendered with an awkward ... பதிவில் ஒப்புக்கொள்ளுங்கள்; the meaning (link this person to that record) should be checked. |
| `claims.needsPatient` | Choose the confirmed record to approve onto. Nothing was recorded. | ஒப்புக்கொள்ள உறுதிசெய்யப்பட்ட பதிவைத் தேர்ந்தெடுங்கள். எதுவும் பதிவாகவில்லை. | Same phrase as claims.approveBody. |
| `claims.note` | Pending requests from people who registered on the portal to be linked to a record. The list shows only a request id and a time. Decide from what the person told you in person, and approve onto the confirmed record. | ஒரு பதிவுடன் இணைக்கக் கோரி வலைவாசலில் பதிவு செய்தவர்களின் நிலுவைக் கோரிக்கைகள். பட்டியல் கோரிக்கை அடையாளத்தையும் நேரத்தையும் மட்டும் காட்டும். அந்த நபர் நேரில் கூறியதை வைத்து முடிவு செய்து, உறுதிசெய்யப்பட்ட பதிவில் ஒப்புக்கொள்ளுங்கள். | Same phrase; "decide from what the person told you in person" is the identity check, so the meaning has to be exact. |
| `dashboard.metric.safety_net_fail_closed_rate` | Safety net fail-closed rate | பாதுகாப்பு வலை மூடுதல் விகிதம் | "fail-closed" (the system stops safely when it fails) is rendered மூடுதல் ("closing"), which loses the meaning. |
| `erasure.approveConflict` | This cannot be approved now (the patient may be part of a live merge, or the request changed). Nothing was erased. | இதை இப்போது ஒப்புக்கொள்ள முடியாது (நோயாளி நேரடி இணைப்பில் இருக்கலாம், அல்லது கோரிக்கை மாறியிருக்கலாம்). எதுவும் அழிக்கப்படவில்லை. | "live merge" rendered நேரடி இணைப்பு ("direct connection / link"); a merge of two records should not read as a connection or link (also used for linking a portal person to a record). |
| `erasure.requestTitle` | Ask for an erasure | அழிப்புக்குக் கேள் | Register: imperative கேள் is the informal singular ("ask"); staff-facing buttons should use the polite form (கேளுங்கள்). |
| `care.plans.write` | Write a care plan | பராமரிப்புத் திட்டம் எழுது | Register: எழுது is the informal singular imperative; use the polite form. |
| `admin.recovery.includeClosed` | Include completed and cancelled | முடிந்தவை மற்றும் ரத்தானவற்றையும் சேர் | Register: சேர் is the informal singular imperative. |
| `identity.filter` | Show items that are | இந்த நிலையிலுள்ளவற்றைக் காட்டு | Register: காட்டு is the informal singular imperative. |
| `referrals.review.confirmBody` | You are about to record the decision "{{decision}}" for this referral. It is kept under your name. | இந்தப் பரிந்துரைக்கு "{{decision}}" என்ற முடிவை நீங்கள் பதிவு செய்யப் போகிறீர்கள். அது உங்கள் பெயரில் வைக்கப்படும். | The decision word inside the sentence ("{{decision}}", for example Approve) is deliberately left in English (referrals.decision.*), so a Tamil sentence contains an English word. A native reviewer should say how the decision words should read. |
| `scheduling.placeholderConfirm` | [PLACEHOLDER: confirmation wording to be approved by the clinic] | [இடஒதுக்கீடு: உறுதிப்படுத்தும் சொற்களை மருத்துவமனை அங்கீகரிக்க வேண்டும்] | A placeholder marker rendered as [இடஒதுக்கீடு: ...]. இடஒதுக்கீடு means a reservation or quota, not "placeholder". Every other placeholder string stays in English by design; this one should too. |
| `problem.step_up` | A fresh authenticator code is needed to do this. Nothing was changed. | இதைச் செய்ய புதிய அங்கீகாரக் குறியீடு தேவை. எதுவும் மாற்றப்படவில்லை. | "fresh" code rendered புதிய ("new"); see admin.stepUp. |
| `staff.stepUp.help` | This action needs a fresh code from your authenticator app. | இந்தச் செயலுக்கு உங்கள் அங்கீகார செயலியிலிருந்து புதிய குறியீடு தேவை. | "fresh" code rendered புதிய ("new"); see admin.stepUp. |
| `access.note` | Read one patient's messages for a limited time, with a reason code. Every read is recorded, and an administrator who did not open the access reviews it afterwards. There is no free text. | ஒரு காரணக் குறியீட்டுடன், குறிப்பிட்ட நேரத்திற்கு ஒரு நோயாளியின் செய்திகளைப் படிக்கலாம். ஒவ்வொரு படிப்பும் பதிவாகும்; அணுகலைத் திறக்காத ஒரு நிர்வாகி பின்னர் அதை மதிப்பாய்வு செய்வார். இலவச உரை எதுவும் இல்லை. | "There is no free text" (a privacy control: only a reason code, never typed text) is rendered இலவச உரை, which means "text that costs nothing". Meaning error. |
| `access.open` | Open access to a patient's messages | நோயாளியின் செய்திகளுக்கு அணுகலைத் திற | Register: திற is the informal singular imperative (also admin.accounts.invite அழை, admin.autonomy.propose முன்மொழி, backfill.prepare தயார் செய்). |
| `backfill.confirm.send.body` | Holds the slot for the current candidate and records the offer. The system sends nothing to the patient: you deliver the offer yourself from the Offers tab. | தற்போதைய வேட்பாளருக்கு இடத்தை நிறுத்தி வாய்ப்பைப் பதிவு செய்கிறது. கணினி நோயாளிக்கு எதுவும் அனுப்பாது: வாய்ப்புகள் தாவலிலிருந்து நீங்களே வாய்ப்பை வழங்குவீர்கள். | "Holds the slot" rendered இடத்தை நிறுத்தி ("stops/parks the place"); elsewhere a hold is ஒதுக்கீடு. "candidate" (the next waitlisted patient) is வேட்பாளர், the word for an election candidate. Choose everyday wording for a patient. |
| `backfill.reply.saved` | The reply was saved and picked below. The rules' reading: | பதில் சேமிக்கப்பட்டு கீழே தேர்ந்தெடுக்கப்பட்டது. விதிகளின் வாசிப்பு: | "The rules' reading" rendered விதிகளின் வாசிப்பு ("the recital of the rules"); say that it is what the rules found. |
| `notes.addendBody` | Writes a new note that adds to the earlier one. The earlier note is not changed. | முந்தைய குறிப்பில் சேர்க்கும் புதிய குறிப்பை எழுதுகிறது. முந்தைய குறிப்பு மாற்றப்படுவதில்லை. | "addendum" (a new note that adds to an earlier one) is rendered இணைப்புக் குறிப்பு ("attachment/link note"); the same word (இணைப்பு) is used for link, merge and attachment. A clearer word for "additional note" is needed. Same in notes.note. |
| `notes.note` | Notes are never edited. A correction is an addendum: a new note that names the one it adds to. | குறிப்புகள் ஒருபோதும் திருத்தப்படுவதில்லை. திருத்தம் என்பது இணைப்புக் குறிப்பு: அது சேர்க்கும் குறிப்பைக் குறிப்பிடும் புதிய குறிப்பு. | Same "addendum" wording as notes.addendBody. |
| `errorBoundary.home` | Go to the home screen | முகப்புத் திரைக்குச் செல் | Register: செல் / திற / திரும்பு are informal singular imperatives (also pages.backHome, patients.full.open, patients.openTitle). Staff and patient pages should use the polite form throughout. |
| `landing.features.offers` | Receive appointment offers by SMS or email | எஸ்எம்எஸ் அல்லது மின்னஞ்சல் மூலம் சந்திப்பு வாய்ப்புகளைப் பெறுங்கள் | Content, not translation: the English promises offers "by SMS or email"; the project sends no SMS and the console says staff deliver offers by hand. The owner should confirm the promise before the Tamil is polished. |
| `portal.signIn.send` | Send me a code | எனக்குக் குறியீட்டை அனுப்பு | Register on PATIENT pages: எனக்குக் குறியீட்டை அனுப்பு and வேறு எண்ணைப் பயன்படுத்து (portal.signIn.change) are informal singular imperatives; portal.signIn.title uses the archaic -க form (உள்நுழைக, உருவாக்குக); most other patient strings use the polite -ங்கள் form. Pick one polite register for every patient-facing string. |
| `portal.signIn.change` | Use a different number | வேறு எண்ணைப் பயன்படுத்து | Informal singular imperative on a patient page, see portal.signIn.send. |
| `portal.signIn.title` | Sign in or create an account | உள்நுழைக அல்லது கணக்கை உருவாக்குக | Archaic -க imperative (உள்நுழைக ... உருவாக்குக) on a patient page, see portal.signIn.send. |
| `portal.signIn.sent` | A code was sent if the number can receive one. | எண்ணுக்குப் பெற முடிந்தால் ஒரு குறியீடு அனுப்பப்பட்டது. | Privacy-sensitive wording ("A code was sent if the number can receive one": the page must not reveal whether a number is registered). The Tamil is awkward (எண்ணுக்குப் பெற முடிந்தால்); check that it still does not reveal that. |
| `scheduling.createConflict` | That overlaps another slot of the same provider, or was already used for a different request. | இது அதே வழங்குநரின் மற்றொரு நேர இடத்துடன் மேலோங்குகிறது, அல்லது வேறொரு கோரிக்கைக்கு ஏற்கனவே பயன்படுத்தப்பட்டது. | "overlaps another slot" is rendered மேலோங்குகிறது, which means "prevails / rises above", not "overlaps". Meaning error in a scheduling message. |
| `scheduling.zoneNote` | Times are in the clinic time zone: {{zone}} (demo placeholder). | நேரங்கள் மருத்துவமனையின் நேர மண்டலத்தில் உள்ளன: {{zone}} (டெமோ இடஒதுக்கீடு). | "demo placeholder" is rendered டெமோ இடஒதுக்கீடு; இடஒதுக்கீடு means a reservation or quota, not "placeholder". Same in time.zoneNotePlaceholder. |
| `time.zoneNotePlaceholder` | Times are in the clinic time zone: {{zone}} (demo placeholder: the clinic has not set one). | நேரங்கள் மருத்துவமனையின் நேர மண்டலத்தில் உள்ளன: {{zone}} (டெமோ இடஒதுக்கீடு: மருத்துவமனை ஒன்றை அமைக்கவில்லை). | "demo placeholder" rendered டெமோ இடஒதுக்கீடு (reservation); see scheduling.zoneNote. |
| `tasks.ackBody` | You will be recorded as the person who took this task. | இந்தப் பணியை எடுத்தவராக நீங்கள் பதிவு செய்யப்படுவீர்கள். | "acknowledge" (take and be recorded as holding a task) is rendered ஏற்றுக்கொள்; the same verb as "approve". A reader may think acknowledging a task approves something. See alerts.acknowledged. |
| `alerts.class.task_escalated` | Task escalated | பணி உயர்நிலைக்கு அனுப்பப்பட்டது | "escalated" rendered உயர்நிலைக்கு அனுப்பப்பட்டது ("sent to a higher level"); see readiness.statuses.nurse_escalated. |
| `audit.col.resource` | Record | பதிவு | The word பதிவு is used for a patient record, an audit log (audit.title தணிக்கைப் பதிவு), a voice transcript (admin.voice.transcript), a recorded fact and for "register". Readers cannot tell which is meant; use different words. |
| `backfill.reply.confidence` | Confidence | நம்பிக்கை | "Confidence" (how sure the engine is) is rendered நம்பிக்கை (belief, hope, trust). Same in review.confidence if present. |
| `erasure.status.requested` | Open | திறந்தது | "Open" (still waiting) is rendered திறந்தது ("it opened / was opened", past tense). Same in admin.grievances.status.open, alerts.stateOpen, portal.grievance.status.open and others; a status word should say "still open" (for example நிலுவையில்). |
| `patients.via.front_desk` | Front desk | வரவேற்பு மேசை | "Front desk" is வரவேற்பு மேசை here and முன்பணியாளர் in the erasure strings. Use one term. |
| `readiness.col.ends` | Ends | முடிவு | முடிவு means "decision", "result" AND "end" in this interface (readiness.col.ends, scheduling.col.ends, care.results, referrals.col.decision). One word for three meanings in a clinical setting. |
| `shell.clinic` | Clinic | கிளினிக் | "Clinic" is கிளினிக் here but மருத்துவமனை ("hospital") in nearly every other string; the console serves clinics, the landing pages a hospital. Check which word is meant where. |
| `admin.rules.reject` | Reject | நிராகரி | நிராகரி is a plain word for reject; check that it reads as final for a red-flag rule version. |
| `admin.rules.rejectTitle` | Reject this red-flag rule version? | இந்தச் சிவப்புக் கொடி விதிப் பதிப்பை நிராகரிக்கலாமா? | Confirmation question; check the verb with admin.rules.reject. |
| `admin.rules.statuses.approved` | Approved | அங்கீகரிக்கப்பட்டது | அங்கீகரிக்கப்பட்டது versus the button verb ஒப்புக்கொள்: pick one family of words for approve. |
| `admin.rules.statuses.retired` | Retired | நிறுத்தப்பட்டது | "Retired" is rendered நிறுத்தப்பட்டது ("stopped"), the same root as the "stop" and "hold" strings. Check it means taken out of use, not paused. |
| `admin.rules.result.rejected` | Your rejection was recorded. This version is closed. | உங்கள் நிராகரிப்பு பதிவு செய்யப்பட்டது. இந்தப் பதிப்பு மூடப்பட்டது. | Check the closed state is clear. |
| `admin.rules.result.already_decided` | You already decided on this version the other way. Nothing changed. | இந்தப் பதிப்பில் நீங்கள் ஏற்கெனவே எதிர்த் திசையில் முடிவெடுத்துவிட்டீர்கள். எதுவும் மாறவில்லை. | "The other way" rendered எதிர்த் திசையில்: check it means the opposite decision. |
| `admin.rules.result.not_draft` | This version is not a draft any more, so nothing was recorded. | இந்தப் பதிப்பு இப்போது வரைவு அல்ல, எனவே எதுவும் பதிவு செய்யப்படவில்லை. | "Not a draft any more": check the tense and that nothing was saved is clear. |
| `admin.rules.result.origin_mismatch` | This version belongs to a different environment than this clinic, so nothing was recorded. | இந்தப் பதிப்பு இந்த மருத்துவமனையின் சூழலைச் சேர்ந்தது அல்ல, எனவே எதுவும் பதிவு செய்யப்படவில்லை. | "Environment" rendered சூழல்; check it is understood as live, test and so on, not surroundings. |
| `admin.rules.result.empty_rule` | This version has no phrases, patterns or vital-sign criteria, so it cannot be approved. | இந்தப் பதிப்பில் சொற்றொடர்கள், வடிவங்கள் அல்லது உடல்நிலை அளவீட்டு நிபந்தனைகள் எதுவும் இல்லை, எனவே இதை ஒப்புக்கொள்ள முடியாது. | No content, cannot be approved: check wording. |
| `admin.rules.result.rejected_earlier` | A doctor already rejected this version, so it cannot be approved. | ஒரு மருத்துவர் ஏற்கெனவே இந்தப் பதிப்பை நிராகரித்துவிட்டார், எனவே இதை ஒப்புக்கொள்ள முடியாது. | Check it says a doctor already rejected it, so it cannot be approved. |
| `admin.rules.result.platform_rule` | This rule applies to every clinic. Who may approve it has not been decided, so nothing was approved here. | இந்த விதி எல்லா மருத்துவமனைகளுக்கும் பொருந்தும். இதை யார் ஒப்புக்கொள்ளலாம் என்பது இன்னும் முடிவு செய்யப்படவில்லை, எனவே இங்கே எதுவும் அங்கீகரிக்கப்படவில்லை. | "This rule applies to every clinic. Who may approve it has not been decided." Check the meaning of "applies to every clinic". |
| `admin.rules.result.unknown` | The system answered with a result this screen does not know. Nothing is shown as approved. Reload the page to see the current state. | இந்தத் திரைக்குத் தெரியாத ஒரு முடிவை அமைப்பு தெரிவித்தது. எதுவும் அங்கீகரிக்கப்பட்டதாகக் காட்டப்படவில்லை. தற்போதைய நிலையைப் பார்க்க பக்கத்தை மீண்டும் ஏற்றவும். | Fallback: "nothing is shown as approved. Reload". Check clarity. |
| `admin.rules.origins.canary` | Canary | கேனரி | "Canary" is transliterated கேனரி; a test-case word, check whether clinicians would understand it, or keep it in English. |
| `admin.rules.vitals` | Vital-sign criteria | உடல்நிலை அளவீட்டு நிபந்தனைகள் | "Vital-sign criteria" rendered உடல்நிலை அளவீட்டு நிபந்தனைகள்: check the clinical term clinicians use for vital signs. |
| `admin.rules.tokenSets` | Token sets | சொல் தொகுப்புகள் | "Token sets" rendered சொல் தொகுப்புகள் ("word sets"): a technical term; check it is understood as words that must all appear together. |
| `admin.rules.author.body` | A rule has a code and any number of literal phrases and token sets. There is no other kind of criterion here, and no regular expression. Nothing is filled in for you. The version is saved as a draft only. | ஒரு விதிக்கு ஒரு குறியீடும், எத்தனை வேண்டுமானாலும் நேரடிச் சொற்றொடர்களும் சொல் தொகுப்புகளும் உண்டு. இங்கே வேறு வகை நிபந்தனை இல்லை, வழக்கமான கோவை (regular expression) இல்லை. உங்களுக்காக எதுவும் நிரப்பப்படுவதில்லை. இந்தப் பதிப்பு வரைவாக மட்டுமே சேமிக்கப்படும். | Mentions "regular expression" in Tamil with the English words in brackets. Check whether to keep the English term for a doctor. |
| `admin.rules.author.err.phraseChars` | Phrase line {{line}} has a control character or an invisible character. Retype it. | சொற்றொடர் வரி {{line}} இல் கட்டுப்பாட்டு எழுத்து அல்லது கண்ணுக்குத் தெரியாத எழுத்து உள்ளது. மீண்டும் தட்டச்சு செய்யவும். | "Control character or an invisible character": technical, check it is understandable by a doctor and that retyping is the right advice. |
| `admin.rules.author.err.tokenChars` | Token set line {{line}} has a control character or an invisible character. Retype it. | சொல் தொகுப்பு வரி {{line}} இல் கட்டுப்பாட்டு எழுத்து அல்லது கண்ணுக்குத் தெரியாத எழுத்து உள்ளது. மீண்டும் தட்டச்சு செய்யவும். | Same as phraseChars. |
| `admin.rules.author.err.sameWords` | Token set line {{line}} needs at least two different words. | சொல் தொகுப்பு வரி {{line}} இல் குறைந்தது இரண்டு வெவ்வேறு சொற்கள் வேண்டும். | "At least two different words" for a token set: check clarity. |
| `medications.correctBody` | Use this when the entry below was recorded wrongly. It adds a new row that replaces the row below. The old row stays on record, marked as replaced. | கீழே உள்ள பதிவு தவறாகப் பதிவு செய்யப்பட்டிருந்தால் இதைப் பயன்படுத்தவும். இது கீழே உள்ள வரிசையை மாற்றும் புதிய வரிசையைச் சேர்க்கிறது. பழைய வரிசை, மாற்றப்பட்டது எனக் குறிக்கப்பட்டு பதிவில் இருக்கும். | "Replaces the row below; the old row stays, marked replaced": check மாற்றும் (replace) versus "change". |
| `medications.superseded` | Replaced by a later row | பிந்தைய வரிசையால் மாற்றப்பட்டது | "Replaced by a later row" rendered பிந்தைய வரிசையால் மாற்றப்பட்டது: check it does not read as "changed medicine". |
| `medications.verified` | Verified | சரிபார்க்கப்பட்டது | சரிபார்க்கப்பட்டது (verified): check who verified is not implied. |
| `medications.title` | Medications | மருந்துகள் | மருந்துகள் (medications) as a heading may read as a list of what the patient takes now, but it lists statements incl. stopped ones. |
| `medications.note` | The medication statements recorded for this patient, newest first. Nothing is edited or deleted: a correction or a stop is a new row that replaces the old one. Codes are shown exactly as recorded. | இந்த நோயாளிக்காகப் பதிவு செய்யப்பட்ட மருந்து அறிக்கைகள், புதியவை முதலில். எதுவும் திருத்தப்படுவதோ நீக்கப்படுவதோ இல்லை: திருத்தம் அல்லது நிறுத்தம் என்பது பழைய வரிசையை மாற்றும் புதிய வரிசை. குறியீடுகள் பதிவு செய்யப்பட்டபடியே காட்டப்படுகின்றன. | Says nothing is edited or deleted, a correction is a new row. Check it is clear. |
| `medications.recordBody` | This adds a new row to the patient's record. It cannot be edited afterwards; a later change is another new row. | இது நோயாளியின் பதிவில் புதிய வரிசையைச் சேர்க்கிறது. பிறகு இதைத் திருத்த முடியாது; பின்னர் ஏற்படும் மாற்றம் மற்றொரு புதிய வரிசையாகும். | "Cannot be edited afterwards": check it is clear that a later change is a new row. |
| `medications.notSetUp` | This clinic's list of medication codes is not set up yet. Nothing was recorded. | இந்த மருத்துவமனையின் மருந்துக் குறியீடுகளின் பட்டியல் இன்னும் அமைக்கப்படவில்லை. எதுவும் பதிவு செய்யப்படவில்லை. | "The clinic's list of medication codes is not set up. Nothing was recorded.": check that "nothing was recorded" is unmistakable. |
| `medications.invalid` | The code is not in this clinic's list, or the times are not valid. Nothing was recorded. | குறியீடு இந்த மருத்துவமனையின் பட்டியலில் இல்லை, அல்லது நேரங்கள் சரியாக இல்லை. எதுவும் பதிவு செய்யப்படவில்லை. | "Code not in the clinic's list, or times not valid. Nothing was recorded.": check. |
| `patients.revoke.alert.raised` | An alert was sent to the administrators. | நிர்வாகிகளுக்கு எச்சரிக்கை அனுப்பப்பட்டது. | "An alert was sent to the administrators": check நிர்வாகிகள் is who the clinic means. |
| `patients.revoke.alert.not_for_this_reason` | No administrator alert was raised for this reason. | இந்தக் காரணத்திற்கு நிர்வாகி எச்சரிக்கை எழுப்பப்படவில்லை. | Check it says no alert, not that nothing was recorded. |
| `patients.revoke.alert.not_configured` | No administrator alert was raised: the alert settings are not configured yet. | நிர்வாகி எச்சரிக்கை எழுப்பப்படவில்லை: எச்சரிக்கை அமைப்புகள் இன்னும் அமைக்கப்படவில்லை. | Settings not configured; check clarity. |
| `admin.tab.rules` | Red-flag rules | சிவப்புக் கொடி விதிகள் | "Red-flag rules" tab: literal சிவப்புக் கொடி (see the red flag theme); check the term clinicians use. |
| `admin.rules.intro` | The red-flag rule versions of this clinic, with the decisions made on each. Every phrase and pattern is shown exactly as the system holds it. This screen cannot write or change a rule. | இந்த மருத்துவமனையின் சிவப்புக் கொடி விதிப் பதிப்புகளும், ஒவ்வொன்றிலும் எடுக்கப்பட்ட முடிவுகளும். ஒவ்வொரு சொற்றொடரும் வடிவமும் அமைப்பில் உள்ளபடியே காட்டப்படுகின்றன. இந்தத் திரையில் விதியை எழுதவோ மாற்றவோ முடியாது. | "Shown exactly as the system holds it": check it conveys that the screen cannot change a rule. |
| `medications.correctTitle` | Correct this medication entry? | இந்த மருந்துப் பதிவைத் திருத்தலாமா? | Confirmation question for a correction; check it against medications.correct. |
| `medications.stopBody` | This adds a new row with an end time for the medicine below. The old row stays on record, marked as replaced. Nothing else is changed. | இது கீழே உள்ள மருந்துக்கு முடிவு நேரத்துடன் ஒரு புதிய வரிசையைச் சேர்க்கிறது. பழைய வரிசை, மாற்றப்பட்டது எனக் குறிக்கப்பட்டு பதிவில் இருக்கும். வேறு எதுவும் மாறாது. | Adds a row with an end time; the old row stays; nothing else changes. Check clarity. |
| `medications.stopSave` | Record stop | நிறுத்தத்தைப் பதிவு செய் | "Record stop" rendered நிறுத்தத்தைப் பதிவு செய்: check it reads as recording, not ordering. |
| `portal.voice.noAudio` | This screen does not record or play sound yet. The audio connection is not set up. | இந்தத் திரை இன்னும் ஒலியைப் பதிவு செய்யாது, ஒலிக்கவும் செய்யாது. ஒலி இணைப்பு அமைக்கப்படவில்லை. | "ஒலி இணைப்பு அமைக்கப்படவில்லை" (the sound connection is not set up) must not read as a fault of the patient's device. Check the wording. |
| `portal.voice.notYours` | No session was opened. You are not recorded as a patient of this hospital. | எந்த அமர்வும் திறக்கப்படவில்லை. நீங்கள் இந்த மருத்துவமனையின் நோயாளியாகப் பதிவாகவில்லை. | "நோயாளியாகப் பதிவாகவில்லை" (not recorded as a patient) could read as a rejection of the person; check that it is neutral. |
| `portal.voice.unavailable` | Voice is not available at this hospital right now. No session was opened. | இந்த மருத்துவமனையில் குரல் இப்போது கிடைக்கவில்லை. எந்த அமர்வும் திறக்கப்படவில்லை. | Check that "கிடைக்கவில்லை" (not available) reads as a temporary state, and that "எந்த அமர்வும் திறக்கப்படவில்லை" is clear to a patient. |
| `portal.voice.repeat` | This repeated an earlier request. No new session was made. | இது முன்னர் செய்த அதே கோரிக்கையின் மறுமுறை. புதிய அமர்வு உருவாக்கப்படவில்லை. | "மறுமுறை" (repeat) for a repeated request may be unclear to a patient. |
| `portal.voice.intro` | Voice lets you say a reply out loud instead of typing it. It is optional and it stays off until you turn it on. | தட்டச்சு செய்வதற்குப் பதிலாக உங்கள் பதிலை வாய்மொழியாகச் சொல்ல குரல் உதவும். இது விருப்பத்துக்குரியது; நீங்கள் இயக்கும் வரை அணைந்தே இருக்கும். | "வாய்மொழியாக" (by speaking) and "குரல்" (voice) as the feature name; check the word patients use for voice mode. |
| `portal.voice.workflow.slot_offer` | Replying to an appointment offer | சந்திப்பு வாய்ப்புக்குப் பதில் சொல்ல | "சந்திப்பு வாய்ப்பு" for an appointment offer; the console uses another word for appointment in places. |
| `portal.voice.workflow.intake_question` | Answering a question before a visit | வருகைக்கு முன் கேட்கப்படும் கேள்விக்குப் பதில் சொல்ல | Check that "கேள்விக்குப் பதில் சொல்ல" reads as answering a clinic question, not asking one. |
| `portal.voice.workflow.readiness_item` | Answering an item on a visit checklist | வருகைக்கான தயார்நிலைப் பட்டியலில் ஒரு உருப்படிக்குப் பதில் சொல்ல | "தயார்நிலைப் பட்டியல்" (readiness list) is a coined term; check what clinics call a pre-visit checklist. |
| `admin.rules.withdrawTitle` | Withdraw this draft red-flag rule version? | இந்தச் சிவப்புக் கொடி விதி வரைவுப் பதிப்பைத் திரும்பப் பெறவா? | Check "திரும்பப் பெறவா" for a draft (a withdrawal, not a deletion). |
| `admin.rules.retire` | Retire this version | இந்தப் பதிப்பை நிறுத்து | Button label "நிறுத்து" (stop) for retire; check the word clinicians use for taking a rule out of use. |
| `admin.rules.withdraw` | Withdraw this draft | இந்த வரைவைத் திரும்பப் பெறு | Button label "திரும்பப் பெறு" for withdraw a draft; same verb as revoke. |
| `admin.rules.youRetired` | You have recorded your retire decision on this version. | இந்தப் பதிப்பில் உங்கள் நிறுத்த முடிவைப் பதிவு செய்துவிட்டீர்கள். | Check it says the doctor has recorded their own decision only. |
| `admin.rules.youWrote` | You wrote this version. A different doctor has to approve or reject it. | இந்தப் பதிப்பை நீங்கள் எழுதினீர்கள். வேறொரு மருத்துவர்தான் இதை ஒப்புக்கொள்ளவோ நிராகரிக்கவோ வேண்டும். | Says the author cannot approve or reject; check "வேறொரு மருத்துவர்தான்" (only a different doctor) is not read as a request. |
| `admin.rules.decisionWords.retire` | Retired it | நிறுத்தினார் | "நிறுத்தினார்" (he/she stopped it) as the decision word in a table of who decided; the honorific form is neutral, check it matches "ஒப்புக்கொண்டார்" and "நிராகரித்தார்". |
| `staff.activate.confirmBody` | This activates your own account. Nobody else, an administrator included, can do it for you. | இது உங்கள் சொந்தக் கணக்கைச் செயல்படுத்தும். வேறு யாரும், நிர்வாகி உட்பட, உங்களுக்காக இதைச் செய்ய முடியாது. | "நிர்வாகி உட்பட" (an administrator included) states that nobody else can activate the account; check it reads as a fact, not a threat. |
| `staff.activate.body` | If you were invited to the staff console and have not activated your account yet, you can do it here. It needs the authenticator code you signed in with. | பணியாளர் கன்சோலுக்கு உங்களுக்கு அழைப்பு வந்து, இன்னும் கணக்கைச் செயல்படுத்தவில்லை என்றால், இங்கே செய்யலாம். நீங்கள் உள்நுழைந்த அங்கீகார குறியீடு இதற்குத் தேவை. | Long sentence; check the invited-staff wording and that "அங்கீகார குறியீடு" is the word staff use for the authenticator code. |

## Part 3. Minor wording and technical labels (grade C)

| Key | English | Tamil (AI-prepared) | Why a native speaker is needed |
|---|---|---|---|
| `admin.autonomy.result.approved` | Approved. The proposal is resolved. | ஒப்புக்கொள்ளப்பட்டது. முன்மொழிவு தீர்க்கப்பட்டது. | Approve rendered with ஒப்புக்கொள் ("agree/admit"); formal registers usually use அங்கீகரி or ஒப்புதல். Keep one choice everywhere. |
| `admin.accounts.done.revoke` | The role was revoked. | பங்கு திரும்பப் பெறப்பட்டது. | "role" rendered பங்கு, which can also mean "share"; check whether பணி or பொறுப்பு is clearer for clinic staff. Used in many strings. |
| `dashboard.metric.escalation_rate_and_accuracy` | Escalation rate and accuracy | உயர்நிலை அனுப்புதல் விகிதமும் துல்லியமும் | "escalation" rendered உயர்நிலை அனுப்புதல் ("sending to a higher level"); used in several dashboard and alert strings. |
| `erasure.approveForbidden` | You cannot approve this request (you may be the person who asked, or not front desk of this clinic). Nothing was erased. | இந்தக் கோரிக்கையை நீங்கள் ஒப்புக்கொள்ள முடியாது (நீங்களே கேட்டவராக இருக்கலாம், அல்லது இந்த மருத்துவமனையின் முன்பணியாளர் அல்ல). எதுவும் அழிக்கப்படவில்லை. | "front desk" rendered முன்பணியாளர், an unusual compound; check it is the word the clinic uses. |
| `identity.emptyDuplicates` | There are no possible duplicates to show. | காட்டுவதற்குச் சாத்தியமான நகல்கள் இல்லை. | "possible duplicates" rendered சாத்தியமான நகல்கள் ("possible copies"); a native speaker should choose the usual wording for duplicate patient records. |
| `patients.confirmBody` | This records that you confirmed this patient's identity. It is kept under your name. | இந்த நோயாளரின் அடையாளத்தை நீங்கள் உறுதிப்படுத்தியதை இது பதிவு செய்கிறது. அது உங்கள் பெயரில் வைக்கப்படும். | Spelling: நோயாளர் here, நோயாளி elsewhere. Pick one spelling. |
| `referrals.run.body` | This queues the Front Door run for this document. It does not message the patient. Any red flag it finds arrives as a task for a person. | இந்த ஆவணத்துக்கான Front Door ஓட்டத்தை வரிசையில் சேர்க்கிறது. நோயாளிக்கு செய்தி அனுப்பாது. அது கண்டறியும் சிவப்புக் கொடி ஒரு நபருக்கான பணியாக வரும். | "Front Door" is left in English as a system name; check that is intended. |
| `admin.cost.term.failure` | Failure tax per month | மாதத்திற்கு தோல்வி வரி | "Failure tax" rendered literally as தோல்வி வரி (a tax on failure). |
| `backfill.noWaiting` | Nobody on the waitlist matches this slot. | இந்த இடத்திற்குப் பொருந்தும் யாரும் காத்திருப்புப் பட்டியலில் இல்லை. | "slot" is இடம் here and நேர இடம் elsewhere. |
| `care.gaps.dateRefused` | That date is refused: it would move backwards or fall in the future. Nothing was recorded. | அந்தத் தேதி மறுக்கப்பட்டது: அது பின்னோக்கி நகரும் அல்லது எதிர்காலத்தில் விழும். எதுவும் பதிவாகவில்லை. | "fall in the future" rendered எதிர்காலத்தில் விழும் ("fall into the future"); awkward. |
| `dashboard.metric.card_recall_at_3` | Card recall at 3 | 3 இல் அட்டை மீட்பு | "Card" (a protocol card) is rendered அட்டை (a physical card) and "recall" மீட்பு; "cost tail" and "golden set" are literal. Admin-only metric names. |
| `referrals.run.queued` | The run was queued. Job: | ஓட்டம் வரிசையில் சேர்க்கப்பட்டது. பணி: | "Job" (a queued job) is rendered பணி, the same word used for "task" (a review task). Two different things share one word. |
| `scheduling.holdDone` | The slot is held. | இடம் நிறுத்தி வைக்கப்பட்டது. | Three different wordings for a slot "hold": ஒதுக்கீடு (the holds list), நிறுத்தி வைக்கப்பட்டது (this string), வைக்கப்படும் (scheduling.holdBody). நிறுத்து also means "stop". Use one. |
| `tasks.closed` | This task is closed. | இந்தப் பணி தீர்க்கப்பட்டது. | "closed" and "resolved" are both rendered தீர்க்கப்பட்டது ("solved"); the console distinguishes closing a task from resolving it in other strings. |
| `backfill.hold` | Hold | நிறுத்தம் | "Hold" rendered நிறுத்தம் ("a halt"). |
| `admin.voice.workflows.intake_question` | Intake question | உள்வாங்கல் கேள்வி | "intake" rendered உள்வாங்கல் ("absorbing"); the usual clinic wording is different. |
| `dashboard.metric.context_recall` | Context recall | சூழல் மீட்பு | Machine-learning metric names (context recall, grounding rate, guardrail trigger accuracy, model dependency health, loop failure rate, engine agreement, tool call accuracy, cache hit rate and others in dashboard.metric.*) are translated word for word; admin-only, a technical reviewer should confirm or leave in English. |
| `nav.patients` | Patients | நோயாளர்கள் | Spelling: நோயாளர்கள் / நோயாளர் here and in many titles, நோயாளி elsewhere. Choose one. |
| `review.candidateCount` | Candidates | வேட்பாளர்கள் | "Candidates" are protocol-card candidates here and waitlisted patients elsewhere (backfill.candidate); both are வேட்பாளர். |
| `scheduling.col.heldUntil` | Held until | வரை நிறுத்தம் | "Held until" rendered வரை நிறுத்தம் ("until halt"); awkward. |
| `tasks.priority.standard` | Standard | வழக்கமான | "Standard" priority rendered வழக்கமான ("usual"); check it reads as a priority level next to உடனடி (immediate). |
| `upload.scan.clean` | Clean | சுத்தம் | "Clean" (a file scan found nothing) rendered சுத்தம் ("cleanliness"). |
| `admin.rules.origins.eval` | Evaluation | மதிப்பீடு | மதிப்பீடு (evaluation): check it is a test environment and not "assessment of a person". |
| `admin.rules.phrases` | Phrases | சொற்றொடர்கள் | சொற்றொடர்கள் (phrases): check it is clear these are literal text matched in messages. |
| `admin.rules.patterns` | Patterns | வடிவங்கள் | வடிவங்கள் (patterns): a technical label for older rules. |
| `admin.rules.author.setsHint` | Each line is one set of {{min}} to {{max}} words with at least two different words, each word at most {{token}} characters, at most {{sets}} sets. | ஒவ்வொரு வரியும் {{min}} முதல் {{max}} சொற்கள் கொண்ட ஒரு தொகுப்பு; குறைந்தது இரண்டு வெவ்வேறு சொற்கள்; ஒவ்வொரு சொல்லும் அதிகபட்சம் {{token}} எழுத்துகள்; அதிகபட்சம் {{sets}} தொகுப்புகள். | Numbers and limits for token sets; check number words. |
| `medications.conflict` | That row was already replaced, or this request clashes with an earlier one. Reload and try again. | அந்த வரிசை ஏற்கெனவே மாற்றப்பட்டுவிட்டது, அல்லது இந்தக் கோரிக்கை முந்தையதுடன் முரண்படுகிறது. மீண்டும் ஏற்றி முயலவும். | Technical conflict message. |
| `medications.codeHint` | A code from this clinic's own list. Letters, numbers, dots, dashes and underscores only. | இந்த மருத்துவமனையின் சொந்தப் பட்டியலில் உள்ள குறியீடு. எழுத்துகள், எண்கள், புள்ளிகள், கோடுகள், அடிக்கோடுகள் மட்டும். | Allowed characters for a code. |
| `roles.front_desk_supervisor` | Front desk supervisor | வரவேற்பு மேசை மேற்பார்வையாளர் | வரவேற்பு மேசை மேற்பார்வையாளர்: consistent with வரவேற்பு மேசை but the project also uses முன்பணியாளர் for front desk. |
| `medications.stoppedAt` | Stopped at | நிறுத்தப்பட்ட நேரம் | நிறுத்தப்பட்ட நேரம் (stopped at). |
| `medications.stopDone` | The stop was recorded. | நிறுத்தம் பதிவு செய்யப்பட்டது. | நிறுத்தம் பதிவு செய்யப்பட்டது. |
| `portal.voice.invalid` | The session could not be opened as asked. No session was opened. | கேட்டபடி அமர்வைத் திறக்க முடியவில்லை. எந்த அமர்வும் திறக்கப்படவில்லை. | Plain error line; check register. |
| `portal.voice.notSetUp` | Voice sessions are not set up yet. Nothing can be opened. | குரல் அமர்வுகள் இன்னும் அமைக்கப்படவில்லை. எதையும் திறக்க முடியாது. | Plain state line. |
| `portal.voice.confirmBody` | This changes one setting on your account. You can change it back at any time. | இது உங்கள் கணக்கின் ஓர் அமைப்பை மாற்றும். எப்போது வேண்டுமானாலும் மீண்டும் மாற்றலாம். | Check the register. |
| `portal.consent.regrant` | Grant again | மீண்டும் வழங்கு | "மீண்டும் வழங்கு" (give again) is a button label. |
| `portal.myData.history` | Your consent history | உங்கள் ஒப்புதல் வரலாறு | Table heading. |
| `portal.myData.historyNone` | There are no consent entries in the file. | கோப்பில் ஒப்புதல் பதிவுகள் எதுவும் இல்லை. | Empty-state line. |
| `portal.myData.col.purpose` | Purpose | நோக்கம் | Column heading. |
| `portal.myData.col.decision` | Decision | முடிவு | Column heading; முடிவு is the one word for decision, result and end (Part 4). |
| `portal.myData.col.version` | Notice version | அறிவிப்புப் பதிப்பு | Column heading. |
| `portal.myData.col.when` | When | எப்போது | Column heading. |
| `staff.activate.notDone` | Your account was not activated. Contact your administrator. | உங்கள் கணக்கு செயல்படுத்தப்படவில்லை. உங்கள் நிர்வாகியைத் தொடர்பு கொள்ளுங்கள். | Plain state line. |
| `staff.activate.refused` | This account cannot be activated here. Contact your administrator. | இந்தக் கணக்கை இங்கே செயல்படுத்த முடியாது. உங்கள் நிர்வாகியைத் தொடர்பு கொள்ளுங்கள். | Plain state line. |
| `staff.activate.button` | Activate my account | என் கணக்கைச் செயல்படுத்து | Button label. |
| `staff.activate.title` | Activate your account | உங்கள் கணக்கைச் செயல்படுத்துங்கள் | Heading. |
| `staff.activate.confirmTitle` | Activate your account? | உங்கள் கணக்கைச் செயல்படுத்தவா? | Dialog title. |
| `staff.activate.done` | Your account is activated. | உங்கள் கணக்கு செயல்படுத்தப்பட்டது. | Plain state line. |

## Part 4. Terms used inconsistently

| English term | Tamil used (number of strings, approximate: substring match) | Problem |
|---|---|---|
| approve | ஒப்புக்கொள் (verb), ஒப்புதல் (noun) (62) | The verb also means "admit/agree"; some places use ஒப்புதல் அளி. |
| acknowledge | ஏற்றுக்கொள் ("accept") (15) | Same verb as approve; the action only records that you have seen something. |
| record / log / transcript / register | பதிவு everywhere (132) | One word for a patient record, the audit log, a voice transcript, "recorded" and "register". |
| result / decision / end | முடிவு everywhere (66) | One word for a clinical result, a person's decision and an end time. |
| role | பங்கு (14) | Also means "share"; பணி is used for "task". |
| merge / link / connect / attach | இணை / இணைப்பு (51) | Merging two patient records, linking a portal account, attaching a note: one word. |
| red flag | சிவப்புக் கொடி (literal) (21) | Check the wording clinicians use. |
| hold (a slot or a referral) | நிறுத்து / நிறுத்தம் (stop) and ஒதுக்கீடு (allocation) (52) | Two or three words for one idea. |
| retire (a rule version) and withdraw (a draft) | நிறுத்து (retire) and திரும்பப் பெறு (withdraw) (63) | The rule screens need two different words; "withdraw" shares its Tamil with "revoke" and "cannot be undone". |
| task / job | பணி (67) | A review task and a queued job share the word. |
| approved (a status) and approve (a button) | அங்கீகரிக்கப்பட்டது (status) and ஒப்புக்கொள் (button) (14) | Two word families for one idea in the red-flag rule screens. |
| patient | நோயாளி and நோயாளர் (74) | Two spellings. |
| clinic / hospital | மருத்துவமனை (most strings), கிளினிக் (1) (55) | Check which is meant where. |
| front desk | வரவேற்பு மேசை and முன்பணியாளர் (8) | Two renderings. |
| confidence | நம்பிக்கை (2) | Means belief or hope; the strings mean how sure the system is. |
| sample (a sampled decision) | மாதிரி (5) | Reads as a laboratory specimen. |
| revoke / take back, and "cannot be undone" | திரும்பப் பெறு for both (21) | The same verb for "revoke" and "cannot be reversed". |
| grant / withdraw (a consent) | வழங்கு (grant) and திரும்பப் பெறு (withdraw) (23) | Consent withdrawal is one click in the portal, so the word must be unmistakable. |
| fresh (code) | புதிய ("new") (27) | Means a recently entered code, not a new one. |
| screen (the rules read text) | பரிசோதனை (6) | The word for a medical examination or test. |
| escalate | உயர்நிலைக்கு அனுப்பு ("send to a higher level") (5) | Loses the sense of raising urgency. |
| authenticator | அங்கீகார செயலி (app) / அங்கீகார குறியீடு (code) (16) | Consistent; check it is the word staff use. |

## Part 5. Register

Staff-console buttons and menu labels use the bare singular imperative (for example காட்டு, சேர், திற, அனுப்பு), which is common for interface labels. Sentences use the polite -ங்கள் form (for example தேர்ந்தெடுங்கள்). Patient-facing pages mix three forms: -ங்கள் (உருவாக்குங்கள்), the archaic -க (உள்நுழைக, உருவாக்குக) and the bare imperative (எனக்குக் குறியீட்டை அனுப்பு, வேறு எண்ணைப் பயன்படுத்து). A native reviewer should choose one polite register for every patient-facing string. No informal pronouns (நீ, உன்) were found.

## Part 6. Left in English on purpose (31 strings)

These are the same in English and Tamil by design, and a Tamil-speaking patient sees English for them until real Tamil text is supplied. Every `placeholder.*` string is hospital or counsel content (privacy notice, privacy promise, grievance contact, FAQ answers, erasure wording, identity checks); they are not translation work. The others are the clinical task kinds (`tasks.kind.red_flag`, `tasks.kind.screening_unassessable`, `tasks.kind.triage_proposal`), the referral decision words (`referrals.decision.*`), the identity-revoke reason words (`patients.revoke.reasons.*`) and the environment and language labels.

- `env.live`
- `env.test`
- `languages.en`
- `languages.ta`
- `patients.revoke.reasons.mistaken_click`
- `patients.revoke.reasons.planted_or_suspect`
- `patients.revoke.reasons.wrong_person`
- `placeholder.about`
- `placeholder.address`
- `placeholder.departments`
- `placeholder.erasureWording`
- `placeholder.faqGrievance`
- `placeholder.faqPrivacy`
- `placeholder.fullRecordPending`
- `placeholder.grievance`
- `placeholder.hours`
- `placeholder.identityChecks`
- `placeholder.phone`
- `placeholder.privacyNotice`
- `placeholder.privacyPromise`
- `placeholder.reasonCodes`
- `placeholder.registerOutcomes`
- `placeholder.reviewReasons`
- `placeholder.revokeReasons`
- `placeholder.taskClose`
- `referrals.decision.approve`
- `referrals.decision.edit`
- `referrals.decision.reject`
- `tasks.kind.red_flag`
- `tasks.kind.screening_unassessable`
- `tasks.kind.triage_proposal`

## Part 7. All 1614 strings

Fill in the last column. Write **OK**, or write the full corrected Tamil (keep every {{placeholder}} exactly as it is). Leave a row empty if you have not looked at it.

| Key | English | Tamil (AI-prepared) | AI second pass | Reviewer: write OK, or the corrected Tamil |
|---|---|---|---|---|
| `access.appropriate` | Appropriate | பொருத்தமானது | no issue found | |
| `access.chooseReason` | Choose a reason | ஒரு காரணத்தைத் தேர்ந்தெடுங்கள் | no issue found | |
| `access.close` | Close | மூடு | no issue found | |
| `access.col.action` | Action | செயல் | no issue found | |
| `access.col.expires` | Expires | காலாவதி | no issue found | |
| `access.col.grant` | Grant | அனுமதி | no issue found | |
| `access.col.opened` | Opened | திறந்தது | no issue found | |
| `access.col.openedBy` | Opened by | திறந்தவர் | no issue found | |
| `access.col.outcome` | Review | மதிப்பாய்வு | no issue found | |
| `access.col.patient` | Patient | நோயாளி | no issue found | |
| `access.col.reason` | Reason | காரணம் | no issue found | |
| `access.erased` | Erased | அழிக்கப்பட்டது | no issue found | |
| `access.inappropriate` | Inappropriate | பொருத்தமற்றது | no issue found | |
| `access.keysMissing` | Encryption keys are not configured, so the messages cannot be read. | குறியாக்க விசைகள் அமைக்கப்படவில்லை; எனவே செய்திகளைப் படிக்க முடியாது. | no issue found | |
| `access.messages` | Messages | செய்திகள் | no issue found | |
| `access.mineEmpty` | You have no open access right now. | இப்போது உங்களிடம் திறந்த அணுகல் இல்லை. | no issue found | |
| `access.mineTitle` | Your open access | உங்கள் திறந்த அணுகல் | no issue found | |
| `access.noGrants` | There are no grants to review. | மதிப்பாய்வு செய்ய அனுமதிகள் இல்லை. | no issue found | |
| `access.noMessages` | There are no messages. | செய்திகள் இல்லை. | no issue found | |
| `access.notConfigured` | Break-glass access is not set up yet (the window length is not configured). Nothing was opened. | அவசரகால அணுகல் இன்னும் அமைக்கப்படவில்லை (சாளர நீளம் அமைக்கப்படவில்லை). எதுவும் திறக்கப்படவில்லை. | no issue found | |
| `access.note` | Read one patient's messages for a limited time, with a reason code. Every read is recorded, and an administrator who did not open the access reviews it afterwards. There is no free text. | ஒரு காரணக் குறியீட்டுடன், குறிப்பிட்ட நேரத்திற்கு ஒரு நோயாளியின் செய்திகளைப் படிக்கலாம். ஒவ்வொரு படிப்பும் பதிவாகும்; அணுகலைத் திறக்காத ஒரு நிர்வாகி பின்னர் அதை மதிப்பாய்வு செய்வார். இலவச உரை எதுவும் இல்லை. | B: "There is no free text" (a privacy control: only a reason code, never typed text) is rendered இலவச உரை, which means "text that costs nothing". Meaning error. | |
| `access.open` | Open access to a patient's messages | நோயாளியின் செய்திகளுக்கு அணுகலைத் திற | B: Register: திற is the informal singular imperative (also admin.accounts.invite அழை, admin.autonomy.propose முன்மொழி, backfill.prepare தயார் செய்). | |
| `access.openBody` | Opens a time-limited grant for ONE patient. Every read is recorded under your name and reviewed afterwards. | ஒரே ஒரு நோயாளிக்கான நேர வரம்புள்ள அனுமதியைத் திறக்கிறது. ஒவ்வொரு படிப்பும் உங்கள் பெயரில் பதிவாகி, பின்னர் மதிப்பாய்வு செய்யப்படும். | no issue found | |
| `access.openConfirm` | Open access | அணுகலைத் திற | no issue found | |
| `access.openTitle` | Open access | அணுகலைத் திற | no issue found | |
| `access.opened` | Access is open until | அணுகல் திறந்திருக்கும் நேரம்: | no issue found | |
| `access.outcome.appropriate` | Appropriate | பொருத்தமானது | no issue found | |
| `access.outcome.inappropriate` | Inappropriate | பொருத்தமற்றது | no issue found | |
| `access.patientNotFound` | The patient was not found. Nothing was opened. | நோயாளி கிடைக்கவில்லை. எதுவும் திறக்கப்படவில்லை. | no issue found | |
| `access.read` | Read the messages | செய்திகளைப் படி | no issue found | |
| `access.reasonCode` | Reason code | காரணக் குறியீடு | no issue found | |
| `access.reasonHint` | A code from the clinic's list (lowercase letters, digits and underscores). The backend refuses a code that is not on the list. | மருத்துவமனையின் பட்டியலிலிருந்து ஒரு குறியீடு (சிற்றெழுத்துகள், எண்கள், அடிக்கோடுகள்). பட்டியலில் இல்லாத குறியீட்டை பின்புலம் மறுக்கும். | no issue found | |
| `access.reasonRefused` | That reason code is not on the clinic's list. Nothing was opened. | அந்தக் காரணக் குறியீடு மருத்துவமனையின் பட்டியலில் இல்லை. எதுவும் திறக்கப்படவில்லை. | no issue found | |
| `access.reviewBody` | Records your review of this access, under your name. It cannot be changed afterwards. | இந்த அணுகலைப் பற்றிய உங்கள் மதிப்பாய்வை உங்கள் பெயரில் பதிவு செய்கிறது. பின்னர் மாற்ற முடியாது. | no issue found | |
| `access.reviewConfirmTitle` | Record this review? | இந்த மதிப்பாய்வைப் பதிவு செய்யவா? | no issue found | |
| `access.reviewNote` | Grants that have not been reviewed. You cannot review an access you opened yourself. | மதிப்பாய்வு செய்யப்படாத அனுமதிகள். நீங்களே திறந்த அணுகலை நீங்கள் மதிப்பாய்வு செய்ய முடியாது. | no issue found | |
| `access.reviewResult.already_reviewed` | This access was already reviewed. | இந்த அணுகல் ஏற்கனவே மதிப்பாய்வு செய்யப்பட்டது. | no issue found | |
| `access.reviewResult.reviewed` | The review was recorded. | மதிப்பாய்வு பதிவானது. | no issue found | |
| `access.reviewResult.same_person` | You opened this access, so someone else must review it. | இந்த அணுகலை நீங்களே திறந்தீர்கள்; எனவே வேறொருவர் மதிப்பாய்வு செய்ய வேண்டும். | no issue found | |
| `access.reviewTitle` | Review afterwards | பின்னர் மதிப்பாய்வு | no issue found | |
| `access.sender.patient` | Patient | நோயாளி | no issue found | |
| `access.sender.staff` | Staff | பணியாளர் | no issue found | |
| `access.sender.system` | System | அமைப்பு | no issue found | |
| `access.title` | Break-glass access | அவசரகால அணுகல் | no issue found | |
| `access.topic.appointments` | Appointments | சந்திப்புகள் | no issue found | |
| `access.topic.care` | Care | பராமரிப்பு | no issue found | |
| `access.topic.readiness` | Readiness | தயார்நிலை | no issue found | |
| `access.topic.safety` | Safety | பாதுகாப்பு | no issue found | |
| `access.unreadable` | Not readable | படிக்க இயலவில்லை | no issue found | |
| `access.unreviewed` | Not reviewed | மதிப்பாய்வு செய்யப்படவில்லை | no issue found | |
| `access.use` | Use | பயன்படுத்து | no issue found | |
| `access.windowClosed` | The access window has closed, or this access is not yours. | அணுகல் சாளரம் மூடப்பட்டது, அல்லது இந்த அணுகல் உங்களுடையது அல்ல. | no issue found | |
| `admin.accounts.cannotChange` | You cannot change this account: it is your own, or you are not an administrator of every clinic where it holds a role. | இந்தக் கணக்கை உங்களால் மாற்ற முடியாது: இது உங்களுடையது, அல்லது இது பங்கு வகிக்கும் ஒவ்வொரு மருத்துவமனையிலும் நீங்கள் நிர்வாகி அல்ல. | no issue found | |
| `admin.accounts.col.account` | Account | கணக்கு | no issue found | |
| `admin.accounts.col.action` | Action | செயல் | no issue found | |
| `admin.accounts.col.roles` | Roles | பங்குகள் | no issue found | |
| `admin.accounts.col.status` | Status | நிலை | no issue found | |
| `admin.accounts.done.grant` | The role was granted. | பங்கு வழங்கப்பட்டது. | no issue found | |
| `admin.accounts.done.invite` | The invitation was created. | அழைப்பு உருவாக்கப்பட்டது. | no issue found | |
| `admin.accounts.done.reinstate` | The account was reinstated. | கணக்கு மீண்டும் செயல்படுத்தப்பட்டது. | no issue found | |
| `admin.accounts.done.revoke` | The role was revoked. | பங்கு திரும்பப் பெறப்பட்டது. | C: "role" rendered பங்கு, which can also mean "share"; check whether பணி or பொறுப்பு is clearer for clinic staff. Used in many strings. | |
| `admin.accounts.done.suspend` | The account was suspended. | கணக்கு இடைநிறுத்தப்பட்டது. | no issue found | |
| `admin.accounts.email` | E-mail address | மின்னஞ்சல் முகவரி | no issue found | |
| `admin.accounts.empty` | There are no staff accounts to show. | காட்ட பணியாளர் கணக்குகள் இல்லை. | no issue found | |
| `admin.accounts.exists` | An account with that e-mail already exists. Grant it a role instead. | அந்த மின்னஞ்சலுடன் ஒரு கணக்கு ஏற்கனவே உள்ளது. அதற்குப் பதிலாக ஒரு பங்கை வழங்குங்கள். | no issue found | |
| `admin.accounts.grant` | Grant a role | பங்கை வழங்கு | no issue found | |
| `admin.accounts.grantBody` | Gives this account the role you choose at this clinic. | இந்த மருத்துவமனையில் நீங்கள் தேர்ந்தெடுக்கும் பங்கை இந்தக் கணக்கிற்கு வழங்குகிறது. | no issue found | |
| `admin.accounts.grantTitle` | Grant a role? | பங்கை வழங்கவா? | no issue found | |
| `admin.accounts.invite` | Invite a staff member | பணியாளரை அழை | no issue found | |
| `admin.accounts.inviteBody` | Creates an account with the one role you choose. They activate it themselves. | நீங்கள் தேர்ந்தெடுக்கும் ஒரே பங்குடன் ஒரு கணக்கை உருவாக்குகிறது. அவர்களே அதைச் செயல்படுத்துவார்கள். | no issue found | |
| `admin.accounts.inviteTitle` | Invite a staff member? | பணியாளரை அழைக்கவா? | no issue found | |
| `admin.accounts.lastAdmin` | That would leave the clinic without an active administrator. Nothing was changed. | அது மருத்துவமனையை செயலில் உள்ள நிர்வாகி இல்லாமல் விட்டுவிடும். எதுவும் மாற்றப்படவில்லை. | no issue found | |
| `admin.accounts.notConfigured` | Inviting is not set up yet (no sign-in administration is configured). Nothing was sent. | அழைப்பு இன்னும் அமைக்கப்படவில்லை (உள்நுழைவு நிர்வாகம் அமைக்கப்படவில்லை). எதுவும் அனுப்பப்படவில்லை. | no issue found | |
| `admin.accounts.note` | Accounts that hold or held a role at this clinic. Only ids, a status and role words are shown; no e-mail is stored here. | இந்த மருத்துவமனையில் பங்கு வகிக்கும் அல்லது வகித்த கணக்குகள். அடையாளங்கள், நிலை மற்றும் பங்குச் சொற்கள் மட்டுமே காட்டப்படுகின்றன; மின்னஞ்சல் இங்கு சேமிக்கப்படுவதில்லை. | no issue found | |
| `admin.accounts.reinstate` | Reinstate | மீண்டும் செயல்படுத்து | no issue found | |
| `admin.accounts.reinstateBody` | The suspended account can sign in again. | இடைநிறுத்தப்பட்ட கணக்கு மீண்டும் உள்நுழையலாம். | no issue found | |
| `admin.accounts.reinstateTitle` | Reinstate this account? | இந்தக் கணக்கை மீண்டும் செயல்படுத்தவா? | no issue found | |
| `admin.accounts.revoke` | Revoke a role | பங்கைத் திரும்பப் பெறு | no issue found | |
| `admin.accounts.revokeBody` | Removes the role you choose from this account at this clinic. | இந்த மருத்துவமனையில் நீங்கள் தேர்ந்தெடுக்கும் பங்கை இந்தக் கணக்கிலிருந்து நீக்குகிறது. | no issue found | |
| `admin.accounts.revokeTitle` | Revoke a role? | பங்கைத் திரும்பப் பெறவா? | no issue found | |
| `admin.accounts.role` | Role | பங்கு | no issue found | |
| `admin.accounts.status.active` | Active | செயலில் | no issue found | |
| `admin.accounts.status.closed` | Closed | மூடப்பட்டது | no issue found | |
| `admin.accounts.status.invited` | Invited | அழைக்கப்பட்டது | no issue found | |
| `admin.accounts.status.suspended` | Suspended | இடைநிறுத்தப்பட்டது | no issue found | |
| `admin.accounts.suspend` | Suspend | இடைநிறுத்து | no issue found | |
| `admin.accounts.suspendBody` | The account can no longer sign in to this system until it is reinstated. | மீண்டும் செயல்படுத்தப்படும் வரை இந்தக் கணக்கு இந்த அமைப்பில் உள்நுழைய முடியாது. | no issue found | |
| `admin.accounts.suspendTitle` | Suspend this account? | இந்தக் கணக்கை இடைநிறுத்தவா? | no issue found | |
| `admin.autonomy.alreadyPending` | A proposal is already pending. | ஒரு முன்மொழிவு ஏற்கனவே நிலுவையில் உள்ளது. | no issue found | |
| `admin.autonomy.approve` | Approve | ஒப்புக்கொள் | no issue found | |
| `admin.autonomy.approveTitle` | Approve this proposal? | இந்த முன்மொழிவை ஒப்புக்கொள்ளவா? | no issue found | |
| `admin.autonomy.change` | Change the mode | முறையை மாற்று | no issue found | |
| `admin.autonomy.changeBody` | Sets the clinic to shadow or assisted. Switching to autonomous is only done through a proposal that a second person approves. | மருத்துவமனையை நிழல் அல்லது உதவியுடன் முறைக்கு அமைக்கிறது. தன்னாட்சிக்கு மாற்றுவது, இரண்டாம் நபர் ஒப்புக்கொள்ளும் முன்மொழிவு மூலம் மட்டுமே செய்யப்படும். | B: "shadow" and "assisted" are mode names rendered as நிழல் and உதவியுடன்; system terms, confirm they are understood by clinic staff and used the same way everywhere. | |
| `admin.autonomy.col.action` | Action | செயல் | no issue found | |
| `admin.autonomy.col.by` | By | செய்தவர் | no issue found | |
| `admin.autonomy.col.created` | Created | உருவாக்கப்பட்டது | no issue found | |
| `admin.autonomy.col.decidedBy` | Decided by | முடிவு செய்தவர் | no issue found | |
| `admin.autonomy.col.mode` | Mode | முறை | no issue found | |
| `admin.autonomy.col.outcome` | Outcome | முடிவு | no issue found | |
| `admin.autonomy.col.proposal` | Proposal | முன்மொழிவு | no issue found | |
| `admin.autonomy.col.role` | As | பங்கு | no issue found | |
| `admin.autonomy.col.what` | What | என்ன | no issue found | |
| `admin.autonomy.col.when` | When | எப்போது | no issue found | |
| `admin.autonomy.decideBody` | Your decision is recorded under your name by the database. | உங்கள் முடிவு தரவுத்தளத்தால் உங்கள் பெயரில் பதிவு செய்யப்படும். | no issue found | |
| `admin.autonomy.historyEmpty` | There is no history yet. | இன்னும் வரலாறு இல்லை. | no issue found | |
| `admin.autonomy.historyNote` | Past mode changes and switch-to-autonomous proposals with their outcomes. Each read is recorded. | கடந்தகால முறை மாற்றங்களும் தன்னாட்சிக்கு மாற்றும் முன்மொழிவுகளும் அவற்றின் முடிவுகளுடன். ஒவ்வொரு படிப்பும் பதிவு செய்யப்படும். | no issue found | |
| `admin.autonomy.historyTitle` | History | வரலாறு | no issue found | |
| `admin.autonomy.kinds.mode_change` | Mode change | முறை மாற்றம் | no issue found | |
| `admin.autonomy.kinds.proposal` | Proposal | முன்மொழிவு | no issue found | |
| `admin.autonomy.mode` | Mode | முறை | no issue found | |
| `admin.autonomy.modeSet` | The mode was set. | முறை அமைக்கப்பட்டது. | no issue found | |
| `admin.autonomy.modes.assisted` | Assisted | உதவியுடன் | no issue found | |
| `admin.autonomy.modes.autonomous` | Autonomous | தன்னாட்சி | no issue found | |
| `admin.autonomy.modes.shadow` | Shadow | நிழல் | no issue found | |
| `admin.autonomy.neverSet` | (never set, so shadow) | (ஒருபோதும் அமைக்கப்படவில்லை; எனவே நிழல்) | no issue found | |
| `admin.autonomy.noPending` | There are no pending proposals. | நிலுவையில் உள்ள முன்மொழிவுகள் இல்லை. | no issue found | |
| `admin.autonomy.notEnabled` | Switching to autonomous is not enabled yet. Nothing was changed. | தன்னாட்சிக்கு மாற்றுவது இன்னும் இயக்கப்படவில்லை. எதுவும் மாற்றப்படவில்லை. | no issue found | |
| `admin.autonomy.outcomes.approved` | Approved | ஒப்புக்கொள்ளப்பட்டது | no issue found | |
| `admin.autonomy.outcomes.expired` | Expired | காலாவதியானது | no issue found | |
| `admin.autonomy.outcomes.pending` | Pending | நிலுவையில் | no issue found | |
| `admin.autonomy.outcomes.rejected` | Rejected | நிராகரிக்கப்பட்டது | no issue found | |
| `admin.autonomy.pending` | Pending proposals | நிலுவையில் உள்ள முன்மொழிவுகள் | no issue found | |
| `admin.autonomy.propose` | Propose switching to autonomous | தன்னாட்சிக்கு மாற்ற முன்மொழி | no issue found | |
| `admin.autonomy.proposeBody` | You propose as {{role}}. A different person who holds the other role must approve it within 7 days. Nothing changes until then. | நீங்கள் {{role}} ஆக முன்மொழிகிறீர்கள். மற்ற பங்கை வகிக்கும் வேறொருவர் 7 நாட்களுக்குள் ஒப்புக்கொள்ள வேண்டும். அதுவரை எதுவும் மாறாது. | no issue found | |
| `admin.autonomy.proposeConfirm` | Propose | முன்மொழி | no issue found | |
| `admin.autonomy.proposed` | The proposal was made. | முன்மொழிவு செய்யப்பட்டது. | no issue found | |
| `admin.autonomy.reject` | Reject | நிராகரி | no issue found | |
| `admin.autonomy.rejectTitle` | Reject this proposal? | இந்த முன்மொழிவை நிராகரிக்கவா? | no issue found | |
| `admin.autonomy.result.approved` | Approved. The proposal is resolved. | ஒப்புக்கொள்ளப்பட்டது. முன்மொழிவு தீர்க்கப்பட்டது. | C: Approve rendered with ஒப்புக்கொள் ("agree/admit"); formal registers usually use அங்கீகரி or ஒப்புதல். Keep one choice everywhere. | |
| `admin.autonomy.result.expired` | This proposal has expired. | இந்த முன்மொழிவு காலாவதியாகிவிட்டது. | no issue found | |
| `admin.autonomy.result.not_pending` | This proposal is no longer pending. | இந்த முன்மொழிவு இனி நிலுவையில் இல்லை. | no issue found | |
| `admin.autonomy.result.rejected` | Rejected. The proposal is closed. | நிராகரிக்கப்பட்டது. முன்மொழிவு மூடப்பட்டது. | no issue found | |
| `admin.autonomy.result.same_person` | The person who proposed it cannot approve it. | முன்மொழிந்தவரே அதை ஒப்புக்கொள்ள முடியாது. | no issue found | |
| `admin.autonomy.result.wrong_role` | Approval has to come from the other role. | ஒப்புதல் மற்ற பங்கிலிருந்து வர வேண்டும். | no issue found | |
| `admin.autonomy.set` | Set mode | முறையை அமை | no issue found | |
| `admin.autonomy.you` | You | நீங்கள் | no issue found | |
| `admin.cost.amplification` | Amplification over the ceiling | உச்ச வரம்பிற்கு மேல் பெருக்கம் | no issue found | |
| `admin.cost.col.actual` | Actual | உண்மையானது | no issue found | |
| `admin.cost.col.shadow` | At scale | அளவில் | no issue found | |
| `admin.cost.col.term` | Term | உறுப்பு | no issue found | |
| `admin.cost.noColumn` | No column | பத்தி இல்லை | no issue found | |
| `admin.cost.notMeasured` | Not measured | அளக்கப்படவில்லை | no issue found | |
| `admin.cost.note` | Monthly cost from the spend ledger and completed jobs, in two columns: actual, and at the scale you plan for. A term that is not configured or not measured stays empty. | செலவுப் பேரேடு மற்றும் முடிந்த பணிகளிலிருந்து மாதாந்திரச் செலவு, இரண்டு பத்திகளில்: உண்மையானது, மற்றும் நீங்கள் திட்டமிடும் அளவில். அமைக்கப்படாத அல்லது அளக்கப்படாத உறுப்பு காலியாகவே இருக்கும். | no issue found | |
| `admin.cost.reasonText.fewerThanSevenDays` | Fewer than seven days of measured traffic | அளக்கப்பட்ட போக்குவரத்து ஏழு நாட்களுக்கும் குறைவு | no issue found | |
| `admin.cost.reasonText.floorNotSet` | The platform floor (P) is not configured | தளத்தின் அடிப்படைச் செலவு (P) அமைக்கப்படவில்லை | no issue found | |
| `admin.cost.reasonText.minutesNotMeasured` | Reviewer minutes are not measured yet | மதிப்பாய்வாளர் நிமிடங்கள் இன்னும் அளக்கப்படவில்லை | no issue found | |
| `admin.cost.reasonText.noCompleted` | No completed workflows | முடிந்த பணிப்பாய்வுகள் இல்லை | no issue found | |
| `admin.cost.reasonText.ratesNotSet` | Reviewer rates are not configured | மதிப்பாய்வாளர் கட்டணங்கள் அமைக்கப்படவில்லை | no issue found | |
| `admin.cost.reasonText.retryNotMeasured` | Retry cost is not measured yet | மறுமுயற்சிச் செலவு இன்னும் அளக்கப்படவில்லை | no issue found | |
| `admin.cost.reasonText.roleNoRate` | A reviewer role has no configured rate | ஒரு மதிப்பாய்வாளர் பங்குக்கு அமைக்கப்பட்ட கட்டணம் இல்லை | no issue found | |
| `admin.cost.reasons` | Reasons | காரணங்கள் | no issue found | |
| `admin.cost.state` | State | நிலை | no issue found | |
| `admin.cost.states.insufficient_data` | Not enough data | போதுமான தரவு இல்லை | no issue found | |
| `admin.cost.states.ok` | OK | சரி | no issue found | |
| `admin.cost.states.unconfigured` | Not configured | அமைக்கப்படவில்லை | no issue found | |
| `admin.cost.term.calls` | Calls per workflow | பணிப்பாய்வுக்கு அழைப்புகள் | no issue found | |
| `admin.cost.term.failure` | Failure tax per month | மாதத்திற்கு தோல்வி வரி | C: "Failure tax" rendered literally as தோல்வி வரி (a tax on failure). | |
| `admin.cost.term.floor` | Platform floor per month | மாதத்திற்கு தள அடிப்படைச் செலவு | no issue found | |
| `admin.cost.term.perCall` | Cost per call | அழைப்புக்கான செலவு | no issue found | |
| `admin.cost.term.total` | Total per month | மாதத்திற்கு மொத்தம் | no issue found | |
| `admin.cost.term.variable` | Variable cost per month | மாதத்திற்கு மாறுபடும் செலவு | no issue found | |
| `admin.cost.term.workflows` | Workflows per day | நாளொன்றுக்கு பணிப்பாய்வுகள் | no issue found | |
| `admin.grievances.acknowledge` | Acknowledge | ஏற்றுக்கொள் | no issue found | |
| `admin.grievances.acknowledgeBody` | Records that you have seen it, under your name. | நீங்கள் இதைப் பார்த்தீர்கள் என்று உங்கள் பெயரில் பதிவு செய்கிறது. | no issue found | |
| `admin.grievances.acknowledgeTitle` | Acknowledge this grievance? | இந்தக் குறையை ஏற்றுக்கொள்ளவா? | no issue found | |
| `admin.grievances.col.action` | Action | செயல் | no issue found | |
| `admin.grievances.col.category` | Category | வகை | no issue found | |
| `admin.grievances.col.grievance` | Grievance | குறை | no issue found | |
| `admin.grievances.col.received` | Received | பெறப்பட்டது | no issue found | |
| `admin.grievances.col.status` | Status | நிலை | no issue found | |
| `admin.grievances.col.text` | Text | உரை | no issue found | |
| `admin.grievances.empty` | There are no grievances. | குறைகள் இல்லை. | no issue found | |
| `admin.grievances.erased` | Erased | அழிக்கப்பட்டது | no issue found | |
| `admin.grievances.note` | Grievances sent by patients. The text is shown as the patient wrote it while it can be read. A resolved grievance is final. | நோயாளிகள் அனுப்பிய குறைகள். படிக்க முடியும் வரை, நோயாளி எழுதியபடியே உரை காட்டப்படும். தீர்க்கப்பட்ட குறை இறுதியானது. | no issue found | |
| `admin.grievances.resolve` | Resolve | தீர் | no issue found | |
| `admin.grievances.resolveBody` | Closes the grievance, under your name. A resolved grievance is final. | உங்கள் பெயரில் குறையை மூடுகிறது. தீர்க்கப்பட்ட குறை இறுதியானது. | no issue found | |
| `admin.grievances.resolveTitle` | Resolve this grievance? | இந்தக் குறையைத் தீர்க்கவா? | no issue found | |
| `admin.grievances.result.invalid_transition` | That change is not allowed from its current status. Nothing was changed. | தற்போதைய நிலையிலிருந்து அந்த மாற்றம் அனுமதிக்கப்படவில்லை. எதுவும் மாற்றப்படவில்லை. | no issue found | |
| `admin.grievances.result.recorded` | Recorded. | பதிவானது. | no issue found | |
| `admin.grievances.result.replay` | That was already recorded. | அது ஏற்கனவே பதிவாகியுள்ளது. | no issue found | |
| `admin.grievances.status.acknowledged` | Acknowledged | ஏற்கப்பட்டது | no issue found | |
| `admin.grievances.status.open` | Open | திறந்தது | no issue found | |
| `admin.grievances.status.resolved` | Resolved | தீர்க்கப்பட்டது | no issue found | |
| `admin.grievances.unreadable` | Not readable | படிக்க இயலவில்லை | no issue found | |
| `admin.notRecorded` | Not recorded | பதிவு செய்யப்படவில்லை | no issue found | |
| `admin.recovery.approve` | Approve | ஒப்புக்கொள் | no issue found | |
| `admin.recovery.approveBody` | Approves the request as the second administrator. The second factor is not reset until it is completed. | இரண்டாம் நிர்வாகியாக கோரிக்கையை ஒப்புக்கொள்கிறது. அது முடிக்கப்படும் வரை இரண்டாம் காரணி மீட்டமைக்கப்படாது. | no issue found | |
| `admin.recovery.approveTitle` | Approve this recovery? | இந்த மீட்பை ஒப்புக்கொள்ளவா? | no issue found | |
| `admin.recovery.cancel` | Cancel | ரத்து செய் | no issue found | |
| `admin.recovery.cancelBody` | Cancels the request. Nothing is reset. | கோரிக்கையை ரத்து செய்கிறது. எதுவும் மீட்டமைக்கப்படாது. | no issue found | |
| `admin.recovery.cancelTitle` | Cancel this recovery? | இந்த மீட்பை ரத்து செய்யவா? | no issue found | |
| `admin.recovery.cannotRequest` | A recovery cannot be requested for this account: it is your own, it is not staff of this clinic, or one is already open. | இந்தக் கணக்குக்கு மீட்பைக் கோர முடியாது: இது உங்களுடையது, இந்த மருத்துவமனையின் பணியாளர் அல்ல, அல்லது ஒன்று ஏற்கனவே திறந்துள்ளது. | no issue found | |
| `admin.recovery.col.account` | Account | கணக்கு | no issue found | |
| `admin.recovery.col.action` | Action | செயல் | no issue found | |
| `admin.recovery.col.by` | Asked by | கேட்டவர் | no issue found | |
| `admin.recovery.col.created` | Created | உருவாக்கப்பட்டது | no issue found | |
| `admin.recovery.col.request` | Request | கோரிக்கை | no issue found | |
| `admin.recovery.col.status` | Status | நிலை | no issue found | |
| `admin.recovery.complete` | Complete | முடி | no issue found | |
| `admin.recovery.completeBody` | Resets the account's second factor now. The person sets up a new one at their next sign-in. | கணக்கின் இரண்டாம் காரணியை இப்போது மீட்டமைக்கிறது. அடுத்த உள்நுழைவில் அந்த நபர் புதியதை அமைப்பார். | B: Safety-relevant: says the second factor is reset NOW. Check the tense and that "reset" (மீட்டமை) is not read as "restore". | |
| `admin.recovery.completeTitle` | Complete this recovery? | இந்த மீட்பை முடிக்கவா? | no issue found | |
| `admin.recovery.empty` | There are no recovery requests. | மீட்புக் கோரிக்கைகள் இல்லை. | no issue found | |
| `admin.recovery.includeClosed` | Include completed and cancelled | முடிந்தவை மற்றும் ரத்தானவற்றையும் சேர் | B: Register: சேர் is the informal singular imperative. | |
| `admin.recovery.notConfigured` | Recovery is not set up yet (no sign-in administration is configured). Nothing was reset. | மீட்பு இன்னும் அமைக்கப்படவில்லை (உள்நுழைவு நிர்வாகம் அமைக்கப்படவில்லை). எதுவும் மீட்டமைக்கப்படவில்லை. | no issue found | |
| `admin.recovery.note` | Resets a staff member's second factor. You cannot ask for your own, and a second administrator, who is neither the requester nor the person being recovered, must approve it. | ஒரு பணியாளரின் இரண்டாம் காரணியை மீட்டமைக்கிறது. உங்களுக்காக நீங்களே கேட்க முடியாது; கோரியவரோ மீட்கப்படுபவரோ அல்லாத இரண்டாம் நிர்வாகி ஒப்புக்கொள்ள வேண்டும். | no issue found | |
| `admin.recovery.refused` | You cannot do this: you may be the requester or the person being recovered, or you are not an administrator of this clinic. | இதை உங்களால் செய்ய முடியாது: நீங்கள் கோரியவராகவோ மீட்கப்படுபவராகவோ இருக்கலாம், அல்லது இந்த மருத்துவமனையின் நிர்வாகி அல்ல. | no issue found | |
| `admin.recovery.request` | Request a recovery | மீட்பைக் கோரு | no issue found | |
| `admin.recovery.requestBody` | Asks for this account's second factor to be reset. Nothing is reset until a second administrator approves and it is completed. | இந்தக் கணக்கின் இரண்டாம் காரணியை மீட்டமைக்கக் கேட்கிறது. இரண்டாம் நிர்வாகி ஒப்புக்கொண்டு அது முடிக்கப்படும் வரை எதுவும் மீட்டமைக்கப்படாது. | no issue found | |
| `admin.recovery.requestConfirm` | Request | கோரு | no issue found | |
| `admin.recovery.requested` | The recovery was requested. | மீட்பு கோரப்பட்டது. | no issue found | |
| `admin.recovery.result.approved` | Approved. It still has to be completed. | ஒப்புக்கொள்ளப்பட்டது. இன்னும் முடிக்கப்பட வேண்டும். | no issue found | |
| `admin.recovery.result.cancelled` | Cancelled. Nothing was reset. | ரத்தானது. எதுவும் மீட்டமைக்கப்படவில்லை. | no issue found | |
| `admin.recovery.result.completed` | Completed. The second factor was reset. | முடிந்தது. இரண்டாம் காரணி மீட்டமைக்கப்பட்டது. | no issue found | |
| `admin.recovery.status.approved` | Approved | ஒப்புக்கொள்ளப்பட்டது | no issue found | |
| `admin.recovery.status.cancelled` | Cancelled | ரத்தானது | no issue found | |
| `admin.recovery.status.completed` | Completed | முடிந்தது | no issue found | |
| `admin.recovery.status.requested` | Requested | கோரப்பட்டது | no issue found | |
| `admin.reviewer.canaryCount` | {{approved}} of {{seen}} | {{seen}} இல் {{approved}} | no issue found | |
| `admin.reviewer.col.approvals` | Approvals | ஒப்புதல்கள் | no issue found | |
| `admin.reviewer.col.canaries` | Test cases approved | ஒப்புக்கொண்ட சோதனை நிகழ்வுகள் | no issue found | |
| `admin.reviewer.col.decisions` | Decisions | முடிவுகள் | no issue found | |
| `admin.reviewer.col.edits` | Edits | திருத்தங்கள் | no issue found | |
| `admin.reviewer.col.median` | Median seconds to decide | முடிவெடுக்க இடைநிலை நொடிகள் | no issue found | |
| `admin.reviewer.col.rate` | Approval rate | ஒப்புதல் விகிதம் | no issue found | |
| `admin.reviewer.col.reasons` | Reasons | காரணங்கள் | no issue found | |
| `admin.reviewer.col.rejections` | Rejections | நிராகரிப்புகள் | no issue found | |
| `admin.reviewer.col.reviewer` | Reviewer | மதிப்பாய்வாளர் | no issue found | |
| `admin.reviewer.col.verdict` | Verdict | தீர்ப்பு | no issue found | |
| `admin.reviewer.empty` | There are no reviewer decisions in this period. | இந்தக் காலத்தில் மதிப்பாய்வாளர் முடிவுகள் இல்லை. | no issue found | |
| `admin.reviewer.noPolicy` | The policy that judges rubber-stamping is not set, so no verdict is given. | கண்மூடித்தனமான ஒப்புதலை மதிப்பிடும் கொள்கை அமைக்கப்படவில்லை; எனவே தீர்ப்பு வழங்கப்படவில்லை. | no issue found | |
| `admin.reviewer.verdicts.not_configured` | Policy not set | கொள்கை அமைக்கப்படவில்லை | no issue found | |
| `admin.reviewer.verdicts.ok` | OK | சரி | no issue found | |
| `admin.reviewer.verdicts.review` | Needs a look | பார்க்க வேண்டும் | no issue found | |
| `admin.reviewer.verdicts.too_few` | Too few decisions | முடிவுகள் மிகக் குறைவு | no issue found | |
| `admin.reviewer.window` | The last {{days}} days. | கடந்த {{days}} நாட்கள். | no issue found | |
| `admin.rules.approve` | Approve | ஒப்புக்கொள் | A: "Approve" is rendered ஒப்புக்கொள், the same verb that is also used for "acknowledge" (alerts) and that can mean "admit". For a two-doctor safety approval a clinician must choose the verb (for example ஒப்புதல் அளி). Same issue in approveTitle, approveBody and the result strings. | |
| `admin.rules.approveBody` | You are approving exactly the content shown below. The version becomes approved only if a second, different doctor also approves it. The person who wrote a version cannot decide it. | கீழே காட்டப்பட்டுள்ள உள்ளடக்கத்தையே நீங்கள் ஒப்புக்கொள்கிறீர்கள். இரண்டாவது, வேறொரு மருத்துவரும் ஒப்புக்கொண்டால் மட்டுமே இந்தப் பதிப்பு அங்கீகரிக்கப்படும். ஒரு பதிப்பை எழுதியவர் அதில் முடிவெடுக்க முடியாது. | A: Safety text for the approval confirmation. Check that it is unmistakable that a SECOND, different doctor is also needed, and that "the person who wrote it cannot decide it" reads as a rule, not a suggestion. | |
| `admin.rules.approveTitle` | Approve this red-flag rule version? | இந்தச் சிவப்புக் கொடி விதிப் பதிப்பை ஒப்புக்கொள்ளலாமா? | A: Same verb issue as admin.rules.approve: this is the confirmation question before a doctor approves a red-flag rule. | |
| `admin.rules.approvedAt` | Approved at | அங்கீகரிக்கப்பட்ட நேரம் | no issue found | |
| `admin.rules.author.back` | Back to editing | திருத்தத்திற்குத் திரும்பு | no issue found | |
| `admin.rules.author.body` | A rule has a code and any number of literal phrases and token sets. There is no other kind of criterion here, and no regular expression. Nothing is filled in for you. The version is saved as a draft only. | ஒரு விதிக்கு ஒரு குறியீடும், எத்தனை வேண்டுமானாலும் நேரடிச் சொற்றொடர்களும் சொல் தொகுப்புகளும் உண்டு. இங்கே வேறு வகை நிபந்தனை இல்லை, வழக்கமான கோவை (regular expression) இல்லை. உங்களுக்காக எதுவும் நிரப்பப்படுவதில்லை. இந்தப் பதிப்பு வரைவாக மட்டுமே சேமிக்கப்படும். | B: Mentions "regular expression" in Tamil with the English words in brackets. Check whether to keep the English term for a doctor. | |
| `admin.rules.author.code` | Rule code | விதிக் குறியீடு | no issue found | |
| `admin.rules.author.codeHint` | Lower-case letters, digits and underscores, starting with a letter, at most 63 characters. A code that already exists gets the next version number. | சிறிய ஆங்கில எழுத்துகள், இலக்கங்கள், அடிக்கோடுகள் மட்டும்; ஓர் எழுத்தில் தொடங்க வேண்டும்; அதிகபட்சம் 63 எழுத்துகள். ஏற்கெனவே உள்ள குறியீட்டுக்கு அடுத்த பதிப்பு எண் கிடைக்கும். | no issue found | |
| `admin.rules.author.confirmBody` | This saves exactly what is shown below as a draft. A draft is never active, and it is approved only when two different doctors approve it. You cannot approve a draft you wrote. | கீழே காட்டப்பட்டுள்ளதையே இது வரைவாகச் சேமிக்கிறது. வரைவு ஒருபோதும் செயலில் இருக்காது; இரண்டு வெவ்வேறு மருத்துவர்கள் ஒப்புக்கொண்டால் மட்டுமே அது அங்கீகரிக்கப்படும். நீங்கள் எழுதிய வரைவை நீங்களே அங்கீகரிக்க முடியாது. | A: Says the draft is never active and needs two different doctors, and that you cannot approve your own. Same wording checks as admin.rules.draftNote. | |
| `admin.rules.author.confirmTitle` | Save this draft red-flag rule version? | இந்தச் சிவப்புக் கொடி விதிப் பதிப்பின் வரைவைச் சேமிக்கலாமா? | no issue found | |
| `admin.rules.author.conflict` | This request clashes with an earlier one that used the same key. Close this window and start again. | இதே விசையைப் பயன்படுத்திய முந்தைய கோரிக்கையுடன் இந்தக் கோரிக்கை முரண்படுகிறது. இந்தச் சாளரத்தை மூடி மீண்டும் தொடங்கவும். | no issue found | |
| `admin.rules.author.err.code` | The rule code must start with a lower-case letter and use only lower-case letters, digits and underscores (at most 63 characters). | விதிக் குறியீடு சிறிய ஆங்கில எழுத்தில் தொடங்கி, சிறிய ஆங்கில எழுத்துகள், இலக்கங்கள், அடிக்கோடுகள் மட்டுமே கொண்டிருக்க வேண்டும் (அதிகபட்சம் 63 எழுத்துகள்). | no issue found | |
| `admin.rules.author.err.noCriterion` | Add at least one phrase or one token set. | குறைந்தது ஒரு சொற்றொடர் அல்லது ஒரு சொல் தொகுப்பைச் சேர்க்கவும். | no issue found | |
| `admin.rules.author.err.phraseChars` | Phrase line {{line}} has a control character or an invisible character. Retype it. | சொற்றொடர் வரி {{line}} இல் கட்டுப்பாட்டு எழுத்து அல்லது கண்ணுக்குத் தெரியாத எழுத்து உள்ளது. மீண்டும் தட்டச்சு செய்யவும். | B: "Control character or an invisible character": technical, check it is understandable by a doctor and that retyping is the right advice. | |
| `admin.rules.author.err.phraseLong` | Phrase line {{line}} is too long. | சொற்றொடர் வரி {{line}} மிக நீளமானது. | no issue found | |
| `admin.rules.author.err.sameWords` | Token set line {{line}} needs at least two different words. | சொல் தொகுப்பு வரி {{line}} இல் குறைந்தது இரண்டு வெவ்வேறு சொற்கள் வேண்டும். | B: "At least two different words" for a token set: check clarity. | |
| `admin.rules.author.err.setSize` | Token set line {{line}} must have between 2 and 10 words. | சொல் தொகுப்பு வரி {{line}} இல் 2 முதல் 10 சொற்கள் இருக்க வேண்டும். | no issue found | |
| `admin.rules.author.err.tokenChars` | Token set line {{line}} has a control character or an invisible character. Retype it. | சொல் தொகுப்பு வரி {{line}} இல் கட்டுப்பாட்டு எழுத்து அல்லது கண்ணுக்குத் தெரியாத எழுத்து உள்ளது. மீண்டும் தட்டச்சு செய்யவும். | B: Same as phraseChars. | |
| `admin.rules.author.err.tokenLong` | Token set line {{line}} has a word that is too long. | சொல் தொகுப்பு வரி {{line}} இல் மிக நீளமான ஒரு சொல் உள்ளது. | no issue found | |
| `admin.rules.author.err.tooManyPhrases` | There are too many phrases (at most {{max}}). | சொற்றொடர்கள் மிக அதிகம் (அதிகபட்சம் {{max}}). | no issue found | |
| `admin.rules.author.err.tooManySets` | There are too many token sets (at most {{sets}}). | சொல் தொகுப்புகள் மிக அதிகம் (அதிகபட்சம் {{sets}}). | no issue found | |
| `admin.rules.author.invalid` | The draft is outside what a rule may contain. Nothing was saved. Check the limits and try again. | வரைவு, ஒரு விதி கொண்டிருக்கக்கூடியதற்கு வெளியே உள்ளது. எதுவும் சேமிக்கப்படவில்லை. வரம்புகளைச் சரிபார்த்து மீண்டும் முயலவும். | no issue found | |
| `admin.rules.author.open` | Write a new rule version | புதிய விதிப் பதிப்பை எழுது | no issue found | |
| `admin.rules.author.phrases` | Phrases (one per line) | சொற்றொடர்கள் (ஒரு வரிக்கு ஒன்று) | no issue found | |
| `admin.rules.author.phrasesHint` | Each line is one literal phrase, at most {{length}} characters, at most {{max}} phrases. Empty lines are ignored. | ஒவ்வொரு வரியும் ஒரு நேரடிச் சொற்றொடர், அதிகபட்சம் {{length}} எழுத்துகள், அதிகபட்சம் {{max}} சொற்றொடர்கள். காலி வரிகள் புறக்கணிக்கப்படும். | no issue found | |
| `admin.rules.author.problems` | Problems to fix | சரிசெய்ய வேண்டியவை | no issue found | |
| `admin.rules.author.review` | Review the draft | வரைவைச் சரிபார் | no issue found | |
| `admin.rules.author.save` | Save draft | வரைவைச் சேமி | no issue found | |
| `admin.rules.author.saved` | The draft was saved. It is not approved and it is not active. | வரைவு சேமிக்கப்பட்டது. அது அங்கீகரிக்கப்படவில்லை, செயலிலும் இல்லை. | A: "Not approved and not active": check both halves are unmistakable. | |
| `admin.rules.author.sets` | Token sets (one set per line, words separated by spaces) | சொல் தொகுப்புகள் (ஒரு வரிக்கு ஒரு தொகுப்பு, சொற்கள் இடைவெளியால் பிரிக்கப்பட வேண்டும்) | no issue found | |
| `admin.rules.author.setsHint` | Each line is one set of {{min}} to {{max}} words with at least two different words, each word at most {{token}} characters, at most {{sets}} sets. | ஒவ்வொரு வரியும் {{min}} முதல் {{max}} சொற்கள் கொண்ட ஒரு தொகுப்பு; குறைந்தது இரண்டு வெவ்வேறு சொற்கள்; ஒவ்வொரு சொல்லும் அதிகபட்சம் {{token}} எழுத்துகள்; அதிகபட்சம் {{sets}} தொகுப்புகள். | C: Numbers and limits for token sets; check number words. | |
| `admin.rules.author.title` | Write a draft red-flag rule version | சிவப்புக் கொடி விதிப் பதிப்பின் வரைவை எழுது | no issue found | |
| `admin.rules.col.at` | When | எப்போது | no issue found | |
| `admin.rules.col.by` | Decided by (account id) | முடிவெடுத்தவர் (கணக்கு அடையாளம்) | no issue found | |
| `admin.rules.col.decision` | Decision | முடிவு | no issue found | |
| `admin.rules.decisionWords.approve` | Approved | ஒப்புக்கொண்டார் | no issue found | |
| `admin.rules.decisionWords.reject` | Rejected | நிராகரித்தார் | no issue found | |
| `admin.rules.decisionWords.retire` | Retired it | நிறுத்தினார் | B: "நிறுத்தினார்" (he/she stopped it) as the decision word in a table of who decided; the honorific form is neutral, check it matches "ஒப்புக்கொண்டார்" and "நிராகரித்தார்". | |
| `admin.rules.decisions` | Decisions so far | இதுவரை எடுத்த முடிவுகள் | no issue found | |
| `admin.rules.draftNote` | This is a draft. It is never active. It becomes approved only when two different doctors approve it, and its author cannot be one of them. | இது ஒரு வரைவு. இது ஒருபோதும் செயலில் இருக்காது. இரண்டு வெவ்வேறு மருத்துவர்கள் ஒப்புக்கொண்டால் மட்டுமே இது அங்கீகரிக்கப்படும்; அதை எழுதியவர் அவர்களில் ஒருவராக இருக்க முடியாது. | A: Says a draft is never active. A wrong word here could make a draft look live. Check "never" (ஒருபோதும்) and "active" (செயலில்). | |
| `admin.rules.empty` | There are no red-flag rule versions to show. | காட்ட சிவப்புக் கொடி விதிப் பதிப்புகள் இல்லை. | no issue found | |
| `admin.rules.intro` | The red-flag rule versions of this clinic, with the decisions made on each. Every phrase and pattern is shown exactly as the system holds it. This screen cannot write or change a rule. | இந்த மருத்துவமனையின் சிவப்புக் கொடி விதிப் பதிப்புகளும், ஒவ்வொன்றிலும் எடுக்கப்பட்ட முடிவுகளும். ஒவ்வொரு சொற்றொடரும் வடிவமும் அமைப்பில் உள்ளபடியே காட்டப்படுகின்றன. இந்தத் திரையில் விதியை எழுதவோ மாற்றவோ முடியாது. | B: "Shown exactly as the system holds it": check it conveys that the screen cannot change a rule. | |
| `admin.rules.listLabel` | Red-flag rule versions | சிவப்புக் கொடி விதிப் பதிப்புகள் | no issue found | |
| `admin.rules.noDecisions` | Nobody has decided on this version yet. | இந்தப் பதிப்பில் இதுவரை யாரும் முடிவெடுக்கவில்லை. | no issue found | |
| `admin.rules.none` | None | இல்லை | no issue found | |
| `admin.rules.origin` | Environment | சூழல் | no issue found | |
| `admin.rules.origins.canary` | Canary | கேனரி | B: "Canary" is transliterated கேனரி; a test-case word, check whether clinicians would understand it, or keep it in English. | |
| `admin.rules.origins.eval` | Evaluation | மதிப்பீடு | C: மதிப்பீடு (evaluation): check it is a test environment and not "assessment of a person". | |
| `admin.rules.origins.live` | Live | நேரடி | no issue found | |
| `admin.rules.origins.test` | Test | சோதனை | no issue found | |
| `admin.rules.patterns` | Patterns | வடிவங்கள் | C: வடிவங்கள் (patterns): a technical label for older rules. | |
| `admin.rules.phrases` | Phrases | சொற்றொடர்கள் | C: சொற்றொடர்கள் (phrases): check it is clear these are literal text matched in messages. | |
| `admin.rules.readOnly` | You can read these rules and who decided on them. Only doctors decide. | இந்த விதிகளையும் அவற்றில் முடிவெடுத்தவர்களையும் நீங்கள் படிக்கலாம். மருத்துவர்கள் மட்டுமே முடிவெடுப்பார்கள். | no issue found | |
| `admin.rules.reject` | Reject | நிராகரி | B: நிராகரி is a plain word for reject; check that it reads as final for a red-flag rule version. | |
| `admin.rules.rejectBody` | One rejection closes this version for good. It cannot be approved afterwards. | ஒரு நிராகரிப்பு இந்தப் பதிப்பை நிரந்தரமாக மூடிவிடும். அதன் பிறகு இதை ஒப்புக்கொள்ள முடியாது. | A: "One rejection closes this version for good" (நிரந்தரமாக மூடிவிடும்): check that the irreversibility is clear and that "close" is not read as "pause". | |
| `admin.rules.rejectTitle` | Reject this red-flag rule version? | இந்தச் சிவப்புக் கொடி விதிப் பதிப்பை நிராகரிக்கலாமா? | B: Confirmation question; check the verb with admin.rules.reject. | |
| `admin.rules.result.already_decided` | You already decided on this version the other way. Nothing changed. | இந்தப் பதிப்பில் நீங்கள் ஏற்கெனவே எதிர்த் திசையில் முடிவெடுத்துவிட்டீர்கள். எதுவும் மாறவில்லை. | B: "The other way" rendered எதிர்த் திசையில்: check it means the opposite decision. | |
| `admin.rules.result.approved` | Your approval was recorded and this version is now approved. | உங்கள் ஒப்புதல் பதிவு செய்யப்பட்டது; இந்தப் பதிப்பு இப்போது அங்கீகரிக்கப்பட்டுள்ளது. | A: "This version is now approved": check the word is the same as the status word and cannot be read as "recorded only". | |
| `admin.rules.result.author_cannot_decide` | You wrote this version, so you cannot decide it. A different doctor must. | இந்தப் பதிப்பை நீங்கள் எழுதினீர்கள், எனவே இதில் முடிவெடுக்க முடியாது. வேறொரு மருத்துவர் முடிவெடுக்க வேண்டும். | A: Tells the author they cannot decide their own rule. Check it is firm and not polite advice. | |
| `admin.rules.result.empty_rule` | This version has no phrases, patterns or vital-sign criteria, so it cannot be approved. | இந்தப் பதிப்பில் சொற்றொடர்கள், வடிவங்கள் அல்லது உடல்நிலை அளவீட்டு நிபந்தனைகள் எதுவும் இல்லை, எனவே இதை ஒப்புக்கொள்ள முடியாது. | B: No content, cannot be approved: check wording. | |
| `admin.rules.result.not_approved` | This version is not approved, so it cannot be retired. Nothing was recorded. | இந்தப் பதிப்பு ஒப்புக்கொள்ளப்படவில்லை, எனவே நிறுத்த முடியாது. எதுவும் பதிவு செய்யப்படவில்லை. | A: Says nothing was recorded because the version is not approved; a reader must not take it as retired. | |
| `admin.rules.result.not_draft` | This version is not a draft any more, so nothing was recorded. | இந்தப் பதிப்பு இப்போது வரைவு அல்ல, எனவே எதுவும் பதிவு செய்யப்படவில்லை. | B: "Not a draft any more": check the tense and that nothing was saved is clear. | |
| `admin.rules.result.origin_mismatch` | This version belongs to a different environment than this clinic, so nothing was recorded. | இந்தப் பதிப்பு இந்த மருத்துவமனையின் சூழலைச் சேர்ந்தது அல்ல, எனவே எதுவும் பதிவு செய்யப்படவில்லை. | B: "Environment" rendered சூழல்; check it is understood as live, test and so on, not surroundings. | |
| `admin.rules.result.platform_rule` | This rule applies to every clinic. Who may approve it has not been decided, so nothing was approved here. | இந்த விதி எல்லா மருத்துவமனைகளுக்கும் பொருந்தும். இதை யார் ஒப்புக்கொள்ளலாம் என்பது இன்னும் முடிவு செய்யப்படவில்லை, எனவே இங்கே எதுவும் அங்கீகரிக்கப்படவில்லை. | B: "This rule applies to every clinic. Who may approve it has not been decided." Check the meaning of "applies to every clinic". | |
| `admin.rules.result.recorded` | Your approval was recorded. The version is not approved yet: a second, different doctor must approve it. | உங்கள் ஒப்புதல் பதிவு செய்யப்பட்டது. இந்தப் பதிப்பு இன்னும் அங்கீகரிக்கப்படவில்லை: இரண்டாவது, வேறொரு மருத்துவர் ஒப்புக்கொள்ள வேண்டும். | A: "Your approval was recorded. It is not approved yet: a second, different doctor must approve it." Check that the two halves cannot be read as "approved". | |
| `admin.rules.result.rejected` | Your rejection was recorded. This version is closed. | உங்கள் நிராகரிப்பு பதிவு செய்யப்பட்டது. இந்தப் பதிப்பு மூடப்பட்டது. | B: Check the closed state is clear. | |
| `admin.rules.result.rejected_earlier` | A doctor already rejected this version, so it cannot be approved. | ஒரு மருத்துவர் ஏற்கெனவே இந்தப் பதிப்பை நிராகரித்துவிட்டார், எனவே இதை ஒப்புக்கொள்ள முடியாது. | B: Check it says a doctor already rejected it, so it cannot be approved. | |
| `admin.rules.result.retired` | Your retire decision was recorded and this version is now retired. | உங்கள் நிறுத்த முடிவு பதிவானது; இந்தப் பதிப்பு இப்போது நிறுத்தப்பட்டது. | A: Tells a doctor a rule version is now retired. Must be unambiguous that it is out of use. | |
| `admin.rules.result.unknown` | The system answered with a result this screen does not know. Nothing is shown as approved. Reload the page to see the current state. | இந்தத் திரைக்குத் தெரியாத ஒரு முடிவை அமைப்பு தெரிவித்தது. எதுவும் அங்கீகரிக்கப்பட்டதாகக் காட்டப்படவில்லை. தற்போதைய நிலையைப் பார்க்க பக்கத்தை மீண்டும் ஏற்றவும். | B: Fallback: "nothing is shown as approved. Reload". Check clarity. | |
| `admin.rules.result.withdrawn` | This draft is withdrawn. It is closed for good and is not in force. | இந்த வரைவு திரும்பப் பெறப்பட்டது. அது முற்றிலும் மூடப்பட்டது; நடைமுறையில் இல்லை. | A: Tells a doctor a draft is closed and not in force; also shown when a repeat request reaches an already withdrawn version. | |
| `admin.rules.retire` | Retire this version | இந்தப் பதிப்பை நிறுத்து | B: Button label "நிறுத்து" (stop) for retire; check the word clinicians use for taking a rule out of use. | |
| `admin.rules.retireBody` | Your retire decision is recorded now, and nothing is retired until a second, different doctor also records one. A retired version never comes back: a new version is the way back. If this is the newest version of its rule code, the whole rule code stops being in force. | உங்கள் நிறுத்த முடிவு இப்போது பதிவாகும்; வேறொரு மருத்துவரும் பதிவு செய்யும் வரை எதுவும் நிறுத்தப்படாது. நிறுத்தப்பட்ட பதிப்பு மீண்டும் வராது; புதிய பதிப்பே திரும்ப வழி. இது இந்த விதிக் குறியீட்டின் புதிய பதிப்பாக இருந்தால், முழு விதிக் குறியீடும் நடைமுறையில் இருக்காது. | A: Says nothing is retired until a second, different doctor records it, and that a retired newest version switches off the whole rule code. A wrong reading could leave a rule in force or switch one off. A native speaker who is a clinician should check. Clinician sheet item 37. | |
| `admin.rules.retireCount` | Retire decisions recorded so far: {{count}}. Two different doctors are needed. | இதுவரை பதிவான நிறுத்த முடிவுகள்: {{count}}. இரண்டு வெவ்வேறு மருத்துவர்கள் தேவை. | A: Says how many retire decisions exist and that two different doctors are needed. A count read wrongly could make a person think a rule is already retired. | |
| `admin.rules.retireTitle` | Retire this approved red-flag rule version? | ஒப்புக்கொள்ளப்பட்ட இந்தச் சிவப்புக் கொடி விதிப் பதிப்பை நிறுத்தவா? | A: Asks whether to retire an approved red-flag rule version; "நிறுத்தவா" can read as "stop it now" although one doctor alone cannot retire it. | |
| `admin.rules.status` | Status | நிலை | no issue found | |
| `admin.rules.statuses.approved` | Approved | அங்கீகரிக்கப்பட்டது | B: அங்கீகரிக்கப்பட்டது versus the button verb ஒப்புக்கொள்: pick one family of words for approve. | |
| `admin.rules.statuses.draft_unreviewed` | Draft, not approved | வரைவு, அங்கீகரிக்கப்படவில்லை | A: "Draft, not approved": check it cannot be read as "approved draft". The word அங்கீகரிக்கப்பட்டது is used for the status "Approved" but the button verb is ஒப்புக்கொள்: two words for one idea. | |
| `admin.rules.statuses.retired` | Retired | நிறுத்தப்பட்டது | B: "Retired" is rendered நிறுத்தப்பட்டது ("stopped"), the same root as the "stop" and "hold" strings. Check it means taken out of use, not paused. | |
| `admin.rules.statuses.withdrawn` | Withdrawn | திரும்பப் பெறப்பட்டது | A: A status word. "திரும்பப் பெறப்பட்டது" (withdrawn) must not be read as "retired" (நிறுத்தப்பட்டது), since the two mean different things for whether a rule is in force. | |
| `admin.rules.tokenSets` | Token sets | சொல் தொகுப்புகள் | B: "Token sets" rendered சொல் தொகுப்புகள் ("word sets"): a technical term; check it is understood as words that must all appear together. | |
| `admin.rules.twoDoctors` | A draft version becomes approved only when two different doctors approve it, each with a fresh authenticator code. One rejection closes the version. Nothing is approved by default. | வரைவுப் பதிப்பு, இரண்டு வெவ்வேறு மருத்துவர்கள் ஒவ்வொருவரும் புதிய அங்கீகார குறியீட்டுடன் ஒப்புக்கொண்டால் மட்டுமே அங்கீகரிக்கப்படும். ஒரு நிராகரிப்பு அந்தப் பதிப்பை மூடிவிடும். இயல்பாக எதுவும் அங்கீகரிக்கப்படுவதில்லை. | A: States the two-doctor rule and "nothing is approved by default" (இயல்பாக எதுவும் அங்கீகரிக்கப்படுவதில்லை). Check that இயல்பாக reads as "by default" and not "normally". | |
| `admin.rules.vitals` | Vital-sign criteria | உடல்நிலை அளவீட்டு நிபந்தனைகள் | B: "Vital-sign criteria" rendered உடல்நிலை அளவீட்டு நிபந்தனைகள்: check the clinical term clinicians use for vital signs. | |
| `admin.rules.vitalsNo` | This version has no vital-sign criteria. | இந்தப் பதிப்பில் உடல்நிலை அளவீட்டு நிபந்தனைகள் இல்லை. | no issue found | |
| `admin.rules.vitalsYes` | This version has vital-sign criteria. | இந்தப் பதிப்பில் உடல்நிலை அளவீட்டு நிபந்தனைகள் உள்ளன. | no issue found | |
| `admin.rules.withdraw` | Withdraw this draft | இந்த வரைவைத் திரும்பப் பெறு | B: Button label "திரும்பப் பெறு" for withdraw a draft; same verb as revoke. | |
| `admin.rules.withdrawBody` | A withdrawn draft is closed for good. It can never be approved and it is never in force. A withdrawn version is not the same as a retired one. Only a draft can be withdrawn. To change a rule, write a new version. | திரும்பப் பெறப்பட்ட வரைவு முற்றிலும் மூடப்படும். அதை ஒருபோதும் ஒப்புக்கொள்ள முடியாது; அது ஒருபோதும் நடைமுறையில் இருக்காது. திரும்பப் பெறுதல் என்பது நிறுத்துதலிலிருந்து வேறுபட்டது. வரைவை மட்டுமே திரும்பப் பெற முடியும். ஒரு விதியை மாற்ற, புதிய பதிப்பை எழுதுங்கள். | A: Says a withdrawn draft is closed for good and is NOT the same as retired. "திரும்பப் பெறுதல்" and "நிறுத்துதல்" are close in everyday Tamil, so the contrast may not come across. Clinician sheet item 37. | |
| `admin.rules.withdrawTitle` | Withdraw this draft red-flag rule version? | இந்தச் சிவப்புக் கொடி விதி வரைவுப் பதிப்பைத் திரும்பப் பெறவா? | B: Check "திரும்பப் பெறவா" for a draft (a withdrawal, not a deletion). | |
| `admin.rules.youDecided` | You have already decided on this version. | இந்தப் பதிப்பில் நீங்கள் ஏற்கெனவே முடிவெடுத்துவிட்டீர்கள். | no issue found | |
| `admin.rules.youRetired` | You have recorded your retire decision on this version. | இந்தப் பதிப்பில் உங்கள் நிறுத்த முடிவைப் பதிவு செய்துவிட்டீர்கள். | B: Check it says the doctor has recorded their own decision only. | |
| `admin.rules.youWrote` | You wrote this version. A different doctor has to approve or reject it. | இந்தப் பதிப்பை நீங்கள் எழுதினீர்கள். வேறொரு மருத்துவர்தான் இதை ஒப்புக்கொள்ளவோ நிராகரிக்கவோ வேண்டும். | B: Says the author cannot approve or reject; check "வேறொரு மருத்துவர்தான்" (only a different doctor) is not read as a request. | |
| `admin.stepUp` | This needs a fresh authenticator code. You will be asked for it. | இதற்குப் புதிய அங்கீகார குறியீடு தேவை. அது உங்களிடம் கேட்கப்படும். | B: "fresh" code is rendered புதிய ("new"). It means a RECENTLY entered code, not a never-used one; a reviewer should pick wording that says "recent". | |
| `admin.tab.accounts` | Staff accounts | பணியாளர் கணக்குகள் | no issue found | |
| `admin.tab.autonomy` | Autonomy | தன்னாட்சி | no issue found | |
| `admin.tab.cost` | Cost canvas | செலவு வரைபடம் | no issue found | |
| `admin.tab.grievances` | Grievances | குறைகள் | no issue found | |
| `admin.tab.recovery` | Staff recovery | பணியாளர் மீட்பு | no issue found | |
| `admin.tab.reviewer` | Reviewer statistics | மதிப்பாய்வாளர் புள்ளிவிவரங்கள் | no issue found | |
| `admin.tab.rules` | Red-flag rules | சிவப்புக் கொடி விதிகள் | B: "Red-flag rules" tab: literal சிவப்புக் கொடி (see the red flag theme); check the term clinicians use. | |
| `admin.tab.voice` | Voice transcripts | குரல் பதிவுகள் | no issue found | |
| `admin.tabs` | Admin sections | நிர்வாகப் பிரிவுகள் | no issue found | |
| `admin.title` | Admin | நிர்வாகம் | no issue found | |
| `admin.voice.col.action` | Action | செயல் | no issue found | |
| `admin.voice.col.chunks` | Chunks | துண்டுகள் | no issue found | |
| `admin.voice.col.expired` | Expired chunks | காலாவதியான துண்டுகள் | no issue found | |
| `admin.voice.col.retention` | Retention (days) | தக்கவைப்பு (நாட்கள்) | no issue found | |
| `admin.voice.col.session` | Session | அமர்வு | no issue found | |
| `admin.voice.col.started` | Started | தொடங்கியது | no issue found | |
| `admin.voice.col.status` | Status | நிலை | no issue found | |
| `admin.voice.col.workflow` | Workflow | பணிப்பாய்வு | no issue found | |
| `admin.voice.empty` | There are no voice sessions. | குரல் அமர்வுகள் இல்லை. | no issue found | |
| `admin.voice.keysMissing` | Encryption keys are not configured, so the transcript cannot be read. | குறியாக்க விசைகள் அமைக்கப்படவில்லை; எனவே பதிவைப் படிக்க முடியாது. | no issue found | |
| `admin.voice.noChunks` | There is no unexpired text in this session. | இந்த அமர்வில் காலாவதியாகாத உரை இல்லை. | no issue found | |
| `admin.voice.note` | Transcripts are read and purged only by an administrator with a fresh second factor. Every read and purge is audited by the database. | புதிய இரண்டாம் காரணியுடன் ஒரு நிர்வாகி மட்டுமே பதிவுகளைப் படிக்கவும் அழிக்கவும் முடியும். ஒவ்வொரு படிப்பும் அழிப்பும் தரவுத்தளத்தால் தணிக்கை செய்யப்படுகிறது. | no issue found | |
| `admin.voice.purge` | Purge expired transcripts | காலாவதியான பதிவுகளை அழி | no issue found | |
| `admin.voice.purgeBody` | Deletes expired transcript chunks of this clinic. An unexpired chunk is never deleted. This cannot be undone. | இந்த மருத்துவமனையின் காலாவதியான பதிவுத் துண்டுகளை நீக்குகிறது. காலாவதியாகாத துண்டு ஒருபோதும் நீக்கப்படாது. இதைத் திரும்பப் பெற முடியாது. | A: Irreversible deletion. The Tamil says it cannot be undone (திரும்பப் பெற முடியாது), which also means "cannot be taken back / withdrawn"; confirm it reads as "cannot be undone". | |
| `admin.voice.purgeConfirm` | Purge | அழி | no issue found | |
| `admin.voice.purgeLimit` | Most chunks to delete (optional) | நீக்க வேண்டிய அதிகபட்சத் துண்டுகள் (விருப்பம்) | no issue found | |
| `admin.voice.purged` | Deleted {{count}} expired chunks. | {{count}} காலாவதியான துண்டுகள் நீக்கப்பட்டன. | no issue found | |
| `admin.voice.read` | Read transcript | பதிவைப் படி | no issue found | |
| `admin.voice.readBody` | The text is shown only while this window is open, and the read is audited. | இந்த சாளரம் திறந்திருக்கும் வரை மட்டுமே உரை காட்டப்படும்; படிப்பு தணிக்கை செய்யப்படும். | no issue found | |
| `admin.voice.readTitle` | Read this transcript? | இந்தப் பதிவைப் படிக்கவா? | no issue found | |
| `admin.voice.statuses.ended` | Ended | முடிந்தது | no issue found | |
| `admin.voice.statuses.open` | Open | திறந்தது | no issue found | |
| `admin.voice.transcript` | Transcript | பதிவு | no issue found | |
| `admin.voice.workflows.intake_question` | Intake question | உள்வாங்கல் கேள்வி | C: "intake" rendered உள்வாங்கல் ("absorbing"); the usual clinic wording is different. | |
| `admin.voice.workflows.readiness_item` | Readiness item | தயார்நிலை உருப்படி | no issue found | |
| `admin.voice.workflows.slot_offer` | Slot offer | நேர இட வாய்ப்பு | no issue found | |
| `alerts.acknowledge` | Acknowledge | ஏற்றுக்கொள் | A: Button "Acknowledge" is rendered ஏற்றுக்கொள் ("accept / agree to"), the same verb as Approve (ஒப்புக்கொள்) in many places. Acknowledging an alert only records that you have seen it. Also in admin.grievances.acknowledge and tasks. Check the verb. | |
| `alerts.acknowledged` | The alert was acknowledged. | எச்சரிக்கை ஏற்கப்பட்டது. | A: "acknowledged" is rendered ஏற்கப்பட்டது ("accepted"); an alert that is only SEEN could be read as accepted or agreed with. Check the verb; the same verb is used in alerts.already and on the alert buttons. | |
| `alerts.already` | This alert was already acknowledged. The list will refresh. | இந்த எச்சரிக்கை ஏற்கனவே ஏற்கப்பட்டது. பட்டியல் புதுப்பிக்கப்படும். | A: Same verb issue as alerts.acknowledged. | |
| `alerts.class.canary_approved` | A test case was approved | ஒரு சோதனை நிகழ்வு ஒப்புக்கொள்ளப்பட்டது | no issue found | |
| `alerts.class.dead_letters` | Failed jobs waiting | தோல்வியுற்ற பணிகள் காத்திருக்கின்றன | no issue found | |
| `alerts.class.delivery_failed` | Delivery failed | வழங்கல் தோல்வியடைந்தது | no issue found | |
| `alerts.class.fallback_spike` | Fallback spike | மாற்று வழி பயன்பாடு அதிகரிப்பு | no issue found | |
| `alerts.class.identity_revoked_suspect` | An identity confirmation was revoked as suspect | அடையாள உறுதிப்படுத்தல் சந்தேகத்தின் பேரில் திரும்பப் பெறப்பட்டது | A: "An identity confirmation was revoked as suspect" rendered அடையாள உறுதிப்படுத்தல் சந்தேகத்தின் பேரில் திரும்பப் பெறப்பட்டது. திரும்பப் பெறு is the same verb as "cannot be undone" elsewhere, and சந்தேகம் may read as suspicion of the person. Needs a native reviewer. | |
| `alerts.class.queue_age` | Queue is old | வரிசை பழையதாகிவிட்டது | no issue found | |
| `alerts.class.red_flag_overdue` | Red-flag task overdue | சிவப்புக் கொடிப் பணி தாமதமானது | A: "Red-flag task overdue" (an overdue safety task) uses the literal சிவப்புக் கொடி term; an overdue red-flag alert must be unmistakable. | |
| `alerts.class.spend_runaway` | Spending is running away | செலவு கட்டுப்பாட்டை மீறுகிறது | no issue found | |
| `alerts.class.task_escalated` | Task escalated | பணி உயர்நிலைக்கு அனுப்பப்பட்டது | B: "escalated" rendered உயர்நிலைக்கு அனுப்பப்பட்டது ("sent to a higher level"); see readiness.statuses.nurse_escalated. | |
| `alerts.class.task_overdue` | Task overdue | பணி தாமதமானது | no issue found | |
| `alerts.class.tracing_cap` | Tracing limit reached | தடமறிதல் வரம்பு எட்டப்பட்டது | no issue found | |
| `alerts.col.action` | Action | செயல் | no issue found | |
| `alerts.col.aimedAt` | Aimed at | யாருக்கு | no issue found | |
| `alerts.col.class` | Class | வகை | no issue found | |
| `alerts.col.count` | Count | எண்ணிக்கை | no issue found | |
| `alerts.col.raised` | Raised | எழுப்பப்பட்டது | no issue found | |
| `alerts.col.ref` | Reference | குறிப்பு | no issue found | |
| `alerts.col.state` | State | நிலை | no issue found | |
| `alerts.confirmBody` | This records that you have seen it, under your name. It can be done once. | இதைப் பார்த்தீர்கள் என்று உங்கள் பெயரில் பதிவாகும். இதை ஒரு முறை மட்டுமே செய்ய முடியும். | B: Says "records that you saw it"; check it matches the verb used for the button. | |
| `alerts.confirmTitle` | Acknowledge this alert? | இந்த எச்சரிக்கையை ஏற்றுக்கொள்ளவா? | no issue found | |
| `alerts.empty` | There are no alerts. | எச்சரிக்கைகள் இல்லை. | no issue found | |
| `alerts.includeAcknowledged` | Include acknowledged alerts | ஏற்கப்பட்ட எச்சரிக்கைகளையும் சேர் | no issue found | |
| `alerts.note` | Alerts aimed at a role you hold. The role shown is who the alert has reached, so an overdue item that has escalated is aimed at a higher role. | நீங்கள் வகிக்கும் பங்கிற்கான எச்சரிக்கைகள். காட்டப்படும் பங்கு, எச்சரிக்கை யாரை அடைந்துள்ளது என்பதைக் குறிக்கும்; எனவே உயர்நிலைக்குச் சென்ற தாமதமான பணி உயர் பங்கிற்கு அனுப்பப்படும். | no issue found | |
| `alerts.stateDone` | Acknowledged | ஏற்கப்பட்டது | no issue found | |
| `alerts.stateOpen` | Open | திறந்தது | no issue found | |
| `alerts.title` | Alerts | எச்சரிக்கைகள் | no issue found | |
| `app.hospitalName` | [PLACEHOLDER: hospital name] | [PLACEHOLDER: மருத்துவமனையின் பெயர்] | no issue found | |
| `app.staffTitle` | Staff console | பணியாளர் தளம் | no issue found | |
| `audit.action` | Action | செயல் | no issue found | |
| `audit.actor` | Person (account id) | நபர் (கணக்கு அடையாளம்) | no issue found | |
| `audit.actorInvalid` | An account id looks like 3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b. | கணக்கு அடையாளம் 3f2b8c1e-6a4d-4e1b-9d0a-1c2d3e4f5a6b போல இருக்கும். | no issue found | |
| `audit.col.action` | Action | செயல் | no issue found | |
| `audit.col.clinic` | Clinic | மருத்துவமனை | no issue found | |
| `audit.col.details` | Details | விவரங்கள் | no issue found | |
| `audit.col.origin` | Origin | மூலம் | no issue found | |
| `audit.col.resource` | Record | பதிவு | B: The word பதிவு is used for a patient record, an audit log (audit.title தணிக்கைப் பதிவு), a voice transcript (admin.voice.transcript), a recorded fact and for "register". Readers cannot tell which is meant; use different words. | |
| `audit.col.when` | When | எப்போது | no issue found | |
| `audit.col.who` | Who | யார் | no issue found | |
| `audit.empty` | No rows match. | பொருந்தும் வரிகள் இல்லை. | no issue found | |
| `audit.end` | That is the end of the log for this search. | இந்தத் தேடலுக்கான பதிவின் முடிவு இதுவே. | no issue found | |
| `audit.filterHint` | All filters are optional. Leave a box empty to not filter on it. | எல்லா வடிகட்டிகளும் விருப்பமானவை. வடிகட்ட வேண்டாத பெட்டியை காலியாக விடுங்கள். | no issue found | |
| `audit.noClinic` | No clinic | மருத்துவமனை இல்லை | no issue found | |
| `audit.noDetails` | None | இல்லை | no issue found | |
| `audit.noRole` | No role recorded | பங்கு பதிவாகவில்லை | no issue found | |
| `audit.note` | Every row is shown as the system recorded it, newest first, for the clinics you audit. Nothing here can be changed. | ஒவ்வொரு வரியும் அமைப்பு பதிவு செய்தபடியே, நீங்கள் தணிக்கை செய்யும் மருத்துவமனைகளுக்கு, புதியது முதலில் காட்டப்படுகிறது. இங்கே எதையும் மாற்ற முடியாது. | no issue found | |
| `audit.older` | Older rows | பழைய வரிகள் | no issue found | |
| `audit.origin.canary` | Canary | கேனரி | no issue found | |
| `audit.origin.eval` | Evaluation | மதிப்பீடு | no issue found | |
| `audit.origin.live` | Live | நேரடி | no issue found | |
| `audit.origin.test` | Test | சோதனை | no issue found | |
| `audit.read` | Read the log | பதிவைப் படி | no issue found | |
| `audit.resourceId` | Record id | பதிவு அடையாளம் | no issue found | |
| `audit.resourceType` | Kind of record | பதிவின் வகை | no issue found | |
| `audit.showDetails` | Show details | விவரங்களைக் காட்டு | no issue found | |
| `audit.since` | From | முதல் | no issue found | |
| `audit.title` | Audit log | தணிக்கைப் பதிவு | no issue found | |
| `audit.until` | Until | வரை | no issue found | |
| `audit.zoneHint` | Dates are in your browser's time zone. | தேதிகள் உங்கள் உலாவியின் நேர மண்டலத்தில் உள்ளன. | no issue found | |
| `backfill.action.send` | Send | அனுப்பு | no issue found | |
| `backfill.action.skip` | Skip | தவிர் | no issue found | |
| `backfill.action.stop` | Stop | நிறுத்து | no issue found | |
| `backfill.candidate` | Current candidate | தற்போதைய வேட்பாளர் | no issue found | |
| `backfill.close` | Close | மூடு | no issue found | |
| `backfill.confirm.send.body` | Holds the slot for the current candidate and records the offer. The system sends nothing to the patient: you deliver the offer yourself from the Offers tab. | தற்போதைய வேட்பாளருக்கு இடத்தை நிறுத்தி வாய்ப்பைப் பதிவு செய்கிறது. கணினி நோயாளிக்கு எதுவும் அனுப்பாது: வாய்ப்புகள் தாவலிலிருந்து நீங்களே வாய்ப்பை வழங்குவீர்கள். | B: "Holds the slot" rendered இடத்தை நிறுத்தி ("stops/parks the place"); elsewhere a hold is ஒதுக்கீடு. "candidate" (the next waitlisted patient) is வேட்பாளர், the word for an election candidate. Choose everyday wording for a patient. | |
| `backfill.confirm.send.title` | Send the offer to this candidate? | இந்த வேட்பாளருக்கு வாய்ப்பை அனுப்பவா? | no issue found | |
| `backfill.confirm.skip.body` | Passes over the current candidate and moves to the next one on the waitlist. Nothing is sent. | தற்போதைய வேட்பாளரைத் தாண்டி, காத்திருப்புப் பட்டியலில் அடுத்தவருக்குச் செல்கிறது. எதுவும் அனுப்பப்படாது. | no issue found | |
| `backfill.confirm.skip.title` | Skip this candidate? | இந்த வேட்பாளரைத் தவிர்க்கவா? | no issue found | |
| `backfill.confirm.stop.body` | Ends the run for this slot. No offer is made and nobody else is considered. | இந்த நேர இடத்திற்கான ஓட்டத்தை முடிக்கிறது. வாய்ப்பு எதுவும் வழங்கப்படாது; வேறு யாரும் பரிசீலிக்கப்படமாட்டார்கள். | no issue found | |
| `backfill.confirm.stop.title` | Stop the run? | ஓட்டத்தை நிறுத்தவா? | no issue found | |
| `backfill.current` | Current | தற்போதையவர் | no issue found | |
| `backfill.currentYes` | Current candidate | தற்போதைய வேட்பாளர் | no issue found | |
| `backfill.decided.send` | The offer was recorded for the candidate. Finished: {{finished}}. | வேட்பாளருக்கான வாய்ப்பு பதிவானது. முடிந்ததா: {{finished}}. | no issue found | |
| `backfill.decided.skip` | The candidate was skipped. Finished: {{finished}}. | வேட்பாளர் தவிர்க்கப்பட்டார். முடிந்ததா: {{finished}}. | no issue found | |
| `backfill.decided.stop` | The run was stopped. Finished: {{finished}}. | ஓட்டம் நிறுத்தப்பட்டது. முடிந்ததா: {{finished}}. | no issue found | |
| `backfill.finished` | Finished | முடிந்தது | no issue found | |
| `backfill.hold` | Hold | நிறுத்தம் | C: "Hold" rendered நிறுத்தம் ("a halt"). | |
| `backfill.noRun` | There is no offer run for this slot yet. | இந்த நேர இடத்திற்கு இன்னும் வாய்ப்பு ஓட்டம் இல்லை. | no issue found | |
| `backfill.noTask` | None raised | எதுவும் எழுப்பப்படவில்லை | no issue found | |
| `backfill.noWaiting` | Nobody on the waitlist matches this slot. | இந்த இடத்திற்குப் பொருந்தும் யாரும் காத்திருப்புப் பட்டியலில் இல்லை. | C: "slot" is இடம் here and நேர இடம் elsewhere. | |
| `backfill.notConfigured` | Offer runs are not set up yet. Nothing was started or changed. | வாய்ப்பு ஓட்டங்கள் இன்னும் அமைக்கப்படவில்லை. எதுவும் தொடங்கப்படவில்லை அல்லது மாற்றப்படவில்லை. | no issue found | |
| `backfill.notWaitingForThis` | The run is not waiting for that. Refresh and look again. | ஓட்டம் அதற்காகக் காத்திருக்கவில்லை. புதுப்பித்து மீண்டும் பாருங்கள். | no issue found | |
| `backfill.note` | Nothing is sent to a patient from here. Send holds the slot for the current candidate and records the offer; you deliver it yourself from the Offers tab. | இங்கிருந்து நோயாளிக்கு எதுவும் அனுப்பப்படாது. அனுப்பு என்பது தற்போதைய வேட்பாளருக்கு இடத்தை நிறுத்தி வாய்ப்பைப் பதிவு செய்யும்; வாய்ப்புகள் தாவலிலிருந்து நீங்களே அதை வழங்குவீர்கள். | no issue found | |
| `backfill.prepare` | Prepare offer for this slot | இந்த நேர இடத்திற்கு வாய்ப்பைத் தயார் செய் | no issue found | |
| `backfill.prepareBody` | Starts the run for this slot: the waitlist is ranked by clinical priority and the run stops at the first candidate for you to decide. Nothing is sent to anyone. | இந்த நேர இடத்திற்கான ஓட்டத்தைத் தொடங்குகிறது: காத்திருப்புப் பட்டியல் மருத்துவ முன்னுரிமைப்படி வரிசைப்படுத்தப்பட்டு, நீங்கள் முடிவு செய்ய முதல் வேட்பாளரிடம் ஓட்டம் நிற்கும். யாருக்கும் எதுவும் அனுப்பப்படாது. | no issue found | |
| `backfill.prepareShort` | Prepare offer | வாய்ப்பைத் தயார் செய் | no issue found | |
| `backfill.rank` | rank {{rank}} | தரவரிசை {{rank}} | no issue found | |
| `backfill.ranked` | Ranked waitlist | வரிசைப்படுத்தப்பட்ட காத்திருப்புப் பட்டியல் | no issue found | |
| `backfill.reply.confidence` | Confidence | நம்பிக்கை | B: "Confidence" (how sure the engine is) is rendered நம்பிக்கை (belief, hope, trust). Same in review.confidence if present. | |
| `backfill.reply.engine` | Engine | இயந்திரம் | no issue found | |
| `backfill.reply.enter` | Enter this reply | இந்தப் பதிலை உள்ளிடு | no issue found | |
| `backfill.reply.enterBody` | Records the stored message you picked as the patient's reply. The red-flag rules screen it first; a red flag or a reply they cannot assess raises the nurse task and ends the run. | நீங்கள் தேர்ந்தெடுத்த சேமிக்கப்பட்ட செய்தியை நோயாளியின் பதிலாகப் பதிவு செய்கிறது. சிவப்புக் கொடி விதிகள் முதலில் பரிசோதிக்கும்; சிவப்புக் கொடி அல்லது மதிப்பிட முடியாத பதில் செவிலியர் பணியை எழுப்பி ஓட்டத்தை முடிக்கும். | A: "Red flag" is rendered literally as சிவப்புக் கொடி ("red flag", a flag that is red). It is the central safety term; a native clinician should confirm the phrase patients and nurses actually use (for example an "alarm sign" wording). Same term in backfill.reply.note, backfill.reply.reasons.red_flag, backfill.reply.savedOutcome.red_flag and others. | |
| `backfill.reply.enterTitle` | Enter this reply? | இந்தப் பதிலை உள்ளிடவா? | no issue found | |
| `backfill.reply.erased` | That message was erased and cannot be read. | அந்தச் செய்தி அழிக்கப்பட்டது; படிக்க முடியாது. | no issue found | |
| `backfill.reply.hide` | Hide | மறை | no issue found | |
| `backfill.reply.needsPerson` | A person must read it | ஒருவர் படிக்க வேண்டும் | no issue found | |
| `backfill.reply.noReply` | No reply came | பதில் வரவில்லை | no issue found | |
| `backfill.reply.noReplyBody` | Records that the patient did not reply in time. The run moves on. | நோயாளி நேரத்திற்குள் பதிலளிக்கவில்லை என்று பதிவு செய்கிறது. ஓட்டம் தொடரும். | no issue found | |
| `backfill.reply.noReplyTitle` | Record that no reply came? | பதில் வரவில்லை என்று பதிவு செய்யவா? | no issue found | |
| `backfill.reply.noSuggestion` | No suggestion: a person must read the reply. | பரிந்துரை இல்லை: ஒருவர் பதிலைப் படிக்க வேண்டும். | no issue found | |
| `backfill.reply.none` | No stored reply was found for this patient. The reply has to be stored first; this console cannot store one yet. If none came, choose No reply came. | இந்த நோயாளிக்கு சேமிக்கப்பட்ட பதில் எதுவும் கிடைக்கவில்லை. பதில் முதலில் சேமிக்கப்பட வேண்டும்; இந்தக் கணினியால் இன்னும் ஒன்றைச் சேமிக்க முடியாது. பதில் வரவில்லை என்றால், பதில் வரவில்லை என்பதைத் தேர்ந்தெடுங்கள். | no issue found | |
| `backfill.reply.notConfigured` | Reply reading is not set up yet. Read the message yourself. | பதிலைப் படித்தறிதல் இன்னும் அமைக்கப்படவில்லை. செய்தியை நீங்களே படியுங்கள். | no issue found | |
| `backfill.reply.notWaiting` | The run is not waiting for a reply. Refresh and look again. | ஓட்டம் பதிலுக்காகக் காத்திருக்கவில்லை. புதுப்பித்து மீண்டும் பாருங்கள். | no issue found | |
| `backfill.reply.note` | Type what the patient told you, or pick a reply that was stored earlier. A suggested reading is only a suggestion: you decide. The red-flag rules read the reply first. | நோயாளி உங்களிடம் சொன்னதை தட்டச்சு செய்யுங்கள், அல்லது முன்பு சேமிக்கப்பட்ட பதிலைத் தேர்ந்தெடுங்கள். பரிந்துரைக்கப்பட்ட புரிதல் ஒரு பரிந்துரை மட்டுமே: நீங்களே முடிவு செய்கிறீர்கள். சிவப்புக் கொடி விதிகள் முதலில் பதிலைப் படிக்கும். | no issue found | |
| `backfill.reply.outcome.clear` | Clear | தெளிவு | A: Safety screening outcome "Clear" (the rules found no red flag) is rendered தெளிவு ("clarity"); the lower-case form (backfill.reply.savedOutcome.clear) is the same. A nurse could read it as "the reply is clear / easy to understand" instead of "no red flag found". Highest priority for a native clinician. | |
| `backfill.reply.outcome.red_flag` | Red flag | சிவப்புக் கொடி | A: Red flag wording (see backfill.reply.enterBody). | |
| `backfill.reply.outcome.unassessable` | Could not be assessed | மதிப்பிட முடியவில்லை | no issue found | |
| `backfill.reply.pick` | Stored messages from this patient's appointments thread | இந்த நோயாளியின் சந்திப்புகள் இழையிலிருந்து சேமிக்கப்பட்ட செய்திகள் | no issue found | |
| `backfill.reply.read` | Read this message | இந்தச் செய்தியைப் படி | no issue found | |
| `backfill.reply.reasons.engine_failed` | The reading engine failed | படித்தறியும் இயந்திரம் தோல்வியடைந்தது | no issue found | |
| `backfill.reply.reasons.no_route` | No reading engine is available | படித்தறியும் இயந்திரம் எதுவும் இல்லை | no issue found | |
| `backfill.reply.reasons.not_a_patient_reply` | It is not a patient's reply | இது நோயாளியின் பதில் அல்ல | no issue found | |
| `backfill.reply.reasons.red_flag` | The rules found a red flag | விதிகள் சிவப்புக் கொடியைக் கண்டன | A: Red flag wording, see backfill.reply.enterBody. | |
| `backfill.reply.reasons.spend_ceiling` | The spend ceiling was reached | செலவு உச்சவரம்பு எட்டப்பட்டது | no issue found | |
| `backfill.reply.reasons.unassessable` | The rules could not assess it | விதிகளால் மதிப்பிட முடியவில்லை | no issue found | |
| `backfill.reply.save` | Save the reply | பதிலைச் சேமி | no issue found | |
| `backfill.reply.saveBody` | Stores what you typed as a message from the patient, encrypted, in their appointments thread. The red-flag rules read it first. Nothing is sent to the patient. Saving it does not yet enter it into the offer run. | நீங்கள் தட்டச்சு செய்ததை நோயாளியிடமிருந்து வந்த செய்தியாக, குறியாக்கம் செய்து, அவரது சந்திப்புகள் இழையில் சேமிக்கிறது. சிவப்புக் கொடி விதிகள் முதலில் அதைப் படிக்கும். நோயாளிக்கு எதுவும் அனுப்பப்படாது. சேமிப்பது இன்னும் வாய்ப்பு ஓட்டத்தில் அதை உள்ளிடாது. | B: "offer run" is rendered வாய்ப்பு ஓட்டம் ("opportunity flow"); "offer" and "run" are system terms with no everyday meaning here. Confirm the wording. | |
| `backfill.reply.saveConflict` | A different reply was already saved with this request. Check what you typed and save again. | இந்தக் கோரிக்கையுடன் வேறொரு பதில் ஏற்கனவே சேமிக்கப்பட்டது. நீங்கள் தட்டச்சு செய்ததைச் சரிபார்த்து மீண்டும் சேமிக்கவும். | no issue found | |
| `backfill.reply.saveInvalid` | The reply is blank or too long. Nothing was saved. | பதில் காலியாக உள்ளது அல்லது மிக நீளமாக உள்ளது. எதுவும் சேமிக்கப்படவில்லை. | no issue found | |
| `backfill.reply.saveNotFound` | This patient is not registered at this clinic. Nothing was saved. | இந்த நோயாளி இந்த மருத்துவமனையில் பதிவு செய்யப்படவில்லை. எதுவும் சேமிக்கப்படவில்லை. | no issue found | |
| `backfill.reply.saveTitle` | Save this reply? | இந்தப் பதிலைச் சேமிக்கவா? | no issue found | |
| `backfill.reply.saveUnavailable` | Replies cannot be stored yet (encryption is not set up). Nothing was saved. | பதில்களை இன்னும் சேமிக்க முடியாது (குறியாக்கம் அமைக்கப்படவில்லை). எதுவும் சேமிக்கப்படவில்லை. | no issue found | |
| `backfill.reply.saved` | The reply was saved and picked below. The rules' reading: | பதில் சேமிக்கப்பட்டு கீழே தேர்ந்தெடுக்கப்பட்டது. விதிகளின் வாசிப்பு: | B: "The rules' reading" rendered விதிகளின் வாசிப்பு ("the recital of the rules"); say that it is what the rules found. | |
| `backfill.reply.savedOutcome.clear` | clear | தெளிவு | no issue found | |
| `backfill.reply.savedOutcome.red_flag` | a red flag was found; a nurse task was raised | சிவப்புக் கொடி கண்டறியப்பட்டது; செவிலியர் பணி எழுப்பப்பட்டது | A: Red flag wording, see backfill.reply.enterBody. Also "raised a nurse task" is rendered செவிலியர் பணி எழுப்பப்பட்டது ("a nurse job was woken"): check that it reads as "a task was created for a nurse". | |
| `backfill.reply.savedOutcome.unassessable` | it could not be assessed; a nurse task was raised | மதிப்பிட முடியவில்லை; செவிலியர் பணி எழுப்பப்பட்டது | no issue found | |
| `backfill.reply.screenedNote` | The rules did not clear this reply. Entering it raises the nurse task and ends the run. | விதிகள் இந்தப் பதிலைத் தெளிவானது எனக் குறிக்கவில்லை. இதை உள்ளிட்டால் செவிலியர் பணி எழுப்பப்பட்டு ஓட்டம் முடியும். | A: "The rules did not clear this reply": "clear" (screening found no red flag) is rendered தெளிவானது ("clear" as in obvious or understandable). Safety outcome word; the same "clear" appears in the saved/outcome strings. | |
| `backfill.reply.screening` | Rules screening | விதிகள் பரிசோதனை | A: "Rules screening" rendered விதிகள் பரிசோதனை; see care.plans.status.screening. | |
| `backfill.reply.storedTitle` | Or pick a reply that was stored earlier | அல்லது முன்பு சேமிக்கப்பட்ட பதிலைத் தேர்ந்தெடுங்கள் | no issue found | |
| `backfill.reply.suggest` | Suggest a reading | ஒரு புரிதலைப் பரிந்துரை | no issue found | |
| `backfill.reply.suggested` | Suggested reading (a person confirms) | பரிந்துரைக்கப்பட்ட புரிதல் (ஒருவர் உறுதிசெய்வார்) | no issue found | |
| `backfill.reply.suggestion` | Suggested meaning | பரிந்துரைக்கப்பட்ட பொருள் | no issue found | |
| `backfill.reply.suggestions.accept` | Accepts | ஏற்கிறார் | no issue found | |
| `backfill.reply.suggestions.decline` | Declines | மறுக்கிறார் | no issue found | |
| `backfill.reply.suggestions.other_time` | Asks for another time | வேறு நேரம் கேட்கிறார் | no issue found | |
| `backfill.reply.suggestions.question` | Asks a question | கேள்வி கேட்கிறார் | no issue found | |
| `backfill.reply.suggestions.unclear` | Unclear | தெளிவில்லை | no issue found | |
| `backfill.reply.title` | The patient's reply | நோயாளியின் பதில் | no issue found | |
| `backfill.reply.typeLabel` | The patient's reply, as they gave it (by phone or in person) | நோயாளியின் பதில், அவர் கொடுத்தபடியே (தொலைபேசியில் அல்லது நேரில்) | no issue found | |
| `backfill.reply.typeTitle` | Type what the patient told you | நோயாளி உங்களிடம் சொன்னதைத் தட்டச்சு செய்யுங்கள் | no issue found | |
| `backfill.reply.why` | Why there is no suggestion | பரிந்துரை ஏன் இல்லை | no issue found | |
| `backfill.reply.yourCall` | Entering the reply records it as stored. What it means is your decision. | பதிலை உள்ளிடுவது அதைச் சேமிக்கப்பட்டதாகப் பதிவு செய்யும். அதன் பொருள் உங்கள் முடிவு. | no issue found | |
| `backfill.replyEntered` | The reply was entered. Finished: {{finished}}. | பதில் உள்ளிடப்பட்டது. முடிந்ததா: {{finished}}. | no issue found | |
| `backfill.started` | The run is started. Decide what to do with the first candidate. | ஓட்டம் தொடங்கப்பட்டது. முதல் வேட்பாளரைப் பற்றி முடிவு செய்யுங்கள். | no issue found | |
| `backfill.status` | Status | நிலை | no issue found | |
| `backfill.task` | Review task | மதிப்பாய்வுப் பணி | no issue found | |
| `backfill.title` | Offer run for slot | நேர இடத்திற்கான வாய்ப்பு ஓட்டம் | no issue found | |
| `backfill.waiting.none` | Nothing | எதுவுமில்லை | no issue found | |
| `backfill.waiting.reply_entry` | The patient's reply to be entered | நோயாளியின் பதிலை உள்ளிட வேண்டும் | no issue found | |
| `backfill.waiting.send_decision` | Your decision to send, skip or stop | அனுப்ப, தவிர்க்க அல்லது நிறுத்த உங்கள் முடிவு | no issue found | |
| `backfill.waitingFor` | Waiting for | காத்திருப்பது | no issue found | |
| `care.changed` | That changed in the meantime. Refresh and look again. | இடைப்பட்ட நேரத்தில் இது மாறிவிட்டது. புதுப்பித்து மீண்டும் பாருங்கள். | no issue found | |
| `care.gaps.ageNotSet` | The age at which an item is flagged overdue is not set, so nothing is flagged. | ஒரு உருப்படி தாமதமானது எனக் குறிக்கும் வயது அமைக்கப்படவில்லை; எனவே எதுவும் குறிக்கப்படாது. | no issue found | |
| `care.gaps.ageUnknown` | Unknown | தெரியாது | no issue found | |
| `care.gaps.alreadyResolved` | This barrier was already resolved. The list will refresh. | இந்தத் தடை ஏற்கனவே தீர்க்கப்பட்டது. பட்டியல் புதுப்பிக்கப்படும். | no issue found | |
| `care.gaps.barrier.childcare` | Childcare | குழந்தைப் பராமரிப்பு | no issue found | |
| `care.gaps.barrier.cost` | Cost | செலவு | no issue found | |
| `care.gaps.barrier.forgot` | Forgot | மறந்துவிட்டார் | no issue found | |
| `care.gaps.barrier.other` | Other | மற்றவை | no issue found | |
| `care.gaps.barrier.scheduling_conflict` | Scheduling conflict | நேர முரண்பாடு | no issue found | |
| `care.gaps.barrier.transport` | Transport | போக்குவரத்து | no issue found | |
| `care.gaps.barrierBody` | Records a barrier that stops this patient from attending. The type is chosen from a fixed list. | இந்த நோயாளி வருவதைத் தடுக்கும் ஒரு தடையைப் பதிவு செய்கிறது. வகை நிலையான பட்டியலிலிருந்து தேர்ந்தெடுக்கப்படும். | no issue found | |
| `care.gaps.barrierDone` | The barrier was recorded. | தடை பதிவானது. | no issue found | |
| `care.gaps.barrierKind` | Barrier | தடை | no issue found | |
| `care.gaps.col.action` | Action | செயல் | no issue found | |
| `care.gaps.col.age` | Age (days) | வயது (நாட்கள்) | no issue found | |
| `care.gaps.col.flag` | Flag | குறி | no issue found | |
| `care.gaps.col.item` | Item | உருப்படி | no issue found | |
| `care.gaps.col.median` | Median age (days) | இடைநிலை வயது (நாட்கள்) | no issue found | |
| `care.gaps.col.open` | Open items | திறந்த உருப்படிகள் | no issue found | |
| `care.gaps.col.source` | Source | மூலம் | no issue found | |
| `care.gaps.col.undated` | Without a date | தேதி இல்லாதவை | no issue found | |
| `care.gaps.dateRefused` | That date is refused: it would move backwards or fall in the future. Nothing was recorded. | அந்தத் தேதி மறுக்கப்பட்டது: அது பின்னோக்கி நகரும் அல்லது எதிர்காலத்தில் விழும். எதுவும் பதிவாகவில்லை. | C: "fall in the future" rendered எதிர்காலத்தில் விழும் ("fall into the future"); awkward. | |
| `care.gaps.doneBody` | Records the date this rule was last done. It can only move forward and never into the future. | இந்த விதி கடைசியாகச் செய்த தேதியைப் பதிவு செய்கிறது. இது முன்னோக்கி மட்டுமே நகரும்; எதிர்காலத்திற்கு ஒருபோதும் நகராது. | no issue found | |
| `care.gaps.doneOn` | Done on | செய்த தேதி | no issue found | |
| `care.gaps.doneRecorded` | The date was recorded. | தேதி பதிவானது. | no issue found | |
| `care.gaps.episodeBody` | Starts a care episode for the patient and attaches the rules the clinic has set for that kind of episode. | நோயாளிக்கான ஒரு பராமரிப்பு நிகழ்வைத் தொடங்கி, அந்த வகை நிகழ்வுக்கு மருத்துவமனை அமைத்த விதிகளை இணைக்கிறது. | no issue found | |
| `care.gaps.episodeCode` | Episode code | நிகழ்வுக் குறியீடு | no issue found | |
| `care.gaps.episodeDone` | The episode was started. | நிகழ்வு தொடங்கப்பட்டது. | no issue found | |
| `care.gaps.episodeNotConfigured` | Episode rules are not set up yet. Nothing was started. | நிகழ்வு விதிகள் இன்னும் அமைக்கப்படவில்லை. எதுவும் தொடங்கப்படவில்லை. | no issue found | |
| `care.gaps.episodeNotFound` | The clinic, the patient or the episode code was not found. Nothing was started. | மருத்துவமனை, நோயாளி அல்லது நிகழ்வுக் குறியீடு கிடைக்கவில்லை. எதுவும் தொடங்கப்படவில்லை. | no issue found | |
| `care.gaps.errorRate` | After-the-fact review error rate | பின்னாய்வு மறுஆய்வுப் பிழை விகிதம் | no issue found | |
| `care.gaps.itemGone` | That item could not be found, or it has already been dealt with. The list will refresh. | அந்த உருப்படி கிடைக்கவில்லை, அல்லது ஏற்கனவே கையாளப்பட்டது. பட்டியல் புதுப்பிக்கப்படும். | no issue found | |
| `care.gaps.markDone` | Mark done | செய்து முடிந்ததாகக் குறி | no issue found | |
| `care.gaps.noRows` | There are no items. | உருப்படிகள் இல்லை. | no issue found | |
| `care.gaps.noSummaries` | There are no summaries. | சுருக்கங்கள் இல்லை. | no issue found | |
| `care.gaps.notMeasured` | Not measured | அளக்கப்படவில்லை | no issue found | |
| `care.gaps.overdue` | Overdue | தாமதமானது | no issue found | |
| `care.gaps.recheck` | Re-check | மீண்டும் சரிபார் | no issue found | |
| `care.gaps.recheckBody` | This records that you re-checked the sample, under your name. It can be done once. | நீங்கள் மாதிரியை மீண்டும் சரிபார்த்தீர்கள் என்று உங்கள் பெயரில் பதிவாகும். இதை ஒரு முறை மட்டுமே செய்ய முடியும். | A: "sample" (a sampled decision to be re-checked by a second person) is rendered மாதிரி, which a reader will take as a laboratory specimen. Meaning risk in a clinical setting. | |
| `care.gaps.recheckTitle` | Mark this sample re-checked? | இந்த மாதிரியை மீண்டும் சரிபார்க்கப்பட்டதாகக் குறிக்கவா? | A: Same "sample" = specimen ambiguity. | |
| `care.gaps.rechecked` | The sample was marked re-checked. | மாதிரி மீண்டும் சரிபார்க்கப்பட்டதாகக் குறிக்கப்பட்டது. | A: Same "sample" = specimen ambiguity. | |
| `care.gaps.record` | Record | பதிவு செய் | no issue found | |
| `care.gaps.recordBarrier` | Record a barrier | தடையைப் பதிவு செய் | no issue found | |
| `care.gaps.resolve` | Resolve | தீர் | no issue found | |
| `care.gaps.resolveBody` | This closes the barrier, under your name. It can be done once. | இது உங்கள் பெயரில் தடையை மூடுகிறது. இதை ஒரு முறை மட்டுமே செய்ய முடியும். | no issue found | |
| `care.gaps.resolveTitle` | Resolve this barrier? | இந்தத் தடையைத் தீர்க்கவா? | no issue found | |
| `care.gaps.resolved` | The barrier was resolved. | தடை தீர்க்கப்பட்டது. | no issue found | |
| `care.gaps.rows` | Items | உருப்படிகள் | no issue found | |
| `care.gaps.sameDecider` | You made the decision this sample was drawn from, so someone else must re-check it. Nothing was recorded. | இந்த மாதிரி எடுக்கப்பட்ட முடிவை நீங்களே எடுத்தீர்கள்; எனவே வேறொருவர் இதை மீண்டும் சரிபார்க்க வேண்டும். எதுவும் பதிவாகவில்லை. | A: Same "sample" ambiguity; the sentence இந்த மாதிரி எடுக்கப்பட்ட முடிவை நீங்களே எடுத்தீர்கள் is hard to follow ("you made the decision this sample was taken from"). | |
| `care.gaps.source.after_the_fact_review` | After-the-fact review | பின்னாய்வு மறுஆய்வு | no issue found | |
| `care.gaps.source.overdue_recurrence` | Overdue recurring item | தாமதமான மீள்நிகழ்வு உருப்படி | no issue found | |
| `care.gaps.source.unresolved_barrier` | Unresolved barrier | தீர்க்கப்படாத தடை | no issue found | |
| `care.gaps.start` | Start | தொடங்கு | no issue found | |
| `care.gaps.startEpisode` | Start an episode | நிகழ்வைத் தொடங்கு | no issue found | |
| `care.gaps.startedOn` | Started on | தொடங்கிய தேதி | no issue found | |
| `care.gaps.summaries` | Summary by source | மூலம் வாரியான சுருக்கம் | no issue found | |
| `care.gaps.unknownLastDone` | Recurrence rules whose last-done date is unknown | கடைசியாகச் செய்த தேதி தெரியாத மீள்நிகழ்வு விதிகள் | no issue found | |
| `care.gaps.unreadable` | These sources could not be read: | இந்த மூலங்களைப் படிக்க இயலவில்லை: | no issue found | |
| `care.links.notes` | Notes | குறிப்புகள் | no issue found | |
| `care.links.title` | Care for this patient | இந்த நோயாளிக்கான பராமரிப்பு | no issue found | |
| `care.notReadable` | Not readable | படிக்க இயலவில்லை | no issue found | |
| `care.patientId` | Patient id | நோயாளி அடையாளம் | no issue found | |
| `care.patientIdBad` | That is not a patient id. | இது நோயாளி அடையாளம் அல்ல. | no issue found | |
| `care.plans.approve` | Approve | ஒப்புக்கொள் | no issue found | |
| `care.plans.approveTitle` | Approve this plan? | இந்தத் திட்டத்தை ஒப்புக்கொள்ளவா? | no issue found | |
| `care.plans.col.action` | Action | செயல் | no issue found | |
| `care.plans.col.created` | Created | உருவாக்கப்பட்டது | no issue found | |
| `care.plans.col.patient` | Patient | நோயாளி | no issue found | |
| `care.plans.col.plan` | Plan | திட்டம் | no issue found | |
| `care.plans.col.status` | Status | நிலை | no issue found | |
| `care.plans.decideBody` | Your decision is recorded under your name. Nothing runs before a plan is approved. | உங்கள் முடிவு உங்கள் பெயரில் பதிவாகும். திட்டம் ஒப்புக்கொள்ளப்படும் வரை எதுவும் இயங்காது. | no issue found | |
| `care.plans.decided` | Recorded. The plan is now waiting for: {{waiting}}. | பதிவானது. திட்டம் இப்போது இதற்காகக் காத்திருக்கிறது: {{waiting}}. | no issue found | |
| `care.plans.empty` | There are no care plans. | பராமரிப்புத் திட்டங்கள் இல்லை. | no issue found | |
| `care.plans.notConfigured` | Care plans are not set up yet. Nothing was written. | பராமரிப்புத் திட்டங்கள் இன்னும் அமைக்கப்படவில்லை. எதுவும் எழுதப்படவில்லை. | no issue found | |
| `care.plans.note` | A plan text is never shown here. A doctor writes a plan, the rules screen it, and it waits for a doctor to approve it. Nothing runs before then. | திட்ட உரை இங்கு ஒருபோதும் காட்டப்படாது. ஒரு மருத்துவர் திட்டத்தை எழுதுகிறார், விதிகள் அதைப் பரிசோதிக்கின்றன, பிறகு ஒரு மருத்துவர் ஒப்புக்கொள்ளக் காத்திருக்கும். அதற்கு முன் எதுவும் இயங்காது. | no issue found | |
| `care.plans.reject` | Reject | நிராகரி | no issue found | |
| `care.plans.rejectTitle` | Reject this plan? | இந்தத் திட்டத்தை நிராகரிக்கவா? | no issue found | |
| `care.plans.send` | Submit plan | திட்டத்தைச் சமர்ப்பி | no issue found | |
| `care.plans.status.active` | Active | செயலில் | no issue found | |
| `care.plans.status.awaiting_confirmation` | Waiting for confirmation | உறுதிப்படுத்தலுக்குக் காத்திருக்கிறது | no issue found | |
| `care.plans.status.completed` | Completed | முடிந்தது | no issue found | |
| `care.plans.status.erased` | Erased | அழிக்கப்பட்டது | no issue found | |
| `care.plans.status.held` | Held for a person | ஒருவரின் பரிசீலனைக்காக நிறுத்தப்பட்டது | no issue found | |
| `care.plans.status.rejected` | Rejected | நிராகரிக்கப்பட்டது | no issue found | |
| `care.plans.status.screening` | Being screened | பரிசோதனையில் உள்ளது | A: "being screened" (the rules read the text for red flags) is rendered பரிசோதனையில் உள்ளது; பரிசோதனை is the word for a medical examination or test. A clinician could read that the patient is being tested. The same word appears in backfill.reply.screening and the "rules screen" strings. | |
| `care.plans.text` | Plan text | திட்ட உரை | no issue found | |
| `care.plans.waiting.confirmation` | a confirmation | ஒரு உறுதிப்படுத்தல் | no issue found | |
| `care.plans.waiting.none` | nothing | எதுவுமில்லை | no issue found | |
| `care.plans.waiting.result` | a result | ஒரு முடிவு | no issue found | |
| `care.plans.write` | Write a care plan | பராமரிப்புத் திட்டம் எழுது | B: Register: எழுது is the informal singular imperative; use the polite form. | |
| `care.plans.writeBody` | The rules screen the text first. It then waits for a doctor to approve it, and nothing runs before that. | விதிகள் முதலில் உரையைப் பரிசோதிக்கும். பிறகு ஒரு மருத்துவர் ஒப்புக்கொள்ளக் காத்திருக்கும்; அதற்கு முன் எதுவும் இயங்காது. | no issue found | |
| `care.plans.written` | The plan was submitted. It is now waiting for: {{waiting}}. | திட்டம் சமர்ப்பிக்கப்பட்டது. இப்போது இதற்காகக் காத்திருக்கிறது: {{waiting}}. | no issue found | |
| `care.results.addMeasure` | Add a measure | அளவீட்டைச் சேர் | no issue found | |
| `care.results.body` | This records a result for the patient. It cannot be edited afterwards. | இது நோயாளிக்கான ஒரு முடிவைப் பதிவு செய்கிறது. பின்னர் இதைத் திருத்த முடியாது. | A: "result" (a clinical result) is rendered முடிவு, the same word used for "decision" everywhere else. A reader cannot tell a test result from a decision. | |
| `care.results.change` | What kind of change is this? | இது எந்த வகை மாற்றம்? | no issue found | |
| `care.results.changeHint` | Only when this result replaces another. An amendment is a new measurement; a correction means the earlier value was wrong and starts a review. | இந்த முடிவு மற்றொன்றை மாற்றும்போது மட்டும். மேம்படுத்தல் என்பது புதிய அளவீடு; திருத்தம் என்பது முந்தைய மதிப்பு தவறானது என்று பொருள்படும், மதிப்பாய்வைத் தொடங்கும். | A: "amendment" is rendered மேம்படுத்தல் ("improvement / upgrade"), which is not what an amended result means. The difference between an amendment (a new measurement) and a correction (the earlier value was wrong, starts a review) is safety-relevant. | |
| `care.results.changeNone` | Not stated (the system works it out) | குறிப்பிடப்படவில்லை (அமைப்பு கண்டறியும்) | no issue found | |
| `care.results.changes.amendment` | Amendment | மேம்படுத்தல் | A: See care.results.changeHint: amendment rendered மேம்படுத்தல் ("improvement"). | |
| `care.results.changes.correction` | Correction | திருத்தம் | no issue found | |
| `care.results.code` | Result code | முடிவுக் குறியீடு | no issue found | |
| `care.results.done` | The result was recorded. | முடிவு பதிவானது. | A: "result" = முடிவு, same ambiguity as care.results.body. | |
| `care.results.doneWake` | The result was recorded. A waiting care plan will be woken. | முடிவு பதிவானது. காத்திருக்கும் பராமரிப்புத் திட்டம் எழுப்பப்படும். | B: "A waiting care plan will be woken" rendered literally (எழுப்பப்படும், "will be woken up"). | |
| `care.results.measureName` | Measure {{n}} name | அளவீடு {{n}} பெயர் | no issue found | |
| `care.results.measureValue` | Measure {{n}} value | அளவீடு {{n}} மதிப்பு | no issue found | |
| `care.results.measures` | Measures | அளவீடுகள் | no issue found | |
| `care.results.measuresHint` | Leave a value empty if it is not known; it is recorded as unknown, never as zero. | ஒரு மதிப்பு தெரியாவிட்டால் காலியாக விடுங்கள்; அது தெரியாதது எனப் பதிவாகும், பூஜ்யமாக அல்ல. | no issue found | |
| `care.results.notNumber` | A value must be a number or empty. | மதிப்பு ஒரு எண்ணாக அல்லது காலியாக இருக்க வேண்டும். | no issue found | |
| `care.results.note` | Results are append-only. To correct one, record a new result that names the one it replaces. | முடிவுகள் சேர்க்க மட்டுமே முடியும். ஒன்றைத் திருத்த, அது மாற்றும் முடிவைக் குறிப்பிடும் புதிய முடிவைப் பதிவு செய்யுங்கள். | B: "append-only" rendered சேர்க்க மட்டுமே முடியும் ("can only be added"); the idea that nothing is overwritten should be clear. | |
| `care.results.record` | Record a result | முடிவைப் பதிவு செய் | no issue found | |
| `care.results.save` | Save result | முடிவைச் சேமி | no issue found | |
| `care.results.supersedes` | Replaces result id (optional) | மாற்றப்படும் முடிவு அடையாளம் (விருப்பம்) | no issue found | |
| `care.results.validFrom` | Valid from | செல்லுபடியாகத் தொடங்குவது | no issue found | |
| `care.results.validTo` | Valid until (optional) | செல்லுபடியாகும் வரை (விருப்பம்) | no issue found | |
| `care.results.verified` | Verified | சரிபார்க்கப்பட்டது | no issue found | |
| `care.tab.gaps` | Care gaps | பராமரிப்பு இடைவெளிகள் | no issue found | |
| `care.tab.plans` | Care plans | பராமரிப்புத் திட்டங்கள் | no issue found | |
| `care.tab.results` | Results | முடிவுகள் | no issue found | |
| `care.tabs` | Care sections | பராமரிப்புப் பிரிவுகள் | no issue found | |
| `care.title` | Care | பராமரிப்பு | no issue found | |
| `claims.approve` | Approve | ஒப்புக்கொள் | no issue found | |
| `claims.approveBody` | Links the person to the confirmed record you choose. It is recorded under your name. | நீங்கள் தேர்ந்தெடுக்கும் உறுதிசெய்யப்பட்ட பதிவுடன் அந்த நபரை இணைக்கிறது. இது உங்கள் பெயரில் பதிவாகும். | B: "Approve onto the record" rendered with an awkward ... பதிவில் ஒப்புக்கொள்ளுங்கள்; the meaning (link this person to that record) should be checked. | |
| `claims.approveTitle` | Approve this claim? | இந்தக் கோரிக்கையை ஒப்புக்கொள்ளவா? | no issue found | |
| `claims.col.action` | Action | செயல் | no issue found | |
| `claims.col.received` | Received | பெறப்பட்டது | no issue found | |
| `claims.col.request` | Request | கோரிக்கை | no issue found | |
| `claims.empty` | There are no pending claims. | நிலுவையில் உள்ள கோரிக்கைகள் இல்லை. | no issue found | |
| `claims.needsPatient` | Choose the confirmed record to approve onto. Nothing was recorded. | ஒப்புக்கொள்ள உறுதிசெய்யப்பட்ட பதிவைத் தேர்ந்தெடுங்கள். எதுவும் பதிவாகவில்லை. | B: Same phrase as claims.approveBody. | |
| `claims.note` | Pending requests from people who registered on the portal to be linked to a record. The list shows only a request id and a time. Decide from what the person told you in person, and approve onto the confirmed record. | ஒரு பதிவுடன் இணைக்கக் கோரி வலைவாசலில் பதிவு செய்தவர்களின் நிலுவைக் கோரிக்கைகள். பட்டியல் கோரிக்கை அடையாளத்தையும் நேரத்தையும் மட்டும் காட்டும். அந்த நபர் நேரில் கூறியதை வைத்து முடிவு செய்து, உறுதிசெய்யப்பட்ட பதிவில் ஒப்புக்கொள்ளுங்கள். | B: Same phrase; "decide from what the person told you in person" is the identity check, so the meaning has to be exact. | |
| `claims.reject` | Reject | நிராகரி | no issue found | |
| `claims.rejectBody` | Rejects the request. Nobody is linked. It is recorded under your name. | கோரிக்கையை நிராகரிக்கிறது. யாரும் இணைக்கப்படமாட்டார்கள். இது உங்கள் பெயரில் பதிவாகும். | no issue found | |
| `claims.rejectTitle` | Reject this claim? | இந்தக் கோரிக்கையை நிராகரிக்கவா? | no issue found | |
| `claims.result.already_resolved` | This claim was already resolved. | இந்தக் கோரிக்கை ஏற்கனவே தீர்க்கப்பட்டது. | no issue found | |
| `claims.result.approved` | Approved. The person is linked to the record. | ஒப்புக்கொள்ளப்பட்டது. அந்த நபர் பதிவுடன் இணைக்கப்பட்டார். | no issue found | |
| `claims.result.claimant_already_bound` | This person is already linked to a record. Nothing was changed. | இந்த நபர் ஏற்கனவே ஒரு பதிவுடன் இணைந்துள்ளார். எதுவும் மாற்றப்படவில்லை. | no issue found | |
| `claims.result.consent_required` | The person has not given the required consent yet. Nothing was linked. | தேவையான சம்மதத்தை அந்த நபர் இன்னும் வழங்கவில்லை. யாரும் இணைக்கப்படவில்லை. | no issue found | |
| `claims.result.record_not_claimable` | That record cannot be claimed. Nothing was linked. | அந்தப் பதிவைக் கோர முடியாது. யாரும் இணைக்கப்படவில்லை. | no issue found | |
| `claims.result.rejected` | Rejected. | நிராகரிக்கப்பட்டது. | no issue found | |
| `claims.title` | Portal claims | வலைவாசல் கோரிக்கைகள் | no issue found | |
| `common.cancel` | Cancel | ரத்து செய் | no issue found | |
| `common.comingSoon` | Coming soon | விரைவில் வரும் | no issue found | |
| `common.emergencyBanner` | In an emergency call 108 | அவசரநிலையில் 108 ஐ அழைக்கவும் | no issue found | |
| `common.language` | Language | மொழி | no issue found | |
| `common.loading` | Loading… | ஏற்றுகிறது… | no issue found | |
| `common.moreExist` | More results exist than are shown. | காட்டப்பட்டதை விட அதிக முடிவுகள் உள்ளன. | no issue found | |
| `common.no` | No | இல்லை | no issue found | |
| `common.notConfigured` | This site is not configured yet. | இந்தத் தளம் இன்னும் அமைக்கப்படவில்லை. | no issue found | |
| `common.notFoundTitle` | Not found | கிடைக்கவில்லை | no issue found | |
| `common.refresh` | Refresh | புதுப்பி | no issue found | |
| `common.retry` | Try again | மீண்டும் முயற்சிக்கவும் | no issue found | |
| `common.signIn` | Sign in | உள்நுழைக | no issue found | |
| `common.signOut` | Sign out | வெளியேறுக | no issue found | |
| `common.signUp` | Sign up | பதிவு செய்க | no issue found | |
| `common.skipToContent` | Skip to main content | முதன்மை உள்ளடக்கத்திற்குச் செல்க | no issue found | |
| `common.staffSignIn` | Staff sign in | பணியாளர் உள்நுழைவு | no issue found | |
| `common.working` | Working… | செயல்படுகிறது… | no issue found; earlier AI pass flag (unnatural): Literal "it is functioning"; wording for a busy state should read as processing. | |
| `common.yes` | Yes | ஆம் | no issue found | |
| `dashboard.col.metric` | Metric | அளவீடு | no issue found | |
| `dashboard.col.state` | State | நிலை | no issue found | |
| `dashboard.col.value` | Value | மதிப்பு | no issue found | |
| `dashboard.empty` | There is no metrics data to show. | காட்ட அளவீட்டுத் தரவு இல்லை. | no issue found | |
| `dashboard.measured` | Measured | அளக்கப்பட்டவை | no issue found | |
| `dashboard.metric.adoption_rate` | Adoption rate | ஏற்பு விகிதம் | no issue found | |
| `dashboard.metric.after_the_fact_review_error_rate` | After-the-fact review error rate | பின்னாய்வு மறுஆய்வுப் பிழை விகிதம் | no issue found | |
| `dashboard.metric.answer_relevancy` | Answer relevancy | பதிலின் பொருத்தம் | no issue found | |
| `dashboard.metric.audit_log_completeness` | Audit log completeness | தணிக்கைப் பதிவு முழுமை | no issue found | |
| `dashboard.metric.automation_rate` | Automation rate | தானியக்க விகிதம் | no issue found | |
| `dashboard.metric.backfill_fill_rate` | Backfill fill rate | காலி இடம் நிரப்பும் விகிதம் | no issue found | |
| `dashboard.metric.barrier_resolution_rate` | Barrier resolution rate | தடை தீர்வு விகிதம் | no issue found | |
| `dashboard.metric.cache_hit_rate` | Cache hit rate | தற்காலிகச் சேமிப்பு வெற்றி விகிதம் | no issue found | |
| `dashboard.metric.canary_catch_rate` | Test case catch rate | சோதனை நிகழ்வு பிடிப்பு விகிதம் | no issue found | |
| `dashboard.metric.card_recall_at_3` | Card recall at 3 | 3 இல் அட்டை மீட்பு | C: "Card" (a protocol card) is rendered அட்டை (a physical card) and "recall" மீட்பு; "cost tail" and "golden set" are literal. Admin-only metric names. | |
| `dashboard.metric.care_gap_median_age` | Care gap median age | பராமரிப்பு இடைவெளி இடைநிலை வயது | no issue found | |
| `dashboard.metric.care_gap_open_count` | Open care gaps | திறந்த பராமரிப்பு இடைவெளிகள் | no issue found | |
| `dashboard.metric.context_recall` | Context recall | சூழல் மீட்பு | C: Machine-learning metric names (context recall, grounding rate, guardrail trigger accuracy, model dependency health, loop failure rate, engine agreement, tool call accuracy, cache hit rate and others in dashboard.metric.*) are translated word for word; admin-only, a technical reviewer should confirm or leave in English. | |
| `dashboard.metric.correct_abstention_rate` | Correct abstention rate | சரியான விலகல் விகிதம் | no issue found | |
| `dashboard.metric.cost_per_completed_task` | Cost per completed task | முடிந்த பணிக்கான செலவு | no issue found | |
| `dashboard.metric.cost_per_query` | Cost per query | ஒரு வினவலுக்கான செலவு | no issue found | |
| `dashboard.metric.cost_per_successful_workflow` | Cost per successful workflow | வெற்றிகரமான பணிப்பாய்வுக்கான செலவு | no issue found | |
| `dashboard.metric.cost_tail_top_5_percent` | Cost tail, top 5 percent | செலவு வால், முதல் 5 சதவீதம் | no issue found | |
| `dashboard.metric.dead_letter_count` | Failed job count | தோல்வியுற்ற பணிகளின் எண்ணிக்கை | no issue found | |
| `dashboard.metric.engine_agreement` | Engine agreement | இயந்திர உடன்பாடு | no issue found | |
| `dashboard.metric.error_rate` | Error rate | பிழை விகிதம் | no issue found | |
| `dashboard.metric.escalation_rate_and_accuracy` | Escalation rate and accuracy | உயர்நிலை அனுப்புதல் விகிதமும் துல்லியமும் | C: "escalation" rendered உயர்நிலை அனுப்புதல் ("sending to a higher level"); used in several dashboard and alert strings. | |
| `dashboard.metric.evaluation_spend` | Evaluation spend | மதிப்பீட்டுச் செலவு | no issue found | |
| `dashboard.metric.failure_multiplier` | Failure multiplier | தோல்விப் பெருக்கி | no issue found | |
| `dashboard.metric.fallback_and_abstention_spike` | Fallback and abstention spike | மாற்று வழி மற்றும் விலகல் அதிகரிப்பு | no issue found | |
| `dashboard.metric.false_routing_rate` | False routing rate | தவறான வழிப்படுத்தல் விகிதம் | no issue found | |
| `dashboard.metric.first_pass_success_rate` | First-pass success rate | முதல் முயற்சி வெற்றி விகிதம் | no issue found | |
| `dashboard.metric.followups_closed_vs_lost` | Follow-ups closed against lost | முடிந்த மற்றும் தவறவிட்ட தொடர்ச்சிகள் | no issue found | |
| `dashboard.metric.golden_set_vs_variance_band` | Golden set against the variance band | தங்கத் தொகுப்பு மற்றும் மாறுபாட்டு எல்லை | no issue found | |
| `dashboard.metric.grounding_rate` | Grounding rate | அடிப்படை ஆதார விகிதம் | no issue found | |
| `dashboard.metric.guardrail_trigger_accuracy` | Guardrail trigger accuracy | பாதுகாப்பு வரம்பு தூண்டல் துல்லியம் | no issue found | |
| `dashboard.metric.index_freshness_lag` | Index freshness lag | குறியீட்டுப் புதுமைத் தாமதம் | no issue found | |
| `dashboard.metric.intake_completion_rate` | Intake completion rate | உள்வாங்கல் முடிவு விகிதம் | no issue found | |
| `dashboard.metric.latency_external_wait` | Wait for an outside party | வெளியாருக்கான காத்திருப்பு | no issue found | |
| `dashboard.metric.latency_human_wait` | Wait for a person | ஒருவருக்கான காத்திருப்பு | no issue found | |
| `dashboard.metric.latency_p95_machine` | Machine latency, 95th percentile | இயந்திரத் தாமதம், 95வது சதவீதம் | no issue found | |
| `dashboard.metric.loop_failure_rate` | Loop failure rate | சுழற்சித் தோல்வி விகிதம் | no issue found | |
| `dashboard.metric.model_dependency_health` | Model dependency health | மாதிரி சார்புநிலை ஆரோக்கியம் | no issue found | |
| `dashboard.metric.no_show_rate` | No-show rate | வராதவர் விகிதம் | no issue found | |
| `dashboard.metric.priority_accuracy` | Priority accuracy | முன்னுரிமைத் துல்லியம் | no issue found | |
| `dashboard.metric.queue_depth_and_oldest_age` | Queue depth and oldest age | வரிசை ஆழமும் மிகப் பழைய வயதும் | no issue found | |
| `dashboard.metric.readiness_escalation_rate` | Readiness escalation rate | தயார்நிலை உயர்நிலை அனுப்புதல் விகிதம் | no issue found | |
| `dashboard.metric.red_flag_recall` | Red-flag recall | சிவப்புக் கொடி மீட்பு | A: "Red-flag recall" (how many red flags the system catches, i.e. sensitivity) is rendered சிவப்புக் கொடி மீட்பு; மீட்பு means rescue or retrieval. A safety metric an administrator reads must not be ambiguous. Same in urgent_case_recall. | |
| `dashboard.metric.red_flag_task_overdue` | Red-flag tasks overdue | தாமதமான சிவப்புக் கொடிப் பணிகள் | no issue found | |
| `dashboard.metric.referral_to_appointment_time` | Time from referral to appointment | பரிந்துரையிலிருந்து சந்திப்பு வரையிலான நேரம் | no issue found | |
| `dashboard.metric.review_queue_age` | Review queue age | மதிப்பாய்வு வரிசை வயது | no issue found | |
| `dashboard.metric.rollback_frequency` | Rollback frequency | பின்னோக்கல் அதிர்வெண் | no issue found | |
| `dashboard.metric.safety_net_fail_closed_rate` | Safety net fail-closed rate | பாதுகாப்பு வலை மூடுதல் விகிதம் | B: "fail-closed" (the system stops safely when it fails) is rendered மூடுதல் ("closing"), which loses the meaning. | |
| `dashboard.metric.step_efficiency` | Step efficiency | படித் திறன் | no issue found | |
| `dashboard.metric.task_completion_rate` | Task completion rate | பணி முடிவு விகிதம் | no issue found | |
| `dashboard.metric.tool_call_accuracy` | Tool call accuracy | கருவி அழைப்புத் துல்லியம் | no issue found | |
| `dashboard.metric.tool_status_mix` | Tool status mix | கருவி நிலைக் கலவை | no issue found | |
| `dashboard.metric.uptime` | Uptime | இயங்கும் நேரம் | no issue found | |
| `dashboard.metric.urgent_case_recall` | Urgent case recall | அவசர நிகழ்வு மீட்பு | A: "recall" (sensitivity) rendered மீட்பு (rescue); see dashboard.metric.red_flag_recall. | |
| `dashboard.metric.urgent_cases_seen_within_target` | Urgent cases seen within target | இலக்கிற்குள் பார்க்கப்பட்ட அவசர நிகழ்வுகள் | no issue found | |
| `dashboard.metric.voice_safety_escalation_rate` | Voice safety escalation rate | குரல் பாதுகாப்பு உயர்நிலை அனுப்புதல் விகிதம் | no issue found | |
| `dashboard.metric.voice_scope_adherence_rate` | Voice scope adherence rate | குரல் வரம்பு பின்பற்றல் விகிதம் | no issue found | |
| `dashboard.metric.wrong_merge_rate` | Wrong merge rate | தவறான இணைப்பு விகிதம் | no issue found | |
| `dashboard.noneMeasured` | No metric is measured. | எந்த அளவீடும் அளக்கப்படவில்லை. | no issue found | |
| `dashboard.noneMissing` | Every metric has a query behind it. | ஒவ்வொரு அளவீட்டிற்கும் ஒரு வினவல் உள்ளது. | no issue found | |
| `dashboard.notMeasured` | Not measured | அளக்கப்படாதவை | no issue found | |
| `dashboard.notReadable` | Not readable | படிக்க இயலவில்லை | no issue found | |
| `dashboard.state.measured` | Measured | அளக்கப்பட்டது | no issue found | |
| `dashboard.state.unreadable` | Not readable | படிக்க இயலவில்லை | no issue found | |
| `dashboard.title` | Dashboard | கட்டுப்பாட்டுப் பலகை | no issue found | |
| `dev.capture` | Capture claim structure | கோரிக்கை அமைப்பைப் பெறுக | no issue found | |
| `dev.failed` | Could not read the session. | அமர்வைப் படிக்க முடியவில்லை. | no issue found | |
| `dev.note` | Development builds only. Shows the structure of aal, amr and iat with values blanked. It never shows the token. | உருவாக்கப் பதிப்புகளில் மட்டும். aal, amr, iat ஆகியவற்றின் அமைப்பை மதிப்புகள் மறைக்கப்பட்டு காட்டும். டோக்கனைக் காட்டாது. | no issue found | |
| `dev.refresh` | Refresh session, then capture | அமர்வைப் புதுப்பித்து, பின் பெறுக | no issue found | |
| `dev.reveal` | Also show enum-like values (the aal level and amr method names) | enum போன்ற மதிப்புகளையும் காட்டு (aal நிலை, amr முறைப் பெயர்கள்) | no issue found | |
| `dev.title` | Developer: claim structure | உருவாக்குநர்: கிளெய்ம் அமைப்பு | no issue found; earlier AI pass: corrected (meaning problem) | |
| `devMfa.banner` | DEVELOPMENT ONLY: the second factor (authenticator) is skipped in this build. Do not use it with real patients. It must be switched off before any real use. | வளர்ச்சிக்கு மட்டும்: இந்த உருவாக்கத்தில் இரண்டாம் காரணி (அங்கீகரிப்பு செயலி) தவிர்க்கப்படுகிறது. உண்மையான நோயாளிகளுடன் பயன்படுத்த வேண்டாம். உண்மையான பயன்பாட்டிற்கு முன் இதை அணைக்க வேண்டும். | no issue found | |
| `env.live` | LIVE | LIVE | kept in English by design | |
| `env.test` | TEST | TEST | kept in English by design | |
| `env.unknown` | Environment unknown | சூழல் தெரியவில்லை | no issue found | |
| `erasure.actionsOff` | Asking, approving and cancelling are switched off until the second-factor check is confirmed. The list below is read-only. | கேட்பது, ஒப்புக்கொள்வது, ரத்து செய்வது ஆகியவை இரண்டாம் காரணி சரிபார்ப்பு உறுதிப்படுத்தப்படும் வரை அணைக்கப்பட்டுள்ளன. கீழே உள்ள பட்டியல் படிக்க மட்டுமே. | no issue found | |
| `erasure.alreadyOpen` | This patient already has an open request. | இந்த நோயாளிக்கு ஏற்கனவே ஒரு திறந்த கோரிக்கை உள்ளது. | no issue found | |
| `erasure.approve` | Approve | ஒப்புக்கொள் | no issue found | |
| `erasure.approveBody` | Approving erases the identity now. | ஒப்புக்கொண்டால் அடையாளம் இப்போதே அழிக்கப்படும். | no issue found | |
| `erasure.approveConflict` | This cannot be approved now (the patient may be part of a live merge, or the request changed). Nothing was erased. | இதை இப்போது ஒப்புக்கொள்ள முடியாது (நோயாளி நேரடி இணைப்பில் இருக்கலாம், அல்லது கோரிக்கை மாறியிருக்கலாம்). எதுவும் அழிக்கப்படவில்லை. | B: "live merge" rendered நேரடி இணைப்பு ("direct connection / link"); a merge of two records should not read as a connection or link (also used for linking a portal person to a record). | |
| `erasure.approveForbidden` | You cannot approve this request (you may be the person who asked, or not front desk of this clinic). Nothing was erased. | இந்தக் கோரிக்கையை நீங்கள் ஒப்புக்கொள்ள முடியாது (நீங்களே கேட்டவராக இருக்கலாம், அல்லது இந்த மருத்துவமனையின் முன்பணியாளர் அல்ல). எதுவும் அழிக்கப்படவில்லை. | C: "front desk" rendered முன்பணியாளர், an unusual compound; check it is the word the clinic uses. | |
| `erasure.approveTitle` | Approve this erasure? | இந்த அழிப்பை ஒப்புக்கொள்ளவா? | no issue found | |
| `erasure.approved` | Approved. The identity was erased. | ஒப்புக்கொள்ளப்பட்டது. அடையாளம் அழிக்கப்பட்டது. | no issue found | |
| `erasure.cancel` | Cancel the request | கோரிக்கையை ரத்து செய் | no issue found | |
| `erasure.cancelBody` | Cancelling closes the request. Nothing is erased. | ரத்து செய்தால் கோரிக்கை மூடப்படும். எதுவும் அழிக்கப்படாது. | no issue found | |
| `erasure.cancelConflict` | This request cannot be cancelled now. Nothing was changed. | இந்தக் கோரிக்கையை இப்போது ரத்து செய்ய முடியாது. எதுவும் மாற்றப்படவில்லை. | no issue found | |
| `erasure.cancelForbidden` | You cannot cancel this request (it may no longer be open). Nothing was changed. | இந்தக் கோரிக்கையை நீங்கள் ரத்து செய்ய முடியாது (அது இனி திறந்ததாக இல்லாமல் இருக்கலாம்). எதுவும் மாற்றப்படவில்லை. | no issue found | |
| `erasure.cancelTitle` | Cancel this request? | இந்தக் கோரிக்கையை ரத்து செய்யவா? | no issue found | |
| `erasure.cancelled` | The request was cancelled. | கோரிக்கை ரத்து செய்யப்பட்டது. | no issue found | |
| `erasure.codeHint` | You may be asked for a recent authenticator code. | சமீபத்திய அங்கீகார குறியீடு கேட்கப்படலாம். | no issue found | |
| `erasure.col.action` | Action | செயல் | no issue found | |
| `erasure.col.created` | Asked | கேட்கப்பட்டது | no issue found | |
| `erasure.col.decided` | Decided | முடிவு | no issue found | |
| `erasure.col.patient` | Patient | நோயாளி | no issue found | |
| `erasure.col.reason` | Reason | காரணம் | no issue found | |
| `erasure.col.request` | Request | கோரிக்கை | no issue found | |
| `erasure.col.requestedBy` | Asked by | கேட்டவர் | no issue found | |
| `erasure.col.status` | Status | நிலை | no issue found | |
| `erasure.empty` | No requests. | கோரிக்கைகள் இல்லை. | no issue found | |
| `erasure.erasesBody` | Erasing removes the patient's encrypted name, date of birth and phone number. There is no way to bring them back from this screen. | அழிப்பு நோயாளியின் குறியாக்கம் செய்யப்பட்ட பெயர், பிறந்த தேதி, தொலைபேசி எண்ணை நீக்குகிறது. இந்தத் திரையிலிருந்து அவற்றைத் திரும்பப் பெற வழி இல்லை. | no issue found | |
| `erasure.hasMore` | There are more requests than are shown. | காட்டப்பட்டதை விட அதிகமான கோரிக்கைகள் உள்ளன. | no issue found | |
| `erasure.keepsBody` | It keeps the request and its audit rows (they are never deleted), the patient's record number, and the keyed fingerprints used for matching, which ignore an erased record. Messages are not erased by this step. | இது கோரிக்கையையும் அதன் தணிக்கை வரிகளையும் (அவை ஒருபோதும் நீக்கப்படாது), நோயாளியின் பதிவு எண்ணையும், பொருத்தத்துக்கான சாவி கைரேகைகளையும் வைத்திருக்கும்; பொருத்தம் அழிக்கப்பட்ட பதிவைப் புறக்கணிக்கும். இந்தப் படியில் செய்திகள் அழிக்கப்படாது. | A: Privacy meaning: "keyed fingerprints" (a hashed match key) is rendered சாவி கைரேகைகள், which reads as biometric fingerprints being kept. It must say that a one-way code used only for matching is kept, and that it ignores an erased record. | |
| `erasure.listTitle` | Requests | கோரிக்கைகள் | no issue found | |
| `erasure.note` | Erasing a patient's identity takes two front desk people: one asks and a different one approves. Each step needs a recent authenticator code. | ஒரு நோயாளியின் அடையாளத்தை அழிக்க இரண்டு முன்பணியாளர்கள் தேவை: ஒருவர் கேட்கிறார், வேறொருவர் ஒப்புக்கொள்கிறார். ஒவ்வொரு படிக்கும் சமீபத்திய அங்கீகார குறியீடு தேவை. | no issue found | |
| `erasure.nothingNew` | Nothing new was done: the request was already decided or is no longer open. | புதிதாக எதுவும் செய்யப்படவில்லை: கோரிக்கை ஏற்கனவே முடிவு செய்யப்பட்டது அல்லது இனி திறந்ததாக இல்லை. | no issue found | |
| `erasure.off` | Switched off | அணைக்கப்பட்டது | no issue found | |
| `erasure.openRequests` | Open requests | திறந்த கோரிக்கைகள் | no issue found | |
| `erasure.otherMustApprove` | A different person must approve. | வேறொருவர் ஒப்புக்கொள்ள வேண்டும். | no issue found | |
| `erasure.patientNotFound` | This patient could not be found at this clinic. Nothing was sent. | இந்த நோயாளி இந்த மருத்துவமனையில் கிடைக்கவில்லை. எதுவும் அனுப்பப்படவில்லை. | no issue found | |
| `erasure.reason` | Reason (choose one) | காரணம் (ஒன்றைத் தேர்ந்தெடுங்கள்) | no issue found | |
| `erasure.reasonRefused` | That reason is not on the clinic's list. Nothing was sent. | அந்தக் காரணம் மருத்துவமனையின் பட்டியலில் இல்லை. எதுவும் அனுப்பப்படவில்லை. | no issue found | |
| `erasure.reasonsNotSetUp` | The clinic's list of erasure reasons is not set up yet, so a request cannot be made from here. Nothing was sent. | மருத்துவமனையின் அழிப்புக் காரணங்களின் பட்டியல் இன்னும் அமைக்கப்படவில்லை, எனவே இங்கிருந்து கோரிக்கை செய்ய முடியாது. எதுவும் அனுப்பப்படவில்லை. | no issue found | |
| `erasure.requestBody` | Nothing is erased yet. A different front desk person must approve it, with their own authenticator code. | இன்னும் எதுவும் அழிக்கப்படவில்லை. வேறொரு முன்பணியாளர் தமது சொந்த அங்கீகார குறியீட்டுடன் ஒப்புக்கொள்ள வேண்டும். | no issue found | |
| `erasure.requestButton` | Ask for erasure | அழிப்புக்குக் கேள் | no issue found | |
| `erasure.requestConfirmTitle` | Ask for this erasure? | இந்த அழிப்பைக் கேட்கவா? | no issue found | |
| `erasure.requestIs` | Request | கோரிக்கை | no issue found | |
| `erasure.requestTitle` | Ask for an erasure | அழிப்புக்குக் கேள் | B: Register: imperative கேள் is the informal singular ("ask"); staff-facing buttons should use the polite form (கேளுங்கள்). | |
| `erasure.requested` | The request was made. A different front desk person must approve it. | கோரிக்கை செய்யப்பட்டது. வேறொரு முன்பணியாளர் ஒப்புக்கொள்ள வேண்டும். | no issue found | |
| `erasure.status.cancelled` | Cancelled | ரத்து செய்யப்பட்டது | no issue found | |
| `erasure.status.erased` | Erased | அழிக்கப்பட்டது | no issue found | |
| `erasure.status.requested` | Open | திறந்தது | B: "Open" (still waiting) is rendered திறந்தது ("it opened / was opened", past tense). Same in admin.grievances.status.open, alerts.stateOpen, portal.grievance.status.open and others; a status word should say "still open" (for example நிலுவையில்). | |
| `erasure.statusFilter` | Show | காட்டு | no issue found | |
| `erasure.title` | Erasure requests | அழிப்புக் கோரிக்கைகள் | no issue found | |
| `erasure.you` | You | நீங்கள் | no issue found | |
| `errorBoundary.body` | Reload the page, or go to the home screen. | பக்கத்தை மீண்டும் ஏற்றவும், அல்லது முகப்புத் திரைக்குச் செல்லவும். | no issue found | |
| `errorBoundary.home` | Go to the home screen | முகப்புத் திரைக்குச் செல் | B: Register: செல் / திற / திரும்பு are informal singular imperatives (also pages.backHome, patients.full.open, patients.openTitle). Staff and patient pages should use the polite form throughout. | |
| `errorBoundary.reload` | Reload the page | பக்கத்தை மீண்டும் ஏற்று | no issue found | |
| `errorBoundary.title` | This screen could not be shown | இந்தத் திரையைக் காட்ட முடியவில்லை | no issue found | |
| `identity.actions` | Actions | செயல்கள் | no issue found | |
| `identity.actionsSoon` | Approve and unmerge | ஒப்புதல் மற்றும் இணைப்பு நீக்கம் | no issue found | |
| `identity.all` | All | அனைத்தும் | no issue found | |
| `identity.approve` | Approve | ஒப்புதல் அளி | no issue found | |
| `identity.approveBody` | This links the two records. It asks for a fresh authenticator code first. | இது இரண்டு பதிவுகளையும் இணைக்கிறது. முதலில் புதிய அங்கீகாரக் குறியீடு கேட்கப்படும். | A: "merge" of two patient records and "link" use the same word (இணை / இணைப்பு) in identity.approveBody, identity.approved, identity.unmergeBody, identity.flagged and the portal claim strings. Merging two patients is high-consequence; the wording must make clear it is joining two records into one, not a link or connection. | |
| `identity.approveConflict` | That approval was refused. It may already be approved, or you may have proposed it. Refresh and try again. | அந்த ஒப்புதல் மறுக்கப்பட்டது. அது ஏற்கனவே அளிக்கப்பட்டிருக்கலாம், அல்லது நீங்களே முன்மொழிந்திருக்கலாம். புதுப்பித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `identity.approveTitle` | Approve this merge? | இந்த இணைப்புக்கு ஒப்புதல் அளிக்கவா? | no issue found | |
| `identity.approved` | The merge was approved. | இணைப்புக்கு ஒப்புதல் அளிக்கப்பட்டது. | no issue found | |
| `identity.back` | Back to merges | இணைப்புகளுக்குத் திரும்பு | no issue found | |
| `identity.col.activated` | Active since | செயலில் உள்ள நேரம் | no issue found | |
| `identity.col.approvedBy` | Approved by | ஒப்புதல் அளித்தவர் | no issue found | |
| `identity.col.candidate` | Candidate | பரிசீலனைக்குரிய பதிவு | no issue found; earlier AI pass: corrected (meaning problem) | |
| `identity.col.created` | Created | உருவாக்கப்பட்டது | no issue found | |
| `identity.col.master` | Kept record | வைத்திருக்கும் பதிவு | no issue found; earlier AI pass flag (ambiguous): Does not clearly say surviving/primary record, which matters for who is kept in a merge. | |
| `identity.col.matched` | Matched on | பொருந்திய விவரம் | no issue found | |
| `identity.col.merge` | Merge | இணைப்பு | no issue found | |
| `identity.col.other` | Possible duplicate of | எந்தப் பதிவின் சாத்தியமான நகல் | no issue found; earlier AI pass: corrected (meaning problem) | |
| `identity.col.patient` | Record | பதிவு | no issue found | |
| `identity.col.proposedBy` | Proposed by | முன்மொழிந்தவர் | no issue found | |
| `identity.col.reason` | Reason | காரணம் | no issue found | |
| `identity.col.resolved` | Resolved | தீர்க்கப்பட்டது | no issue found | |
| `identity.col.status` | Status | நிலை | no issue found | |
| `identity.col.subordinate` | Linked record | இணைக்கப்பட்ட பதிவு | no issue found | |
| `identity.col.unmerged` | Unmerged at | இணைப்பு நீக்கிய நேரம் | no issue found | |
| `identity.col.unmergedBy` | Unmerged by | இணைப்பை நீக்கியவர் | no issue found | |
| `identity.detail` | Merge | இணைப்பு | no issue found | |
| `identity.differentPerson` | A different person from the one who proposed this merge must approve it. | இந்த இணைப்பை முன்மொழிந்தவரிலிருந்து வேறொருவர் ஒப்புதல் அளிக்க வேண்டும். | no issue found | |
| `identity.dupReason.ambiguous` | Ambiguous | தெளிவற்றது | no issue found | |
| `identity.dupReason.near_match` | Near match | நெருங்கிய பொருத்தம் | no issue found | |
| `identity.dupStatus.pending` | Pending | நிலுவையில் | no issue found | |
| `identity.dupStatus.resolved` | Resolved | தீர்க்கப்பட்டது | no issue found | |
| `identity.duplicates` | Possible duplicates | சாத்தியமான நகல்கள் | no issue found | |
| `identity.emptyDuplicates` | There are no possible duplicates to show. | காட்டுவதற்குச் சாத்தியமான நகல்கள் இல்லை. | C: "possible duplicates" rendered சாத்தியமான நகல்கள் ("possible copies"); a native speaker should choose the usual wording for duplicate patient records. | |
| `identity.emptyMerges` | There are no merges to show. | காட்டுவதற்கு இணைப்புகள் இல்லை. | no issue found | |
| `identity.filter` | Show items that are | இந்த நிலையிலுள்ளவற்றைக் காட்டு | B: Register: காட்டு is the informal singular imperative. | |
| `identity.flagged` | {{n}} bookings, holds, waitlist entries and match-review rows were recorded while the link was active; a review task lists them, and nothing is undone. | இணைப்பு செயலில் இருந்தபோது {{n}} முன்பதிவுகள், ஒதுக்கீடுகள், காத்திருப்புப் பட்டியல் பதிவுகள் மற்றும் பொருத்த-மதிப்பாய்வு வரிசைகள் பதிவாகின; ஒரு மதிப்பாய்வுப் பணி அவற்றைப் பட்டியலிடுகிறது, எதுவும் மாற்றப்படாது. | no issue found; earlier AI pass flag (too-long): Very long run-on sentence stating what the system recorded; "rows" rendered as queues. | |
| `identity.lists` | Identity lists | அடையாளப் பட்டியல்கள் | no issue found | |
| `identity.mergeStatus.active` | Active | செயலில் | no issue found | |
| `identity.mergeStatus.awaiting_second_approval` | Awaiting second approval | இரண்டாவது ஒப்புதலுக்காகக் காத்திருக்கிறது | no issue found | |
| `identity.mergeStatus.unmerged` | Unmerged | இணைப்பு நீக்கப்பட்டது | no issue found | |
| `identity.merges` | Merges | இணைப்புகள் | no issue found | |
| `identity.none` | None | எதுவுமில்லை | no issue found | |
| `identity.openMerge` | Open merge | இணைப்பைத் திற | no issue found | |
| `identity.title` | Identity | அடையாளம் | no issue found | |
| `identity.unmerge` | Unmerge | இணைப்பை நீக்கு | no issue found | |
| `identity.unmergeBody` | This breaks the link between the two records. It asks for a fresh authenticator code first. | இது இரண்டு பதிவுகளுக்கிடையிலான இணைப்பை உடைக்கிறது. முதலில் புதிய அங்கீகாரக் குறியீடு கேட்கப்படும். | A: "breaks the link" (இணைப்பை உடைக்கிறது): same merge/link ambiguity, and உடை ("break") may read as damage rather than undo. | |
| `identity.unmergeConflict` | That could not be done. Refresh and try again. | அதைச் செய்ய முடியவில்லை. புதுப்பித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `identity.unmergeTitle` | Unmerge these records? | இந்தப் பதிவுகளின் இணைப்பை நீக்கவா? | no issue found | |
| `identity.unmerged` | The link was broken. | இணைப்பு உடைக்கப்பட்டது. | no issue found | |
| `landing.about.heading` | About the hospital | மருத்துவமனை பற்றி | no issue found | |
| `landing.account` | Account | கணக்கு | no issue found | |
| `landing.departments.heading` | Departments and doctors | துறைகள் மற்றும் மருத்துவர்கள் | no issue found | |
| `landing.emergencyLabel` | Emergency information | அவசரகாலத் தகவல் | no issue found | |
| `landing.faq.a1` | Choose Sign up, enter your mobile number, and we will send a one-time code to confirm it. | பதிவு செய்க என்பதைத் தேர்ந்தெடுத்து உங்கள் கைபேசி எண்ணை உள்ளிடுங்கள்; அதை உறுதிப்படுத்த ஒருமுறைக் குறியீடு அனுப்புவோம். | no issue found | |
| `landing.faq.a2` | Yes. Use the language menu at the top of any page to switch between English and Tamil. | ஆம். எந்தப் பக்கத்தின் மேலேயும் உள்ள மொழி தேர்வைப் பயன்படுத்தி ஆங்கிலம், தமிழ் இடையே மாறலாம். | no issue found | |
| `landing.faq.heading` | Frequently asked questions | அடிக்கடி கேட்கப்படும் கேள்விகள் | no issue found | |
| `landing.faq.q1` | How do I create an account? | கணக்கை எவ்வாறு உருவாக்குவது? | no issue found | |
| `landing.faq.q2` | Can I use the portal in Tamil? | தளத்தைத் தமிழில் பயன்படுத்தலாமா? | no issue found | |
| `landing.faq.q3` | How do I raise a concern about my data or my care? | என் தரவு அல்லது சிகிச்சை குறித்த கவலையை எவ்வாறு தெரிவிப்பது? | no issue found | |
| `landing.faq.q4` | How is my personal information used? | என் தனிப்பட்ட தகவல் எவ்வாறு பயன்படுத்தப்படுகிறது? | no issue found | |
| `landing.features.book` | Book an appointment | சந்திப்பு நேரத்தை முன்பதிவு செய்யுங்கள் | no issue found | |
| `landing.features.family` | Manage appointments for family members | குடும்ப உறுப்பினர்களுக்கான சந்திப்புகளை நிர்வகியுங்கள் | no issue found | |
| `landing.features.heading` | What you can do here | இங்கே நீங்கள் செய்யக்கூடியவை | no issue found | |
| `landing.features.language` | Use English or Tamil | ஆங்கிலம் அல்லது தமிழைப் பயன்படுத்துங்கள் | no issue found | |
| `landing.features.messages` | Send and read messages with the clinic | மருத்துவமனையுடன் செய்திகளை அனுப்பவும் படிக்கவும் | no issue found | |
| `landing.features.offers` | Receive appointment offers by SMS or email | எஸ்எம்எஸ் அல்லது மின்னஞ்சல் மூலம் சந்திப்பு வாய்ப்புகளைப் பெறுங்கள் | B: Content, not translation: the English promises offers "by SMS or email"; the project sends no SMS and the console says staff deliver offers by hand. The owner should confirm the promise before the Tamil is polished. | |
| `landing.features.records` | See your records after your first visit | முதல் வருகைக்குப் பிறகு உங்கள் பதிவுகளைப் பாருங்கள் | no issue found | |
| `landing.footer.branding` | Placeholder branding: the name, colours and logo are stand-ins until the hospital supplies its own. | பெயர், நிறங்கள், சின்னம் ஆகியவை தற்காலிகமானவை; மருத்துவமனை தனது சொந்தத்தை வழங்கும் வரை இவ்வாறே இருக்கும். | no issue found | |
| `landing.footer.grievance` | Grievance contact | குறைதீர்ப்புத் தொடர்பு | no issue found | |
| `landing.footer.privacyNotice` | Privacy notice | தனியுரிமை அறிவிப்பு | no issue found | |
| `landing.how.heading` | How it works | இது எவ்வாறு செயல்படுகிறது | no issue found | |
| `landing.how.step1Body` | Sign up with your mobile number. We send a one-time code to confirm it. | உங்கள் கைபேசி எண்ணுடன் பதிவு செய்யுங்கள். அதை உறுதிப்படுத்த ஒருமுறைக் குறியீடு அனுப்புவோம். | no issue found; earlier AI pass flag (unnatural): One-time code term is the standard OTP concept; confirm the phrase patients actually recognise. | |
| `landing.how.step1Title` | Create your account | உங்கள் கணக்கை உருவாக்குங்கள் | no issue found | |
| `landing.how.step2Body` | Choose a time that suits you, or accept a time the clinic offers. | உங்களுக்கு ஏற்ற நேரத்தைத் தேர்ந்தெடுங்கள், அல்லது மருத்துவமனை வழங்கும் நேரத்தை ஏற்றுக்கொள்ளுங்கள். | no issue found | |
| `landing.how.step2Title` | Book or accept an appointment | சந்திப்பை முன்பதிவு செய்யுங்கள் அல்லது ஏற்றுக்கொள்ளுங்கள் | no issue found | |
| `landing.how.step3Body` | Get messages and reminders about your visit in the portal. | உங்கள் வருகை குறித்த செய்திகளையும் நினைவூட்டல்களையும் தளத்தில் பெறுங்கள். | no issue found | |
| `landing.how.step3Title` | Stay in touch | தொடர்பில் இருங்கள் | no issue found | |
| `landing.location.address` | Address | முகவரி | no issue found | |
| `landing.location.heading` | Location and timings | இடம் மற்றும் நேரங்கள் | no issue found | |
| `landing.location.hours` | Opening hours | நேரங்கள் | no issue found | |
| `landing.location.phone` | Phone | தொலைபேசி | no issue found | |
| `landing.privacy.heading` | Our privacy promise | எங்கள் தனியுரிமை உறுதிமொழி | no issue found | |
| `landing.privacy.readNotice` | Read the privacy notice | தனியுரிமை அறிவிப்பைப் படிக்கவும் | no issue found | |
| `languages.en` | English | English | kept in English by design | |
| `languages.ta` | தமிழ் | தமிழ் | kept in English by design | |
| `medications.code` | Code | குறியீடு | no issue found | |
| `medications.codeHint` | A code from this clinic's own list. Letters, numbers, dots, dashes and underscores only. | இந்த மருத்துவமனையின் சொந்தப் பட்டியலில் உள்ள குறியீடு. எழுத்துகள், எண்கள், புள்ளிகள், கோடுகள், அடிக்கோடுகள் மட்டும். | C: Allowed characters for a code. | |
| `medications.col.action` | Action | செயல் | no issue found | |
| `medications.col.code` | Code | குறியீடு | no issue found | |
| `medications.col.from` | From | தொடக்கம் | no issue found | |
| `medications.col.recorded` | Recorded | பதிவு செய்யப்பட்டது | no issue found | |
| `medications.col.state` | State | நிலை | no issue found | |
| `medications.col.to` | To | முடிவு | no issue found | |
| `medications.col.verified` | Verified | சரிபார்க்கப்பட்டது | no issue found | |
| `medications.conflict` | That row was already replaced, or this request clashes with an earlier one. Reload and try again. | அந்த வரிசை ஏற்கெனவே மாற்றப்பட்டுவிட்டது, அல்லது இந்தக் கோரிக்கை முந்தையதுடன் முரண்படுகிறது. மீண்டும் ஏற்றி முயலவும். | C: Technical conflict message. | |
| `medications.correct` | Correct this entry | இந்தப் பதிவைத் திருத்து | A: "Correct this entry" rendered இந்தப் பதிவைத் திருத்து: for a recording mistake only. Check a clinician would read it as "I recorded this wrongly" and not as "change the treatment". Wording is item 35 on docs/design/clinician-sheet.md. | |
| `medications.correctBody` | Use this when the entry below was recorded wrongly. It adds a new row that replaces the row below. The old row stays on record, marked as replaced. | கீழே உள்ள பதிவு தவறாகப் பதிவு செய்யப்பட்டிருந்தால் இதைப் பயன்படுத்தவும். இது கீழே உள்ள வரிசையை மாற்றும் புதிய வரிசையைச் சேர்க்கிறது. பழைய வரிசை, மாற்றப்பட்டது எனக் குறிக்கப்பட்டு பதிவில் இருக்கும். | B: "Replaces the row below; the old row stays, marked replaced": check மாற்றும் (replace) versus "change". | |
| `medications.correctTitle` | Correct this medication entry? | இந்த மருந்துப் பதிவைத் திருத்தலாமா? | B: Confirmation question for a correction; check it against medications.correct. | |
| `medications.current` | Current | தற்போதையது | no issue found | |
| `medications.done` | The medication statement was recorded. | மருந்து அறிக்கை பதிவு செய்யப்பட்டது. | no issue found | |
| `medications.empty` | No medication statements are recorded for this patient. | இந்த நோயாளிக்கு மருந்து அறிக்கைகள் எதுவும் பதிவு செய்யப்படவில்லை. | no issue found | |
| `medications.invalid` | The code is not in this clinic's list, or the times are not valid. Nothing was recorded. | குறியீடு இந்த மருத்துவமனையின் பட்டியலில் இல்லை, அல்லது நேரங்கள் சரியாக இல்லை. எதுவும் பதிவு செய்யப்படவில்லை. | B: "Code not in the clinic's list, or times not valid. Nothing was recorded.": check. | |
| `medications.noEnd` | No end recorded | முடிவு நேரம் பதிவு செய்யப்படவில்லை | A: "No end recorded" rendered முடிவு நேரம் பதிவு செய்யப்படவில்லை: it must never read as "ongoing". Check. | |
| `medications.notSetUp` | This clinic's list of medication codes is not set up yet. Nothing was recorded. | இந்த மருத்துவமனையின் மருந்துக் குறியீடுகளின் பட்டியல் இன்னும் அமைக்கப்படவில்லை. எதுவும் பதிவு செய்யப்படவில்லை. | B: "The clinic's list of medication codes is not set up. Nothing was recorded.": check that "nothing was recorded" is unmistakable. | |
| `medications.note` | The medication statements recorded for this patient, newest first. Nothing is edited or deleted: a correction or a stop is a new row that replaces the old one. Codes are shown exactly as recorded. | இந்த நோயாளிக்காகப் பதிவு செய்யப்பட்ட மருந்து அறிக்கைகள், புதியவை முதலில். எதுவும் திருத்தப்படுவதோ நீக்கப்படுவதோ இல்லை: திருத்தம் அல்லது நிறுத்தம் என்பது பழைய வரிசையை மாற்றும் புதிய வரிசை. குறியீடுகள் பதிவு செய்யப்பட்டபடியே காட்டப்படுகின்றன. | B: Says nothing is edited or deleted, a correction is a new row. Check it is clear. | |
| `medications.record` | Record a medication | மருந்தைப் பதிவு செய் | no issue found | |
| `medications.recordBody` | This adds a new row to the patient's record. It cannot be edited afterwards; a later change is another new row. | இது நோயாளியின் பதிவில் புதிய வரிசையைச் சேர்க்கிறது. பிறகு இதைத் திருத்த முடியாது; பின்னர் ஏற்படும் மாற்றம் மற்றொரு புதிய வரிசையாகும். | B: "Cannot be edited afterwards": check it is clear that a later change is a new row. | |
| `medications.recordTitle` | Record a medication statement? | மருந்து அறிக்கையைப் பதிவு செய்யலாமா? | no issue found | |
| `medications.replaces` | Replaces | மாற்றுவது | no issue found | |
| `medications.save` | Record | பதிவு செய் | no issue found | |
| `medications.stop` | Stop this medicine | இந்த மருந்தை நிறுத்து | A: "Stop this medicine" rendered இந்த மருந்தை நிறுத்து: the patient stopped taking it. Check it cannot be read as "stop giving it now" as an instruction to the patient. Wording is item 35 on docs/design/clinician-sheet.md. | |
| `medications.stopBody` | This adds a new row with an end time for the medicine below. The old row stays on record, marked as replaced. Nothing else is changed. | இது கீழே உள்ள மருந்துக்கு முடிவு நேரத்துடன் ஒரு புதிய வரிசையைச் சேர்க்கிறது. பழைய வரிசை, மாற்றப்பட்டது எனக் குறிக்கப்பட்டு பதிவில் இருக்கும். வேறு எதுவும் மாறாது. | B: Adds a row with an end time; the old row stays; nothing else changes. Check clarity. | |
| `medications.stopDone` | The stop was recorded. | நிறுத்தம் பதிவு செய்யப்பட்டது. | C: நிறுத்தம் பதிவு செய்யப்பட்டது. | |
| `medications.stopSave` | Record stop | நிறுத்தத்தைப் பதிவு செய் | B: "Record stop" rendered நிறுத்தத்தைப் பதிவு செய்: check it reads as recording, not ordering. | |
| `medications.stopTitle` | Record that this medicine was stopped? | இந்த மருந்து நிறுத்தப்பட்டது எனப் பதிவு செய்யலாமா? | A: "Record that this medicine was stopped?" rendered இந்த மருந்து நிறுத்தப்பட்டது எனப் பதிவு செய்யலாமா?: it records a fact, it does not order a stop. Check that is clear. | |
| `medications.stoppedAt` | Stopped at | நிறுத்தப்பட்ட நேரம் | C: நிறுத்தப்பட்ட நேரம் (stopped at). | |
| `medications.stoppedAtHint` | The time the medicine was stopped. It is required. | மருந்து நிறுத்தப்பட்ட நேரம். இது கட்டாயம். | no issue found | |
| `medications.superseded` | Replaced by a later row | பிந்தைய வரிசையால் மாற்றப்பட்டது | B: "Replaced by a later row" rendered பிந்தைய வரிசையால் மாற்றப்பட்டது: check it does not read as "changed medicine". | |
| `medications.title` | Medications | மருந்துகள் | B: மருந்துகள் (medications) as a heading may read as a list of what the patient takes now, but it lists statements incl. stopped ones. | |
| `medications.validFrom` | From | தொடக்கம் | no issue found | |
| `medications.validTo` | To (leave empty if no end is recorded) | முடிவு (முடிவு நேரம் பதிவு செய்யப்படாவிட்டால் காலியாக விடவும்) | no issue found | |
| `medications.validToHint` | An empty end is saved as "no end recorded". It is not read as "still taking". | காலியான முடிவு "முடிவு பதிவு செய்யப்படவில்லை" என்று சேமிக்கப்படும். அது "இன்னும் எடுத்துக்கொள்ளப்படுகிறது" என்று கருதப்படாது. | A: The "still taking" statement was reworded without a gendered verb (இன்னும் எடுத்துக்கொள்ளப்படுகிறது, passive). A native clinician should confirm it reads naturally and clearly says an empty end is NOT read as ongoing. Item 36 on docs/design/clinician-sheet.md. | |
| `medications.verified` | Verified | சரிபார்க்கப்பட்டது | B: சரிபார்க்கப்பட்டது (verified): check who verified is not implied. | |
| `nav.access` | Break-glass | அவசரகால அணுகல் | no issue found | |
| `nav.admin` | Admin | நிர்வாகம் | no issue found | |
| `nav.alerts` | Alerts | எச்சரிக்கைகள் | no issue found | |
| `nav.audit` | Audit log | தணிக்கைப் பதிவு | no issue found | |
| `nav.care` | Care | பராமரிப்பு | no issue found | |
| `nav.claims` | Portal claims | வலைவாசல் கோரிக்கைகள் | no issue found | |
| `nav.dashboard` | Dashboard | கட்டுப்பாட்டுப் பலகை | no issue found | |
| `nav.erasure` | Erasure requests | அழிப்புக் கோரிக்கைகள் | no issue found | |
| `nav.home` | Home | முகப்பு | no issue found | |
| `nav.identity` | Identity | அடையாளம் | no issue found | |
| `nav.label` | Sections | பிரிவுகள் | no issue found | |
| `nav.none` | No sections are available to your role. | உங்கள் பங்கிற்குக் கிடைக்கும் பிரிவுகள் எதுவும் இல்லை. | no issue found | |
| `nav.notes` | Notes | குறிப்புகள் | no issue found | |
| `nav.patients` | Patients | நோயாளர்கள் | C: Spelling: நோயாளர்கள் / நோயாளர் here and in many titles, நோயாளி elsewhere. Choose one. | |
| `nav.readiness` | Visit readiness | வருகைத் தயார்நிலை | no issue found | |
| `nav.referrals` | Referrals | பரிந்துரைகள் | no issue found | |
| `nav.scheduling` | Scheduling | நேர அட்டவணை | no issue found; earlier AI pass flag (ambiguous): Timetable reads as a schedule table; a main navigation label should be unambiguous. | |
| `nav.tasks` | Tasks | பணிகள் | no issue found | |
| `notes.addend` | Add an addendum | இணைப்புக் குறிப்பைச் சேர் | no issue found | |
| `notes.addendBody` | Writes a new note that adds to the earlier one. The earlier note is not changed. | முந்தைய குறிப்பில் சேர்க்கும் புதிய குறிப்பை எழுதுகிறது. முந்தைய குறிப்பு மாற்றப்படுவதில்லை. | B: "addendum" (a new note that adds to an earlier one) is rendered இணைப்புக் குறிப்பு ("attachment/link note"); the same word (இணைப்பு) is used for link, merge and attachment. A clearer word for "additional note" is needed. Same in notes.note. | |
| `notes.addendumTo` | addendum to | இணைப்புக் குறிப்பு: | no issue found | |
| `notes.by` | by | எழுதியவர் | no issue found | |
| `notes.empty` | There are no notes for this patient. | இந்த நோயாளிக்குக் குறிப்புகள் இல்லை. | no issue found | |
| `notes.erased` | Erased | அழிக்கப்பட்டது | no issue found | |
| `notes.keysMissing` | Encryption keys are not configured. Nothing was written. | குறியாக்க விசைகள் அமைக்கப்படவில்லை. எதுவும் எழுதப்படவில்லை. | no issue found | |
| `notes.notFound` | The patient or the note was not found. Nothing was written. | நோயாளி அல்லது குறிப்பு கிடைக்கவில்லை. எதுவும் எழுதப்படவில்லை. | no issue found | |
| `notes.note` | Notes are never edited. A correction is an addendum: a new note that names the one it adds to. | குறிப்புகள் ஒருபோதும் திருத்தப்படுவதில்லை. திருத்தம் என்பது இணைப்புக் குறிப்பு: அது சேர்க்கும் குறிப்பைக் குறிப்பிடும் புதிய குறிப்பு. | B: Same "addendum" wording as notes.addendBody. | |
| `notes.open` | Show notes | குறிப்புகளைக் காட்டு | no issue found | |
| `notes.save` | Save | சேமி | no issue found | |
| `notes.saved` | The note was saved. | குறிப்பு சேமிக்கப்பட்டது. | no issue found | |
| `notes.text` | Note | குறிப்பு | no issue found | |
| `notes.title` | Notes | குறிப்புகள் | no issue found | |
| `notes.unreadable` | Not readable | படிக்க இயலவில்லை | no issue found | |
| `notes.write` | Write a note | குறிப்பு எழுது | no issue found | |
| `notes.writeBody` | Writes a new note for this patient. It cannot be edited afterwards. | இந்த நோயாளிக்கான புதிய குறிப்பை எழுதுகிறது. பின்னர் திருத்த முடியாது. | no issue found | |
| `offline.banner` | You are offline. Changes cannot be saved until the connection is back. | நீங்கள் இணைப்பில் இல்லை. இணைப்பு திரும்பும் வரை மாற்றங்களைச் சேமிக்க முடியாது. | no issue found | |
| `pages.backHome` | Back to the home page | முகப்புப் பக்கத்திற்குத் திரும்பு | no issue found | |
| `pages.privacyTitle` | Privacy notice | தனியுரிமை அறிவிப்பு | no issue found | |
| `pages.signInTitle` | Sign in | உள்நுழைக | no issue found | |
| `pages.signUpTitle` | Sign up | பதிவு செய்க | no issue found | |
| `patients.back` | Back to patients | நோயாளர்களுக்குத் திரும்பு | no issue found | |
| `patients.col.label` | Label | குறிப்பு | no issue found; earlier AI pass flag (ambiguous): Note/hint instead of label; withheld note text could be taken as clinical notes. | |
| `patients.col.matched` | Matched on | பொருந்திய விவரம் | no issue found | |
| `patients.col.record` | Record | பதிவு | no issue found | |
| `patients.col.verified` | Verified | சரிபார்க்கப்பட்டது | no issue found | |
| `patients.col.via` | Registered via | பதிவு செய்த வழி | no issue found | |
| `patients.confirm` | Confirm identity | அடையாளத்தை உறுதிப்படுத்து | no issue found | |
| `patients.confirmBody` | This records that you confirmed this patient's identity. It is kept under your name. | இந்த நோயாளரின் அடையாளத்தை நீங்கள் உறுதிப்படுத்தியதை இது பதிவு செய்கிறது. அது உங்கள் பெயரில் வைக்கப்படும். | C: Spelling: நோயாளர் here, நோயாளி elsewhere. Pick one spelling. | |
| `patients.confirmConflict` | That confirmation conflicts with an earlier request. Refresh and try again. | அந்த உறுதிப்படுத்தல் முந்தைய கோரிக்கையுடன் முரண்படுகிறது. புதுப்பித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `patients.confirmTitle` | Confirm this identity? | இந்த அடையாளத்தை உறுதிப்படுத்தவா? | no issue found | |
| `patients.dob` | Date of birth | பிறந்த தேதி | no issue found | |
| `patients.event.confirm` | Confirmed | உறுதிப்படுத்தப்பட்டது | no issue found | |
| `patients.event.revoke` | Confirmation revoked | உறுதிப்படுத்தல் திரும்பப் பெறப்பட்டது | no issue found | |
| `patients.full.audited` | Opening this shows the name, date of birth and phone, and is recorded in the audit log (who, role and which record, never the contents). | இதைத் திறந்தால் பெயர், பிறந்த தேதி, தொலைபேசி காட்டப்படும்; இது தணிக்கைப் பதிவில் பதிவாகும் (யார், பங்கு, எந்தப் பதிவு; உள்ளடக்கம் ஒருபோதும் இல்லை). | no issue found | |
| `patients.full.hide` | Hide the details | விவரங்களை மறை | no issue found | |
| `patients.full.noPhone` | Not recorded | பதிவாகவில்லை | no issue found | |
| `patients.full.open` | Open the full details | முழு விவரங்களைத் திற | no issue found | |
| `patients.full.rateLimited` | Too many records opened in a short time. Wait a moment and try again. | குறுகிய நேரத்தில் அதிகமான பதிவுகள் திறக்கப்பட்டன. சற்று காத்திருந்து மீண்டும் முயலுங்கள். | no issue found | |
| `patients.full.title` | Full details | முழு விவரங்கள் | no issue found | |
| `patients.full.unavailable` | The full details cannot be opened in this setup. Nothing was shown. | இந்த அமைப்பில் முழு விவரங்களைத் திறக்க முடியாது. எதுவும் காட்டப்படவில்லை. | no issue found | |
| `patients.invalidInput` | Check the date of birth and phone number. | பிறந்த தேதி மற்றும் தொலைபேசி எண்ணைச் சரிபார்க்கவும். | no issue found | |
| `patients.labelsWithheld` | Labels are withheld for these results. | இந்த முடிவுகளுக்கான குறிப்புகள் மறைக்கப்பட்டுள்ளன. | no issue found | |
| `patients.latestEvent` | Latest identity event | சமீபத்திய அடையாள நிகழ்வு | no issue found | |
| `patients.matchedKind.name_dob` | Name and date of birth | பெயர் மற்றும் பிறந்த தேதி | no issue found | |
| `patients.matchedKind.phone` | Phone | தொலைபேசி | no issue found | |
| `patients.mergedInto` | This record was merged into another. | இந்தப் பதிவு வேறொரு பதிவில் இணைக்கப்பட்டது. | no issue found | |
| `patients.name` | Full name | முழுப் பெயர் | no issue found | |
| `patients.noEvent` | None | எதுவுமில்லை | no issue found | |
| `patients.noMatch` | No matching record was found. | பொருந்தும் பதிவு எதுவும் கிடைக்கவில்லை. | no issue found | |
| `patients.notVerified` | Not verified | சரிபார்க்கப்படவில்லை | no issue found | |
| `patients.open` | Open | திற | no issue found | |
| `patients.openId` | Record id | பதிவு அடையாள எண் | no issue found | |
| `patients.openInvalid` | That is not a record id. | அது பதிவு அடையாள எண் அல்ல. | no issue found | |
| `patients.openMerged` | Open that record | அந்தப் பதிவைத் திற | no issue found | |
| `patients.openRecord` | Open record | பதிவைத் திற | no issue found | |
| `patients.openTitle` | Open a record by id | அடையாள எண்ணால் பதிவைத் திற | no issue found | |
| `patients.phone` | Phone number (optional) | தொலைபேசி எண் (விருப்பம்) | no issue found | |
| `patients.rateLimited` | Too many searches. Wait a moment and try again. | அதிகத் தேடல்கள். சற்று காத்திருந்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `patients.record` | Patient record | நோயாளர் பதிவு | no issue found | |
| `patients.register.button` | Register | பதிவு செய் | no issue found | |
| `patients.register.candidates` | Possible matching records: | பொருந்தக்கூடிய பதிவுகள்: | no issue found | |
| `patients.register.confirmBody` | This saves the name, date of birth and phone you typed, encrypted, at this clinic. If they match an existing record, that record is used instead of a new one. | நீங்கள் தட்டச்சு செய்த பெயர், பிறந்த தேதி, தொலைபேசியை இந்த மருத்துவமனையில் குறியாக்கம் செய்து சேமிக்கிறது. அவர் ஏற்கனவே உள்ள பதிவுடன் பொருந்தினால், புதிய பதிவுக்குப் பதிலாக அந்தப் பதிவு பயன்படுத்தப்படும். | no issue found | |
| `patients.register.confirmTitle` | Register this patient? | இந்த நோயாளியைப் பதிவு செய்யவா? | no issue found | |
| `patients.register.conflict` | A different person was already saved with this request. Check the details and register again. | இந்தக் கோரிக்கையுடன் வேறொரு நபர் ஏற்கனவே சேமிக்கப்பட்டார். விவரங்களைச் சரிபார்த்து மீண்டும் பதிவு செய்யுங்கள். | no issue found | |
| `patients.register.dob` | Date of birth to register | பதிவு செய்ய வேண்டியவரின் பிறந்த தேதி | no issue found | |
| `patients.register.hint` | Search first. Register only when the person is not found. The details are not kept on this page after saving. | முதலில் தேடுங்கள். நபர் கிடைக்காதபோது மட்டும் பதிவு செய்யுங்கள். சேமித்த பிறகு விவரங்கள் இந்தப் பக்கத்தில் இருக்காது. | no issue found | |
| `patients.register.invalid` | One of the fields is not valid. Check the date of birth and the phone number. Nothing was saved. | ஒரு புலம் சரியில்லை. பிறந்த தேதியையும் தொலைபேசி எண்ணையும் சரிபாருங்கள். எதுவும் சேமிக்கப்படவில்லை. | no issue found | |
| `patients.register.name` | Name to register | பதிவு செய்ய வேண்டிய பெயர் | no issue found | |
| `patients.register.notFound` | This clinic could not be found, or you are not front desk there. Nothing was saved. | இந்த மருத்துவமனை கிடைக்கவில்லை, அல்லது நீங்கள் இங்கே முன்பணியாளர் இல்லை. எதுவும் சேமிக்கப்படவில்லை. | no issue found | |
| `patients.register.outcome.created` | A new record was created. | புதிய பதிவு உருவாக்கப்பட்டது. | no issue found | |
| `patients.register.outcome.matched` | This person matched an existing record. | இந்த நபர் ஏற்கனவே உள்ள பதிவுடன் பொருந்தினார். | no issue found | |
| `patients.register.outcome.replayed` | This request had already been saved. Nothing new was created. | இந்தக் கோரிக்கை ஏற்கனவே சேமிக்கப்பட்டிருந்தது. புதிதாக எதுவும் உருவாக்கப்படவில்லை. | no issue found | |
| `patients.register.outcome.review` | Possible matches were found. Nothing was linked automatically; a person decides. | பொருந்தக்கூடியவை கண்டறியப்பட்டன. தானாக எதுவும் இணைக்கப்படவில்லை; ஒரு நபர் முடிவு செய்வார். | no issue found | |
| `patients.register.outcome.unknown` | The system answered, but the outcome is not one this screen knows. Open the record to check. | அமைப்பு பதிலளித்தது, ஆனால் முடிவு இந்தத் திரை அறியாத ஒன்று. பதிவைத் திறந்து சரிபாருங்கள். | no issue found | |
| `patients.register.phone` | Phone number to register (optional) | பதிவு செய்ய வேண்டிய தொலைபேசி எண் (விருப்பம்) | no issue found | |
| `patients.register.title` | Register a patient | நோயாளியைப் பதிவு செய் | no issue found | |
| `patients.register.unavailable` | Registering is not set up yet (encryption keys are not configured). Nothing was saved. | பதிவு செய்வது இன்னும் அமைக்கப்படவில்லை (குறியாக்க விசைகள் அமைக்கப்படவில்லை). எதுவும் சேமிக்கப்படவில்லை. | no issue found | |
| `patients.results` | Search results | தேடல் முடிவுகள் | no issue found | |
| `patients.revoke.accepted` | The request was accepted. Check the record for its current state. | கோரிக்கை ஏற்கப்பட்டது. பதிவின் தற்போதைய நிலையைச் சரிபார்க்கவும். | no issue found | |
| `patients.revoke.alert.not_configured` | No administrator alert was raised: the alert settings are not configured yet. | நிர்வாகி எச்சரிக்கை எழுப்பப்படவில்லை: எச்சரிக்கை அமைப்புகள் இன்னும் அமைக்கப்படவில்லை. | B: Settings not configured; check clarity. | |
| `patients.revoke.alert.not_for_this_reason` | No administrator alert was raised for this reason. | இந்தக் காரணத்திற்கு நிர்வாகி எச்சரிக்கை எழுப்பப்படவில்லை. | B: Check it says no alert, not that nothing was recorded. | |
| `patients.revoke.alert.raised` | An alert was sent to the administrators. | நிர்வாகிகளுக்கு எச்சரிக்கை அனுப்பப்பட்டது. | B: "An alert was sent to the administrators": check நிர்வாகிகள் is who the clinic means. | |
| `patients.revoke.body` | This moves the record back to unverified, so a match on it is reviewed by a person. Nothing that is already linked is undone. | இது பதிவை மீண்டும் சரிபார்க்கப்படாத நிலைக்கு மாற்றுகிறது; அதனால் அதில் ஏற்படும் பொருத்தத்தை ஒரு நபர் மதிப்பாய்வு செய்வார். ஏற்கனவே இணைக்கப்பட்டவை எதுவும் மாற்றப்படாது. | no issue found; earlier AI pass flag (unnatural): Awkward phrasing on a message describing what the system will do. | |
| `patients.revoke.button` | Revoke confirmation | உறுதிப்படுத்தலைத் திரும்பப் பெறு | no issue found | |
| `patients.revoke.chooseFirst` | Choose a reason first. | முதலில் ஒரு காரணத்தைத் தேர்ந்தெடுக்கவும். | no issue found | |
| `patients.revoke.conflict` | That revoke conflicts with an earlier request. Refresh and try again. | அந்தத் திரும்பப் பெறுதல் முந்தைய கோரிக்கையுடன் முரண்படுகிறது. புதுப்பித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `patients.revoke.done` | The confirmation was revoked. | உறுதிப்படுத்தல் திரும்பப் பெறப்பட்டது. | no issue found | |
| `patients.revoke.reason` | Reason | காரணம் | no issue found | |
| `patients.revoke.reasons.mistaken_click` | Mistaken click | Mistaken click | kept in English by design | |
| `patients.revoke.reasons.planted_or_suspect` | Planted or suspect | Planted or suspect | kept in English by design | |
| `patients.revoke.reasons.wrong_person` | Wrong person | Wrong person | kept in English by design | |
| `patients.revoke.task.not_configured` | No review task was raised: the task settings are not configured yet. | மதிப்பாய்வுப் பணி உருவாக்கப்படவில்லை: பணி அமைப்புகள் இன்னும் அமைக்கப்படவில்லை. | no issue found | |
| `patients.revoke.task.on_record` | Nothing new was recorded: this was already done, or the record was already unverified. | புதிதாக எதுவும் பதிவு செய்யப்படவில்லை: இது ஏற்கனவே செய்யப்பட்டது, அல்லது பதிவு ஏற்கனவே சரிபார்க்கப்படாத நிலையில் இருந்தது. | no issue found | |
| `patients.revoke.task.raised` | A review task was raised. | ஒரு மதிப்பாய்வுப் பணி உருவாக்கப்பட்டது. | no issue found | |
| `patients.revoke.title` | Revoke this identity confirmation? | இந்த அடையாள உறுதிப்படுத்தலைத் திரும்பப் பெறவா? | A: "Revoke / take back" is rendered திரும்பப் பெறு, and "cannot be undone / cannot bring back" is rendered திரும்பப் பெற முடியாது in other strings. The same verb means two different things; a reader could confuse "revoke" with "cannot be reversed". | |
| `patients.search` | Search | தேடு | no issue found | |
| `patients.searchHint` | Search finds a record only for exactly these details. Nothing typed here is saved. | இந்த விவரங்களுக்குச் சரியாகப் பொருந்தும் பதிவை மட்டுமே தேடல் கண்டறியும். இங்கே தட்டச்சு செய்தது எதுவும் சேமிக்கப்படாது. | no issue found | |
| `patients.searchTitle` | Search | தேடல் | no issue found | |
| `patients.title` | Patients | நோயாளர்கள் | no issue found | |
| `patients.truncated` | More matches exist than are shown. | காட்டப்பட்டதை விட அதிகப் பொருத்தங்கள் உள்ளன. | no issue found | |
| `patients.unavailable` | Search is not available right now. Try again later. | தேடல் இப்போது கிடைக்கவில்லை. பின்னர் மீண்டும் முயற்சிக்கவும். | no issue found | |
| `patients.verified` | Verified | சரிபார்க்கப்பட்டது | no issue found | |
| `patients.via.front_desk` | Front desk | வரவேற்பு மேசை | B: "Front desk" is வரவேற்பு மேசை here and முன்பணியாளர் in the erasure strings. Use one term. | |
| `patients.via.referral_worker` | Referral worker | பரிந்துரைப் பணியாளர் | no issue found | |
| `patients.withheld` | Withheld | மறைக்கப்பட்டது | no issue found | |
| `placeholder.about` | [PLACEHOLDER: about the hospital. Content must be supplied by the hospital.] | [PLACEHOLDER: about the hospital. Content must be supplied by the hospital.] | kept in English by design | |
| `placeholder.address` | [PLACEHOLDER: address] | [PLACEHOLDER: address] | kept in English by design | |
| `placeholder.departments` | [PLACEHOLDER: departments and doctors. Content must be supplied by the hospital.] | [PLACEHOLDER: departments and doctors. Content must be supplied by the hospital.] | kept in English by design | |
| `placeholder.erasureWording` | [PLACEHOLDER: what an erasure removes and what it keeps, in the clinic's own words. To be confirmed by the privacy owner and counsel.] | [PLACEHOLDER: what an erasure removes and what it keeps, in the clinic's own words. To be confirmed by the privacy owner and counsel.] | kept in English by design | |
| `placeholder.faqGrievance` | [PLACEHOLDER: how to raise a concern or a data request. Wording to be supplied by the hospital and its legal counsel.] | [PLACEHOLDER: how to raise a concern or a data request. Wording to be supplied by the hospital and its legal counsel.] | kept in English by design | |
| `placeholder.faqPrivacy` | [PLACEHOLDER: how personal information is used. Wording to be supplied by the hospital and its legal counsel.] | [PLACEHOLDER: how personal information is used. Wording to be supplied by the hospital and its legal counsel.] | kept in English by design | |
| `placeholder.fullRecordPending` | [PENDING: opening a patient's full details is awaiting the privacy owner's confirmation. Use synthetic data only until then.] | [PENDING: opening a patient's full details is awaiting the privacy owner's confirmation. Use synthetic data only until then.] | kept in English by design | |
| `placeholder.grievance` | [PLACEHOLDER: grievance officer name and contact details. To be supplied by the hospital.] | [PLACEHOLDER: grievance officer name and contact details. To be supplied by the hospital.] | kept in English by design | |
| `placeholder.hours` | [PLACEHOLDER: opening hours] | [PLACEHOLDER: opening hours] | kept in English by design | |
| `placeholder.identityChecks` | [PLACEHOLDER: what must be checked before confirming an identity. To be written by the clinic.] | [PLACEHOLDER: what must be checked before confirming an identity. To be written by the clinic.] | kept in English by design | |
| `placeholder.phone` | [PLACEHOLDER: phone number] | [PLACEHOLDER: phone number] | kept in English by design | |
| `placeholder.privacyNotice` | [PLACEHOLDER: privacy notice. To be supplied by the hospital and approved by its legal counsel.] | [PLACEHOLDER: privacy notice. To be supplied by the hospital and approved by its legal counsel.] | kept in English by design | |
| `placeholder.privacyPromise` | [PLACEHOLDER: privacy promise wording. To be written and approved by the hospital and its legal counsel.] | [PLACEHOLDER: privacy promise wording. To be written and approved by the hospital and its legal counsel.] | kept in English by design | |
| `placeholder.reasonCodes` | PLACEHOLDER - clinician to define | PLACEHOLDER - clinician to define | kept in English by design | |
| `placeholder.registerOutcomes` | [PLACEHOLDER: the front desk wording for each registration outcome. To be confirmed by the clinic.] | [PLACEHOLDER: the front desk wording for each registration outcome. To be confirmed by the clinic.] | kept in English by design | |
| `placeholder.reviewReasons` | PLACEHOLDER - clinician to define | PLACEHOLDER - clinician to define | kept in English by design | |
| `placeholder.revokeReasons` | [PLACEHOLDER: the wording of these reasons is not final. To be confirmed by the clinic.] | [PLACEHOLDER: the wording of these reasons is not final. To be confirmed by the clinic.] | kept in English by design | |
| `placeholder.taskClose` | PLACEHOLDER - clinician to define how this kind of task is closed. It cannot be closed here. | PLACEHOLDER - clinician to define how this kind of task is closed. It cannot be closed here. | kept in English by design | |
| `portal.activate.activated` | Your account is active. | உங்கள் கணக்கு செயலில் உள்ளது. | no issue found | |
| `portal.activate.already_active` | Your account was already active. | உங்கள் கணக்கு ஏற்கனவே செயலில் இருந்தது. | no issue found | |
| `portal.activate.body` | You can activate your account once you have granted the care-delivery notice. | பராமரிப்பு வழங்கல் அறிவிப்பை வழங்கிய பிறகு உங்கள் கணக்கைச் செயல்படுத்தலாம். | no issue found | |
| `portal.activate.button` | Activate my account | என் கணக்கைச் செயல்படுத்து | no issue found | |
| `portal.activate.confirmBody` | Activates your account if the required decision has been recorded. | தேவையான முடிவு பதிவாகியிருந்தால் உங்கள் கணக்கைச் செயல்படுத்துகிறது. | no issue found | |
| `portal.activate.consent_required` | The care-delivery notice has not been granted yet, so the account was not activated. | பராமரிப்பு வழங்கல் அறிவிப்பு இன்னும் வழங்கப்படவில்லை; எனவே கணக்கு செயல்படுத்தப்படவில்லை. | A: Consent: "the care-delivery notice has not been granted" is rendered பராமரிப்பு வழங்கல் அறிவிப்பு ... வழங்கப்படவில்லை. A notice is not "granted"; consent wording needs the hospital's counsel and a native reviewer. | |
| `portal.activate.title` | Activate your account | உங்கள் கணக்கைச் செயல்படுத்துங்கள் | no issue found | |
| `portal.afterActivation` | Claiming a record, filing a grievance and downloading your data become available once your account is active. | உங்கள் கணக்கு செயலில் வந்த பிறகு பதிவை இணைத்தல், குறை தெரிவித்தல், உங்கள் தரவைப் பதிவிறக்குதல் கிடைக்கும். | no issue found | |
| `portal.claim.alreadyLinked` | Your account is linked to a record. | உங்கள் கணக்கு ஒரு பதிவுடன் இணைக்கப்பட்டுள்ளது. | no issue found | |
| `portal.claim.body` | To see your own record, give the name and date of birth the hospital has for you. This also needs a verified mobile number. | உங்கள் சொந்த பதிவைப் பார்க்க, மருத்துவமனையிடம் உங்களுக்காக உள்ள பெயரையும் பிறந்த தேதியையும் கொடுங்கள். இதற்குச் சரிபார்க்கப்பட்ட கைபேசி எண்ணும் தேவை. | no issue found | |
| `portal.claim.button` | Link my record | என் பதிவை இணை | no issue found | |
| `portal.claim.confirmBody` | We compare what you give with the record that matches your verified mobile number. If it does not match for certain, a person at the hospital decides. | நீங்கள் கொடுப்பதை, உங்கள் சரிபார்க்கப்பட்ட கைபேசி எண்ணுடன் பொருந்தும் பதிவுடன் ஒப்பிடுகிறோம். உறுதியாகப் பொருந்தவில்லை என்றால், மருத்துவமனையில் ஒருவர் முடிவு செய்வார். | no issue found | |
| `portal.claim.dob` | Date of birth | பிறந்த தேதி | no issue found | |
| `portal.claim.invalid` | Check the name and date of birth. Nothing was recorded. | பெயரையும் பிறந்த தேதியையும் சரிபார்க்கவும். எதுவும் பதிவாகவில்லை. | no issue found | |
| `portal.claim.name` | Full name | முழுப் பெயர் | no issue found | |
| `portal.claim.outcome.already_bound` | Your account is already linked to a record. | உங்கள் கணக்கு ஏற்கனவே ஒரு பதிவுடன் இணைக்கப்பட்டுள்ளது. | no issue found | |
| `portal.claim.outcome.claimed` | Your record is linked. | உங்கள் பதிவு இணைக்கப்பட்டது. | no issue found | |
| `portal.claim.outcome.consent_required` | Grant the care-delivery notice first. | முதலில் பராமரிப்பு வழங்கல் அறிவிப்பை வழங்குங்கள். | no issue found | |
| `portal.claim.outcome.locked` | Too many attempts. Please try again later or contact the hospital. | பல முயற்சிகள். பிறகு முயற்சிக்கவும் அல்லது மருத்துவமனையைத் தொடர்பு கொள்ளவும். | no issue found | |
| `portal.claim.outcome.pending_review` | Your request was received. A person at the hospital will decide. | உங்கள் கோரிக்கை பெறப்பட்டது. மருத்துவமனையில் ஒருவர் முடிவு செய்வார். | no issue found | |
| `portal.claim.refused` | This cannot be done for this account: it must be active and have a verified mobile number. | இந்தக் கணக்கிற்கு இதைச் செய்ய முடியாது: அது செயலில் இருக்க வேண்டும், சரிபார்க்கப்பட்ட கைபேசி எண்ணும் இருக்க வேண்டும். | no issue found | |
| `portal.claim.title` | Link your record | உங்கள் பதிவை இணையுங்கள் | no issue found | |
| `portal.claim.unavailable` | This is not available right now. Nothing was recorded. | இது இப்போது கிடைக்கவில்லை. எதுவும் பதிவாகவில்லை. | no issue found | |
| `portal.clinicCode` | Clinic code | மருத்துவமனைக் குறியீடு | no issue found | |
| `portal.consent.changed` | The notice changed while you were reading. The page will refresh; please read the new version. | நீங்கள் படிக்கும்போது அறிவிப்பு மாறிவிட்டது. பக்கம் புதுப்பிக்கப்படும்; புதிய பதிப்பைப் படியுங்கள். | no issue found | |
| `portal.consent.confirmBody` | Your decision is recorded against the version shown above, with the time. | மேலே காட்டப்பட்ட பதிப்புக்கு எதிராக உங்கள் முடிவு நேரத்துடன் பதிவாகும். | no issue found | |
| `portal.consent.decision` | Your decision | உங்கள் முடிவு | no issue found | |
| `portal.consent.grant` | Grant | வழங்கு | no issue found | |
| `portal.consent.grantTitle` | Grant this? | இதை வழங்கவா? | no issue found | |
| `portal.consent.granted` | Granted | வழங்கப்பட்டது | no issue found | |
| `portal.consent.noDecision` | No decision yet | இன்னும் முடிவு இல்லை | no issue found | |
| `portal.consent.none` | No notice has been published yet. | இன்னும் அறிவிப்பு எதுவும் வெளியிடப்படவில்லை. | no issue found | |
| `portal.consent.recorded` | Your decision was recorded. | உங்கள் முடிவு பதிவானது. | no issue found | |
| `portal.consent.regrant` | Grant again | மீண்டும் வழங்கு | C: "மீண்டும் வழங்கு" (give again) is a button label. | |
| `portal.consent.title` | Notices and your decisions | அறிவிப்புகளும் உங்கள் முடிவுகளும் | no issue found | |
| `portal.consent.version` | version {{version}} | பதிப்பு {{version}} | no issue found | |
| `portal.consent.withdraw` | Withdraw | திரும்பப் பெறு | no issue found | |
| `portal.consent.withdrawn` | Withdrawn | திரும்பப் பெறப்பட்டது | no issue found | |
| `portal.consent.withdrawnDone` | Your consent was withdrawn. You can grant it again at any time. | உங்கள் ஒப்புதல் திரும்பப் பெறப்பட்டது. எப்போது வேண்டுமானாலும் மீண்டும் வழங்கலாம். | A: Tells the patient their consent was withdrawn, at once, with no confirmation step. It must read as done and reversible ("மீண்டும் வழங்கலாம்"). "திரும்பப் பெறப்பட்டது" shares its verb with revoke and cannot-be-undone elsewhere (Part 4). | |
| `portal.erased` | Erased | அழிக்கப்பட்டது | no issue found | |
| `portal.grievance.body` | Use this for access, correction, erasure, a nominee, or any other request. A person at the hospital reads it. | அணுகல், திருத்தம், அழித்தல், பரிந்துரைக்கப்பட்டவர், அல்லது வேறு எந்தக் கோரிக்கைக்கும் இதைப் பயன்படுத்துங்கள். மருத்துவமனையில் ஒருவர் இதைப் படிப்பார். | A: "nominee" (a person a patient names to act for them) is rendered பரிந்துரைக்கப்பட்டவர் ("a person who was recommended"). The usual legal word for a nominee differs; this is a patient-rights term, send to the hospital's counsel and a native reviewer. | |
| `portal.grievance.category` | Category | வகை | no issue found | |
| `portal.grievance.categoryHint` | A code from the hospital's list (lowercase letters, digits and underscores). The hospital tells you which codes exist. | மருத்துவமனையின் பட்டியலிலிருந்து ஒரு குறியீடு (சிற்றெழுத்துகள், எண்கள், அடிக்கோடுகள்). எந்தக் குறியீடுகள் உள்ளன என்பதை மருத்துவமனை உங்களுக்குச் சொல்லும். | no issue found | |
| `portal.grievance.categoryRefused` | That category is not on the hospital's list. Nothing was sent. | அந்த வகை மருத்துவமனையின் பட்டியலில் இல்லை. எதுவும் அனுப்பப்படவில்லை. | no issue found | |
| `portal.grievance.chooseCategory` | Choose a category | ஒரு வகையைத் தேர்ந்தெடுங்கள் | no issue found | |
| `portal.grievance.confirmBody` | This sends your message to the hospital. It is stored encrypted. | இது உங்கள் செய்தியை மருத்துவமனைக்கு அனுப்புகிறது. அது குறியாக்கம் செய்து சேமிக்கப்படும். | no issue found | |
| `portal.grievance.file` | Send | அனுப்பு | no issue found | |
| `portal.grievance.filed` | Your message was sent. | உங்கள் செய்தி அனுப்பப்பட்டது. | no issue found | |
| `portal.grievance.mine` | Your earlier messages | நீங்கள் முன்பு அனுப்பிய செய்திகள் | no issue found | |
| `portal.grievance.none` | You have not sent any. | நீங்கள் எதுவும் அனுப்பவில்லை. | no issue found | |
| `portal.grievance.refused` | This cannot be done for this account: it must be active and linked to a record of that clinic. | இந்தக் கணக்கிற்கு இதைச் செய்ய முடியாது: அது செயலில் இருக்க வேண்டும், அந்த மருத்துவமனையின் ஒரு பதிவுடன் இணைக்கப்பட்டிருக்க வேண்டும். | no issue found | |
| `portal.grievance.status.acknowledged` | Seen | பார்க்கப்பட்டது | no issue found | |
| `portal.grievance.status.open` | Open | திறந்தது | no issue found | |
| `portal.grievance.status.resolved` | Resolved | தீர்க்கப்பட்டது | no issue found | |
| `portal.grievance.text` | What you want to tell us | எங்களிடம் நீங்கள் சொல்ல விரும்புவது | no issue found | |
| `portal.grievance.title` | Send a request or a complaint | கோரிக்கை அல்லது புகாரை அனுப்புங்கள் | no issue found | |
| `portal.grievance.unavailable` | This is not available right now (the category list or the encryption is not set up). Nothing was sent. | இது இப்போது கிடைக்கவில்லை (வகைப் பட்டியல் அல்லது குறியாக்கம் அமைக்கப்படவில்லை). எதுவும் அனுப்பப்படவில்லை. | no issue found | |
| `portal.hospital` | Hospital: {{name}} | மருத்துவமனை: {{name}} | no issue found | |
| `portal.linked.label` | Linked to a record | ஒரு பதிவுடன் இணைக்கப்பட்டுள்ளது | no issue found | |
| `portal.myData.body` | Downloads one file with everything the system holds about you that you may see. Each download is recorded. | நீங்கள் பார்க்க அனுமதிக்கப்பட்ட, அமைப்பு உங்களைப் பற்றி வைத்திருக்கும் அனைத்தையும் ஒரே கோப்பாகப் பதிவிறக்குகிறது. ஒவ்வொரு பதிவிறக்கமும் பதிவு செய்யப்படும். | no issue found | |
| `portal.myData.button` | Download my data | என் தரவைப் பதிவிறக்கு | no issue found | |
| `portal.myData.col.decision` | Decision | முடிவு | C: Column heading; முடிவு is the one word for decision, result and end (Part 4). | |
| `portal.myData.col.purpose` | Purpose | நோக்கம் | C: Column heading. | |
| `portal.myData.col.version` | Notice version | அறிவிப்புப் பதிப்பு | C: Column heading. | |
| `portal.myData.col.when` | When | எப்போது | C: Column heading. | |
| `portal.myData.count.bookings` | bookings: {{count}} | முன்பதிவுகள்: {{count}} | no issue found | |
| `portal.myData.count.consents` | consent decisions: {{count}} | சம்மத முடிவுகள்: {{count}} | no issue found | |
| `portal.myData.count.grievances` | messages you sent to the hospital: {{count}} | நீங்கள் மருத்துவமனைக்கு அனுப்பிய செய்திகள்: {{count}} | no issue found | |
| `portal.myData.count.messages` | messages: {{count}} | செய்திகள்: {{count}} | no issue found | |
| `portal.myData.count.records` | records: {{count}} | பதிவுகள்: {{count}} | no issue found | |
| `portal.myData.done` | Your file was downloaded. It holds: | உங்கள் கோப்பு பதிவிறக்கப்பட்டது. அதில் உள்ளவை: | no issue found | |
| `portal.myData.history` | Your consent history | உங்கள் ஒப்புதல் வரலாறு | C: Table heading. | |
| `portal.myData.historyNone` | There are no consent entries in the file. | கோப்பில் ஒப்புதல் பதிவுகள் எதுவும் இல்லை. | C: Empty-state line. | |
| `portal.myData.purpose.ai_processing` | AI-assisted processing | செயற்கை நுண்ணறிவு உதவியுடன் செயலாக்கம் | A: "செயற்கை நுண்ணறிவு உதவியுடன் செயலாக்கம்" is long and technical for a patient; a reviewer should check it is understood, and that it matches the notice wording. | |
| `portal.myData.purpose.care_delivery` | Care delivery | சிகிச்சை வழங்கல் | A: The name of a consent purpose a patient sees in their own history; "சிகிச்சை வழங்கல்" should match how the notice names it. | |
| `portal.myData.purpose.service_improvement` | Service improvement | சேவை மேம்பாடு | A: A consent purpose name; "சேவை மேம்பாடு" (service improvement) should match the notice, which also says the data is de-identified. | |
| `portal.myData.title` | Download my data | என் தரவைப் பதிவிறக்கு | no issue found | |
| `portal.myData.truncated` | The file does not hold everything: some lists were cut short. | கோப்பில் அனைத்தும் இல்லை: சில பட்டியல்கள் சுருக்கப்பட்டன. | no issue found | |
| `portal.notAvailable` | This is not available to you yet. | இது இன்னும் உங்களுக்குக் கிடைக்கவில்லை. | no issue found | |
| `portal.register.body` | You are signed in, but you do not have a patient account here yet. Creating it does not link you to any record. | நீங்கள் உள்நுழைந்துள்ளீர்கள், ஆனால் இங்கு உங்களுக்கு இன்னும் நோயாளிக் கணக்கு இல்லை. அதை உருவாக்குவது உங்களை எந்தப் பதிவுடனும் இணைக்காது. | no issue found | |
| `portal.register.create` | Create my account | என் கணக்கை உருவாக்கு | no issue found | |
| `portal.register.refused` | This sign-in cannot be used for a patient account. | இந்த உள்நுழைவை நோயாளிக் கணக்குக்குப் பயன்படுத்த முடியாது. | no issue found | |
| `portal.register.title` | Create your patient account | உங்கள் நோயாளிக் கணக்கை உருவாக்குங்கள் | no issue found | |
| `portal.signIn.change` | Use a different number | வேறு எண்ணைப் பயன்படுத்து | B: Informal singular imperative on a patient page, see portal.signIn.send. | |
| `portal.signIn.code` | 6-digit code | 6 இலக்கக் குறியீடு | no issue found | |
| `portal.signIn.codeFailed` | That code did not work. Check it and try again, or ask for a new one. | அந்தக் குறியீடு செயல்படவில்லை. சரிபார்த்து மீண்டும் முயற்சிக்கவும், அல்லது புதியதைக் கேட்கவும். | no issue found | |
| `portal.signIn.intro` | We send a one-time code to your mobile number. The same steps sign you in or create your sign-in account. | உங்கள் கைபேசி எண்ணுக்கு ஒருமுறை குறியீடு அனுப்புவோம். அதே படிகள் உங்களை உள்நுழைவிக்கும் அல்லது உங்கள் உள்நுழைவுக் கணக்கை உருவாக்கும். | no issue found | |
| `portal.signIn.phone` | Mobile number | கைபேசி எண் | no issue found | |
| `portal.signIn.phoneBad` | Enter the number with a plus sign and the country code, using digits only. | கூட்டல் குறி மற்றும் நாட்டுக் குறியீட்டுடன், இலக்கங்களை மட்டும் பயன்படுத்தி எண்ணை உள்ளிடுங்கள். | no issue found | |
| `portal.signIn.phoneHint` | With the country code, for example +91 followed by your number. | நாட்டுக் குறியீட்டுடன், எடுத்துக்காட்டாக +91 மற்றும் உங்கள் எண். | no issue found | |
| `portal.signIn.send` | Send me a code | எனக்குக் குறியீட்டை அனுப்பு | B: Register on PATIENT pages: எனக்குக் குறியீட்டை அனுப்பு and வேறு எண்ணைப் பயன்படுத்து (portal.signIn.change) are informal singular imperatives; portal.signIn.title uses the archaic -க form (உள்நுழைக, உருவாக்குக); most other patient strings use the polite -ங்கள் form. Pick one polite register for every patient-facing string. | |
| `portal.signIn.sendFailed` | The code could not be sent. Check the number and try again in a moment. | குறியீட்டை அனுப்ப முடியவில்லை. எண்ணைச் சரிபார்த்து சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `portal.signIn.sent` | A code was sent if the number can receive one. | எண்ணுக்குப் பெற முடிந்தால் ஒரு குறியீடு அனுப்பப்பட்டது. | B: Privacy-sensitive wording ("A code was sent if the number can receive one": the page must not reveal whether a number is registered). The Tamil is awkward (எண்ணுக்குப் பெற முடிந்தால்); check that it still does not reveal that. | |
| `portal.signIn.title` | Sign in or create an account | உள்நுழைக அல்லது கணக்கை உருவாக்குக | B: Archaic -க imperative (உள்நுழைக ... உருவாக்குக) on a patient page, see portal.signIn.send. | |
| `portal.signIn.verify` | Continue | தொடரவும் | no issue found | |
| `portal.stage.active` | Active | செயலில் | no issue found | |
| `portal.stage.label` | Where your account is | உங்கள் கணக்கு இருக்கும் நிலை | no issue found | |
| `portal.stage.new_patient` | New patient | புதிய நோயாளி | no issue found | |
| `portal.stage.pending_activation` | Waiting to be activated | செயல்படுத்தக் காத்திருக்கிறது | no issue found | |
| `portal.stage.registered` | Created | உருவாக்கப்பட்டது | no issue found | |
| `portal.title` | Your account | உங்கள் கணக்கு | no issue found | |
| `portal.unreadable` | Not readable | படிக்க இயலவில்லை | no issue found | |
| `portal.voice.agree` | I have read the notice above and I agree to this voice session. | மேலே உள்ள அறிவிப்பைப் படித்தேன்; இந்தக் குரல் அமர்வுக்கு ஒப்புக்கொள்கிறேன். | A: A patient agrees to a voice session with this sentence. "ஒப்புக்கொள்கிறேன்" (I agree) shares its verb with "approve" and "admit"; a reviewer should confirm it reads as consent, in a polite patient register. | |
| `portal.voice.confirmBody` | This changes one setting on your account. You can change it back at any time. | இது உங்கள் கணக்கின் ஓர் அமைப்பை மாற்றும். எப்போது வேண்டுமானாலும் மீண்டும் மாற்றலாம். | C: Check the register. | |
| `portal.voice.confirmOffTitle` | Turn voice off? | குரலை அணைக்கவா? | no issue found | |
| `portal.voice.confirmOnTitle` | Turn voice on? | குரலை இயக்கவா? | no issue found | |
| `portal.voice.intro` | Voice lets you say a reply out loud instead of typing it. It is optional and it stays off until you turn it on. | தட்டச்சு செய்வதற்குப் பதிலாக உங்கள் பதிலை வாய்மொழியாகச் சொல்ல குரல் உதவும். இது விருப்பத்துக்குரியது; நீங்கள் இயக்கும் வரை அணைந்தே இருக்கும். | B: "வாய்மொழியாக" (by speaking) and "குரல்" (voice) as the feature name; check the word patients use for voice mode. | |
| `portal.voice.invalid` | The session could not be opened as asked. No session was opened. | கேட்டபடி அமர்வைத் திறக்க முடியவில்லை. எந்த அமர்வும் திறக்கப்படவில்லை. | C: Plain error line; check register. | |
| `portal.voice.noAudio` | This screen does not record or play sound yet. The audio connection is not set up. | இந்தத் திரை இன்னும் ஒலியைப் பதிவு செய்யாது, ஒலிக்கவும் செய்யாது. ஒலி இணைப்பு அமைக்கப்படவில்லை. | B: "ஒலி இணைப்பு அமைக்கப்படவில்லை" (the sound connection is not set up) must not read as a fault of the patient's device. Check the wording. | |
| `portal.voice.notSetUp` | Voice sessions are not set up yet. Nothing can be opened. | குரல் அமர்வுகள் இன்னும் அமைக்கப்படவில்லை. எதையும் திறக்க முடியாது. | C: Plain state line. | |
| `portal.voice.notYours` | No session was opened. You are not recorded as a patient of this hospital. | எந்த அமர்வும் திறக்கப்படவில்லை. நீங்கள் இந்த மருத்துவமனையின் நோயாளியாகப் பதிவாகவில்லை. | B: "நோயாளியாகப் பதிவாகவில்லை" (not recorded as a patient) could read as a rejection of the person; check that it is neutral. | |
| `portal.voice.off` | Voice is off for your account. | உங்கள் கணக்கில் குரல் அணைந்துள்ளது. | no issue found | |
| `portal.voice.on` | Voice is on for your account. | உங்கள் கணக்கில் குரல் இயக்கத்தில் உள்ளது. | no issue found | |
| `portal.voice.open` | Open voice session | குரல் அமர்வைத் திற | no issue found | |
| `portal.voice.opened` | A voice session was opened. | ஒரு குரல் அமர்வு திறக்கப்பட்டது. | no issue found | |
| `portal.voice.repeat` | This repeated an earlier request. No new session was made. | இது முன்னர் செய்த அதே கோரிக்கையின் மறுமுறை. புதிய அமர்வு உருவாக்கப்படவில்லை. | B: "மறுமுறை" (repeat) for a repeated request may be unclear to a patient. | |
| `portal.voice.saved` | Your voice setting was saved. | உங்கள் குரல் அமைப்பு சேமிக்கப்பட்டது. | no issue found | |
| `portal.voice.sessionTitle` | Open a voice session | குரல் அமர்வைத் திற | no issue found | |
| `portal.voice.title` | Voice | குரல் | no issue found | |
| `portal.voice.turnOff` | Turn voice off | குரலை அணை | no issue found | |
| `portal.voice.turnOn` | Turn voice on | குரலை இயக்கு | no issue found | |
| `portal.voice.unavailable` | Voice is not available at this hospital right now. No session was opened. | இந்த மருத்துவமனையில் குரல் இப்போது கிடைக்கவில்லை. எந்த அமர்வும் திறக்கப்படவில்லை. | B: Check that "கிடைக்கவில்லை" (not available) reads as a temporary state, and that "எந்த அமர்வும் திறக்கப்படவில்லை" is clear to a patient. | |
| `portal.voice.workflow.intake_question` | Answering a question before a visit | வருகைக்கு முன் கேட்கப்படும் கேள்விக்குப் பதில் சொல்ல | B: Check that "கேள்விக்குப் பதில் சொல்ல" reads as answering a clinic question, not asking one. | |
| `portal.voice.workflow.readiness_item` | Answering an item on a visit checklist | வருகைக்கான தயார்நிலைப் பட்டியலில் ஒரு உருப்படிக்குப் பதில் சொல்ல | B: "தயார்நிலைப் பட்டியல்" (readiness list) is a coined term; check what clinics call a pre-visit checklist. | |
| `portal.voice.workflow.slot_offer` | Replying to an appointment offer | சந்திப்பு வாய்ப்புக்குப் பதில் சொல்ல | B: "சந்திப்பு வாய்ப்பு" for an appointment offer; the console uses another word for appointment in places. | |
| `portal.voice.workflowLegend` | What is this session for? | இந்த அமர்வு எதற்காக? | no issue found | |
| `problem.conflict` | That conflicts with the current state. Refresh and try again. | இது தற்போதைய நிலையுடன் முரண்படுகிறது. புதுப்பித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `problem.forbidden` | This is not available to your role. | இது உங்கள் பங்கிற்குக் கிடைக்காது. | no issue found | |
| `problem.invalid` | Check what you entered and try again. | நீங்கள் உள்ளிட்டதைச் சரிபார்த்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `problem.network` | The connection failed. Check it and try again. | இணைப்பு தோல்வியடைந்தது. இணைப்பைச் சரிபார்த்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `problem.not_found` | That could not be found. | அது கிடைக்கவில்லை. | no issue found | |
| `problem.offline` | You are offline. Nothing was saved. What you typed is still here; send it again when the connection is back. | நீங்கள் இணைப்பில் இல்லை. எதுவும் சேமிக்கப்படவில்லை. நீங்கள் உள்ளிட்டவை இங்கேயே உள்ளன; இணைப்பு திரும்பியதும் மீண்டும் அனுப்புங்கள். | no issue found | |
| `problem.rate_limited` | Too many requests. Wait a moment and try again. | அதிக கோரிக்கைகள். சற்று காத்திருந்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `problem.retry` | Try again | மீண்டும் முயற்சிக்கவும் | no issue found | |
| `problem.step_up` | A fresh authenticator code is needed to do this. Nothing was changed. | இதைச் செய்ய புதிய அங்கீகாரக் குறியீடு தேவை. எதுவும் மாற்றப்படவில்லை. | B: "fresh" code rendered புதிய ("new"); see admin.stepUp. | |
| `problem.unauthorized` | Please sign in again. | தயவுசெய்து மீண்டும் உள்நுழையவும். | no issue found | |
| `problem.unavailable` | This is not available right now. Try again in a moment. | இது இப்போது கிடைக்கவில்லை. சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `problem.unknown` | Something went wrong. Try again. | ஏதோ தவறு ஏற்பட்டது. மீண்டும் முயற்சிக்கவும். | no issue found | |
| `profiles.clinical` | Clinical | மருத்துவப் பயன்பாடு | no issue found | |
| `profiles.demo` | Demo | டெமோ | no issue found | |
| `readiness.col.action` | Action | செயல் | no issue found | |
| `readiness.col.ends` | Ends | முடிவு | B: முடிவு means "decision", "result" AND "end" in this interface (readiness.col.ends, scheduling.col.ends, care.results, referrals.col.decision). One word for three meanings in a clinical setting. | |
| `readiness.col.patient` | Patient | நோயாளி | no issue found | |
| `readiness.col.starts` | Starts | தொடக்கம் | no issue found | |
| `readiness.col.visitType` | Visit type | வருகை வகை | no issue found | |
| `readiness.currentItem` | Current item | தற்போதைய உருப்படி | no issue found | |
| `readiness.escalation` | Review task | மதிப்பாய்வுப் பணி | no issue found | |
| `readiness.finished` | Finished | முடிந்தது | no issue found | |
| `readiness.noContent` | This kind of visit has no readiness content set up. Nothing was started. | இந்த வகை வருகைக்குத் தயார்நிலை உள்ளடக்கம் அமைக்கப்படவில்லை. எதுவும் தொடங்கப்படவில்லை. | no issue found | |
| `readiness.noEscalation` | None raised | எதுவும் எழுப்பப்படவில்லை | no issue found | |
| `readiness.noRun` | There is no readiness run for this visit yet. | இந்த வருகைக்கு இன்னும் தயார்நிலை ஓட்டம் இல்லை. | no issue found | |
| `readiness.notConfigured` | Visit readiness is not set up yet. Nothing was written. | வருகைத் தயார்நிலை இன்னும் அமைக்கப்படவில்லை. எதுவும் எழுதப்படவில்லை. | no issue found | |
| `readiness.notWaiting` | This run is not waiting for a reply. Nothing was recorded. | இந்த ஓட்டம் பதிலுக்காகக் காத்திருக்கவில்லை. எதுவும் பதிவாகவில்லை. | no issue found | |
| `readiness.note` | There is no list of visits here: enter the visit id. The questions and checklist belong to the clinic and are not shown, only where the run is. | இங்கு வருகைகளின் பட்டியல் இல்லை: வருகை அடையாளத்தை உள்ளிடுங்கள். கேள்விகளும் சரிபார்ப்புப் பட்டியலும் மருத்துவமனைக்குரியவை, காட்டப்படுவதில்லை; ஓட்டம் எங்குள்ளது என்பது மட்டுமே காட்டப்படும். | no issue found | |
| `readiness.open` | Show the run | ஓட்டத்தைக் காட்டு | no issue found | |
| `readiness.outcome` | What happened | என்ன நடந்தது | no issue found | |
| `readiness.outcomes.no_reply` | No reply came | பதில் வரவில்லை | no issue found | |
| `readiness.outcomes.replied` | The patient replied | நோயாளி பதிலளித்தார் | no issue found | |
| `readiness.relay` | Relay the patient's reply | நோயாளியின் பதிலை அனுப்பு | no issue found | |
| `readiness.relayBody` | Enter what the patient said, or record that no reply came. The rules read the reply first, and it is stored encrypted. | நோயாளி கூறியதை உள்ளிடுங்கள், அல்லது பதில் வரவில்லை என்று பதிவு செய்யுங்கள். விதிகள் முதலில் பதிலைப் படிக்கும்; அது குறியாக்கம் செய்து சேமிக்கப்படும். | no issue found | |
| `readiness.relayed` | The reply was relayed. | பதில் அனுப்பப்பட்டது. | no issue found | |
| `readiness.replyNeedsText` | A reply needs text. Nothing was recorded. | பதிலுக்கு உரை தேவை. எதுவும் பதிவாகவில்லை. | no issue found | |
| `readiness.replyText` | The patient's reply | நோயாளியின் பதில் | no issue found | |
| `readiness.run` | Run for visit | வருகைக்கான ஓட்டம் | no issue found | |
| `readiness.send` | Relay | அனுப்பு | no issue found | |
| `readiness.start` | Start readiness | தயார்நிலையைத் தொடங்கு | no issue found | |
| `readiness.startBody` | Starts the run for this visit, or shows the one already running. Starting twice returns the same run. | இந்த வருகைக்கான ஓட்டத்தைத் தொடங்குகிறது, அல்லது ஏற்கனவே ஓடுவதைக் காட்டுகிறது. இருமுறை தொடங்கினால் அதே ஓட்டம் திரும்பும். | no issue found | |
| `readiness.startTitle` | Start readiness for this visit? | இந்த வருகைக்கான தயார்நிலையைத் தொடங்கவா? | no issue found | |
| `readiness.started` | The run is started. | ஓட்டம் தொடங்கப்பட்டது. | no issue found | |
| `readiness.status` | Status | நிலை | no issue found | |
| `readiness.statuses.asked` | Question asked | கேள்வி கேட்கப்பட்டது | no issue found | |
| `readiness.statuses.barrier_classified` | Barrier classified | தடை வகைப்படுத்தப்பட்டது | no issue found | |
| `readiness.statuses.barrier_unknown` | Barrier not known | தடை தெரியவில்லை | no issue found | |
| `readiness.statuses.brief_stored` | Brief stored | சுருக்கம் சேமிக்கப்பட்டது | no issue found | |
| `readiness.statuses.brief_unavailable` | Brief unavailable | சுருக்கம் கிடைக்கவில்லை | no issue found | |
| `readiness.statuses.classified` | Reply classified | பதில் வகைப்படுத்தப்பட்டது | no issue found | |
| `readiness.statuses.fact_not_grounded` | Fact not grounded in the reply | தகவல் பதிலில் அடிப்படையற்றது | no issue found | |
| `readiness.statuses.fact_written` | Fact recorded | தகவல் பதிவானது | no issue found | |
| `readiness.statuses.held_unassessable` | Held: could not be assessed | நிறுத்தப்பட்டது: மதிப்பிட முடியவில்லை | no issue found | |
| `readiness.statuses.incomplete_flagged` | Flagged as incomplete | முழுமையற்றது எனக் குறிக்கப்பட்டது | no issue found | |
| `readiness.statuses.next` | Moving to the next item | அடுத்த உருப்படிக்குச் செல்கிறது | no issue found | |
| `readiness.statuses.no_reply` | No reply | பதில் இல்லை | no issue found | |
| `readiness.statuses.nothing_to_ask` | Nothing to ask | கேட்க எதுவுமில்லை | no issue found | |
| `readiness.statuses.nurse_escalated` | Escalated to a nurse | செவிலியருக்கு அனுப்பப்பட்டது | A: "Escalated to a nurse" is rendered செவிலியருக்கு அனுப்பப்பட்டது ("sent to a nurse"), which drops the sense of raising urgency. Same in readiness.statuses.red_flag_escalated and the dashboard and alert strings that use "escalation". | |
| `readiness.statuses.readiness_recorded` | Readiness recorded | தயார்நிலை பதிவானது | no issue found | |
| `readiness.statuses.rebooking_handed_off` | Handed off for rebooking | மறுமுன்பதிவுக்கு ஒப்படைக்கப்பட்டது | no issue found | |
| `readiness.statuses.red_flag_escalated` | Red flag: escalated to a nurse | சிவப்புக் கொடி: செவிலியருக்கு அனுப்பப்பட்டது | A: Red flag wording (see backfill.reply.enterBody) plus the "escalated = sent" loss of urgency. | |
| `readiness.statuses.reply_in` | Reply received | பதில் பெறப்பட்டது | no issue found | |
| `readiness.statuses.screened` | Reply screened | பதில் பரிசோதிக்கப்பட்டது | no issue found | |
| `readiness.statuses.started` | Started | தொடங்கியது | no issue found | |
| `readiness.title` | Visit readiness | வருகைத் தயார்நிலை | no issue found | |
| `readiness.upcomingEmpty` | There are no upcoming visits. | வரவிருக்கும் வருகைகள் இல்லை. | no issue found | |
| `readiness.upcomingTitle` | Upcoming visits | வரவிருக்கும் வருகைகள் | no issue found | |
| `readiness.visitId` | Visit id | வருகை அடையாளம் | no issue found | |
| `readiness.visitIdBad` | That is not a visit id. | இது வருகை அடையாளம் அல்ல. | no issue found | |
| `readiness.waiting.none` | Nothing | எதுவுமில்லை | no issue found | |
| `readiness.waiting.reply` | The patient's reply | நோயாளியின் பதில் | no issue found | |
| `readiness.waitingFor` | Waiting for | காத்திருப்பது | no issue found | |
| `referrals.back` | Back to referrals | பரிந்துரைகளுக்குத் திரும்பு | no issue found | |
| `referrals.card` | Card | அட்டை | no issue found | |
| `referrals.col.decision` | Decision | முடிவு | no issue found | |
| `referrals.col.document` | Document | ஆவணம் | no issue found | |
| `referrals.col.holds` | Open holds | திறந்த நிறுத்தங்கள் | no issue found; earlier AI pass flag (ambiguous): Same hold term issue in the table heading. | |
| `referrals.col.received` | Received | பெறப்பட்டது | no issue found | |
| `referrals.col.state` | State | நிலை | no issue found | |
| `referrals.col.type` | Type | வகை | no issue found | |
| `referrals.decided` | Decided | முடிவு செய்யப்பட்டது | no issue found | |
| `referrals.decision.approve` | Approve | Approve | kept in English by design | |
| `referrals.decision.edit` | Edit | Edit | kept in English by design | |
| `referrals.decision.reject` | Reject | Reject | kept in English by design | |
| `referrals.detail` | Referral | பரிந்துரை | no issue found | |
| `referrals.empty` | There are no referrals to show. | காட்டுவதற்குப் பரிந்துரைகள் இல்லை. | no issue found | |
| `referrals.hiddenFromRole` | Not shown to your role | உங்கள் பங்கிற்குக் காட்டப்படாது | no issue found | |
| `referrals.holds` | Holds | நிறுத்தங்கள் | no issue found; earlier AI pass flag (ambiguous): Stop wording for a hold; different term from slot holds. | |
| `referrals.noHolds` | No holds. | நிறுத்தங்கள் இல்லை. | no issue found | |
| `referrals.noProposal` | There is no proposal for this referral. | இந்தப் பரிந்துரைக்கு முன்மொழிவு இல்லை. | no issue found; earlier AI pass flag (ambiguous): Proposal term differs from the heading term in the same screen. | |
| `referrals.notDecided` | Not decided yet | இன்னும் முடிவு செய்யப்படவில்லை | no issue found | |
| `referrals.notReady` | The decision is offered only when the proposal can be shown. If this referral was only just run, wait a moment and reload the page. | முன்மொழிவைக் காட்ட முடிந்தால் மட்டுமே முடிவு வழங்கப்படும். இந்தப் பரிந்துரை இப்போதுதான் இயக்கப்பட்டிருந்தால், சிறிது நேரம் காத்திருந்து பக்கத்தை மீண்டும் ஏற்றவும். | no issue found | |
| `referrals.open` | Open referral | பரிந்துரையைத் திற | no issue found | |
| `referrals.proposal` | Triage proposal | வகைப்படுத்தல் முன்மொழிவு | A: "Triage" (sorting patients by clinical urgency) is rendered வகைப்படுத்தல் ("classification"), which drops the urgency meaning. The task kind triage_proposal itself stays in English by design; a native clinician should choose the Tamil for triage.; earlier AI pass: corrected (term standardised) | |
| `referrals.proposalAbstained` | Abstained | முன்மொழிவு அளிக்கவில்லை | no issue found; earlier AI pass: corrected (meaning problem) | |
| `referrals.proposalMode` | Mode | முறை | no issue found | |
| `referrals.registeredBy` | Registered by | பதிவு செய்தவர் | no issue found | |
| `referrals.review.confirmBody` | You are about to record the decision "{{decision}}" for this referral. It is kept under your name. | இந்தப் பரிந்துரைக்கு "{{decision}}" என்ற முடிவை நீங்கள் பதிவு செய்யப் போகிறீர்கள். அது உங்கள் பெயரில் வைக்கப்படும். | B: The decision word inside the sentence ("{{decision}}", for example Approve) is deliberately left in English (referrals.decision.*), so a Tamil sentence contains an English word. A native reviewer should say how the decision words should read. | |
| `referrals.review.confirmTitle` | Record this decision? | இந்த முடிவைப் பதிவு செய்யவா? | no issue found | |
| `referrals.review.conflict` | The referral is not waiting for a decision, or a different decision is already recorded. Refresh and try again. | பரிந்துரை முடிவுக்காகக் காத்திருக்கவில்லை, அல்லது வேறொரு முடிவு ஏற்கனவே பதிவாகியுள்ளது. புதுப்பித்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `referrals.review.decidedNote` | A decision has already been recorded. | ஒரு முடிவு ஏற்கனவே பதிவு செய்யப்பட்டுள்ளது. | no issue found | |
| `referrals.review.done` | The decision was recorded. | முடிவு பதிவு செய்யப்பட்டது. | no issue found | |
| `referrals.review.help` | Choose a decision for this referral. Nothing is chosen for you. | இந்தப் பரிந்துரைக்கு ஒரு முடிவைத் தேர்ந்தெடுங்கள். உங்களுக்காக எதுவும் தேர்ந்தெடுக்கப்படவில்லை. | no issue found | |
| `referrals.review.legend` | Decision | முடிவு | no issue found | |
| `referrals.review.reasons` | Reasons | காரணங்கள் | no issue found | |
| `referrals.review.record` | Record decision | முடிவைப் பதிவு செய் | no issue found | |
| `referrals.review.title` | Record a decision | முடிவைப் பதிவு செய் | no issue found | |
| `referrals.run.body` | This queues the Front Door run for this document. It does not message the patient. Any red flag it finds arrives as a task for a person. | இந்த ஆவணத்துக்கான Front Door ஓட்டத்தை வரிசையில் சேர்க்கிறது. நோயாளிக்கு செய்தி அனுப்பாது. அது கண்டறியும் சிவப்புக் கொடி ஒரு நபருக்கான பணியாக வரும். | C: "Front Door" is left in English as a system name; check that is intended. | |
| `referrals.run.button` | Run the referral | பரிந்துரையை இயக்கு | no issue found | |
| `referrals.run.notFound` | This referral could not be found. Nothing was queued. | இந்தப் பரிந்துரை கிடைக்கவில்லை. எதுவும் வரிசையில் சேர்க்கப்படவில்லை. | no issue found | |
| `referrals.run.queued` | The run was queued. Job: | ஓட்டம் வரிசையில் சேர்க்கப்பட்டது. பணி: | C: "Job" (a queued job) is rendered பணி, the same word used for "task" (a review task). Two different things share one word. | |
| `referrals.run.replayed` | This request had already queued a run. Job: | இந்தக் கோரிக்கை ஏற்கனவே ஓட்டத்தை வரிசையில் சேர்த்திருந்தது. பணி: | no issue found | |
| `referrals.run.title` | Run this referral? | இந்தப் பரிந்துரையை இயக்கவா? | no issue found | |
| `referrals.run.unavailable` | Runs cannot be started yet (not set up). Nothing was queued. | ஓட்டங்களை இன்னும் தொடங்க முடியாது (அமைக்கப்படவில்லை). எதுவும் வரிசையில் சேர்க்கப்படவில்லை. | no issue found | |
| `referrals.title` | Referrals | பரிந்துரைகள் | no issue found | |
| `referrals.upload` | Upload a document | ஆவணத்தைப் பதிவேற்று | no issue found | |
| `referrals.version` | version | பதிப்பு | no issue found | |
| `review.abstained` | Abstained | முன்மொழிவு அளிக்கவில்லை | no issue found | |
| `review.altLabel` | Alternative | மாற்று | no issue found | |
| `review.altProbability` | Probability | நிகழ்தகவு | no issue found | |
| `review.alternatives` | Alternatives considered | பரிசீலிக்கப்பட்ட மாற்றுகள் | no issue found | |
| `review.candidateCount` | Candidates | வேட்பாளர்கள் | C: "Candidates" are protocol-card candidates here and waitlisted patients elsewhere (backfill.candidate); both are வேட்பாளர். | |
| `review.confidence` | Confidence | நம்பிக்கை | no issue found | |
| `review.engine` | Engine | இயந்திரம் | no issue found | |
| `review.evidence` | Evidence | சான்று | no issue found | |
| `review.evidenceCrop` | Source image reference | மூலப் படக் குறிப்பு | no issue found | |
| `review.evidenceField` | Field | புலம் | no issue found | |
| `review.evidenceSource` | Source | மூலம் | no issue found | |
| `review.kind` | Task kind | பணி வகை | no issue found | |
| `review.mode` | Mode | முறை | no issue found | |
| `review.noAlternatives` | No alternatives were recorded. | மாற்றுகள் எதுவும் பதிவாகவில்லை. | no issue found | |
| `review.noEvidence` | No evidence was recorded. | சான்று எதுவும் பதிவாகவில்லை. | no issue found | |
| `review.notRecorded` | Not recorded | பதிவு செய்யப்படவில்லை | no issue found | |
| `review.onApprove` | If you approve | நீங்கள் ஒப்புக்கொண்டால் | no issue found | |
| `review.onEdit` | If you edit | நீங்கள் திருத்தினால் | no issue found | |
| `review.onReject` | If you reject | நீங்கள் நிராகரித்தால் | no issue found | |
| `review.reasonChoose` | Choose a reason | ஒரு காரணத்தைத் தேர்ந்தெடுங்கள் | no issue found | |
| `review.reasonLabel` | Reason | காரணம் | no issue found | |
| `review.reasonOptional` | Reason (optional for approve) | காரணம் (ஒப்புக்கொள்ள விருப்பம்) | no issue found | |
| `review.reasonRequired` | Choose a reason. A reject needs one. | ஒரு காரணத்தைத் தேர்ந்தெடுங்கள். நிராகரிக்க ஒன்று தேவை. | no issue found | |
| `review.rule` | Rule that triggered review | மதிப்பாய்வைத் தூண்டிய விதி | no issue found | |
| `review.viewMissing` | There is no proposal to show for this referral. | இந்தப் பரிந்துரைக்குக் காட்ட முன்மொழிவு இல்லை. | no issue found | |
| `review.viewTitle` | Reviewer view | மதிப்பாய்வாளர் பார்வை | no issue found | |
| `roles.admin` | Admin | நிர்வாகி | no issue found | |
| `roles.auditor` | Auditor | தணிக்கையாளர் | no issue found | |
| `roles.business_owner` | Business owner | வணிக உரிமையாளர் | no issue found | |
| `roles.doctor` | Doctor | மருத்துவர் | no issue found | |
| `roles.front_desk` | Front desk | வரவேற்பு மேசை | no issue found | |
| `roles.front_desk_supervisor` | Front desk supervisor | வரவேற்பு மேசை மேற்பார்வையாளர் | C: வரவேற்பு மேசை மேற்பார்வையாளர்: consistent with வரவேற்பு மேசை but the project also uses முன்பணியாளர் for front desk. | |
| `roles.guardian` | Guardian | பாதுகாவலர் | no issue found | |
| `roles.nurse` | Nurse | செவிலியர் | no issue found | |
| `roles.patient` | Patient | நோயாளர் | no issue found | |
| `scheduling.active` | Active | செயலில் | no issue found | |
| `scheduling.add` | Add | சேர் | no issue found | |
| `scheduling.addLocation` | Add a location | இடத்தைச் சேர் | no issue found | |
| `scheduling.addLocationBody` | Creates a location with the name you enter. | நீங்கள் உள்ளிடும் பெயருடன் ஒரு இடத்தை உருவாக்குகிறது. | no issue found | |
| `scheduling.addProvider` | Add a provider | வழங்குநரைச் சேர் | no issue found | |
| `scheduling.addProviderBody` | Creates a provider with the name you enter. No sign-in is linked. | நீங்கள் உள்ளிடும் பெயருடன் ஒரு வழங்குநரை உருவாக்குகிறது. உள்நுழைவு இணைக்கப்படவில்லை. | no issue found | |
| `scheduling.addSlot` | Add a slot | நேர இடத்தைச் சேர் | no issue found | |
| `scheduling.addSlotBody` | Creates one open slot with the provider, location, visit type and times you choose. | நீங்கள் தேர்ந்தெடுக்கும் வழங்குநர், இடம், வருகை வகை மற்றும் நேரங்களுடன் ஒரு திறந்த நேர இடத்தை உருவாக்குகிறது. | no issue found | |
| `scheduling.addVisitType` | Add a visit type | வருகை வகையைச் சேர் | no issue found | |
| `scheduling.addVisitTypeBody` | Creates a visit type with the length you enter. Nothing here guesses a length. | நீங்கள் உள்ளிடும் நீளத்துடன் ஒரு வருகை வகையை உருவாக்குகிறது. இங்கு எந்த நீளமும் ஊகிக்கப்படுவதில்லை. | no issue found | |
| `scheduling.all` | All | அனைத்தும் | no issue found | |
| `scheduling.book` | Book | முன்பதிவு செய் | no issue found | |
| `scheduling.bookBody` | The slot is booked for the patient you choose. Nothing is sent to anyone. | நீங்கள் தேர்ந்தெடுக்கும் நோயாளிக்கு இந்த இடம் முன்பதிவாகும். யாருக்கும் எதுவும் அனுப்பப்படாது. | no issue found | |
| `scheduling.bookDone` | The slot is booked. | இடம் முன்பதிவானது. | no issue found | |
| `scheduling.bookPatient` | Patient | நோயாளி | no issue found | |
| `scheduling.bookTitle` | Book this slot? | இந்த நேர இடத்தை முன்பதிவு செய்யவா? | no issue found | |
| `scheduling.bookings` | Bookings | முன்பதிவுகள் | no issue found | |
| `scheduling.cancelBody` | This cancels the booking and frees its slot. The waitlist is ranked for you to look at. Nobody is contacted and nobody is booked. | இது முன்பதிவை ரத்து செய்து அதன் இடத்தை விடுவிக்கும். நீங்கள் பார்க்க காத்திருப்புப் பட்டியல் வரிசைப்படுத்தப்படும். யாரும் தொடர்பு கொள்ளப்படமாட்டார்கள்; யாரும் முன்பதிவு செய்யப்படமாட்டார்கள். | no issue found | |
| `scheduling.cancelBooking` | Cancel booking | முன்பதிவை ரத்து செய் | no issue found | |
| `scheduling.cancelCandidates` | Ranked waitlist for the freed slot | காலியான இடத்திற்கான வரிசைப்படுத்தப்பட்ட காத்திருப்புப் பட்டியல் | no issue found | |
| `scheduling.cancelDone` | The booking was cancelled and the slot is free. | முன்பதிவு ரத்தானது; இடம் காலியானது. | no issue found | |
| `scheduling.cancelTitle` | Cancel this booking? | இந்த முன்பதிவை ரத்து செய்யவா? | no issue found | |
| `scheduling.choose` | Choose a visit type | ஒரு வருகை வகையைத் தேர்ந்தெடுக்கவும் | no issue found | |
| `scheduling.col.action` | Action | செயல் | no issue found | |
| `scheduling.col.booking` | Booking | முன்பதிவு | no issue found | |
| `scheduling.col.channel` | Channel | வழி | no issue found | |
| `scheduling.col.code` | Code | குறியீடு | no issue found | |
| `scheduling.col.delivered` | Delivered | வழங்கப்பட்டது | no issue found | |
| `scheduling.col.duration` | Length | நீளம் | no issue found; earlier AI pass flag (unnatural): Word means physical length rather than time duration. | |
| `scheduling.col.ends` | Ends | முடிவு | no issue found | |
| `scheduling.col.expires` | Expires | காலாவதி | no issue found | |
| `scheduling.col.heldUntil` | Held until | வரை நிறுத்தம் | C: "Held until" rendered வரை நிறுத்தம் ("until halt"); awkward. | |
| `scheduling.col.hold` | Hold | ஒதுக்கீடு | no issue found; earlier AI pass flag (ambiguous): Same allocation-for-hold term issue. | |
| `scheduling.col.joined` | Joined | சேர்ந்த நேரம் | no issue found | |
| `scheduling.col.label` | Label | குறிப்பு | no issue found; earlier AI pass flag (ambiguous): Same label/note term issue. | |
| `scheduling.col.location` | Location | இடம் | no issue found | |
| `scheduling.col.name` | Name | பெயர் | no issue found | |
| `scheduling.col.offer` | Offer | வாய்ப்பு | no issue found | |
| `scheduling.col.patient` | Patient | நோயாளி | no issue found | |
| `scheduling.col.priority` | Priority | முன்னுரிமை | no issue found | |
| `scheduling.col.provider` | Provider | வழங்குநர் | no issue found | |
| `scheduling.col.rank` | Rank | தரவரிசை | no issue found; earlier AI pass: corrected (meaning problem) | |
| `scheduling.col.slot` | Slot | நேரம் | no issue found | |
| `scheduling.col.starts` | Starts | தொடக்கம் | no issue found | |
| `scheduling.col.state` | State | நிலை | no issue found | |
| `scheduling.col.status` | Status | நிலை | no issue found | |
| `scheduling.col.visitType` | Visit type | வருகை வகை | no issue found | |
| `scheduling.conflict` | That changed in the meantime. Refresh and look again. | இடைப்பட்ட நேரத்தில் இது மாறிவிட்டது. புதுப்பித்து மீண்டும் பாருங்கள். | no issue found | |
| `scheduling.copied` | The offer text was copied. Send it yourself, then mark it delivered. | வாய்ப்பு உரை நகலெடுக்கப்பட்டது. நீங்களே அனுப்பி, பிறகு வழங்கப்பட்டதாகக் குறிக்கவும். | no issue found | |
| `scheduling.copyManual` | Copying was blocked by the browser. Select the text below and copy it yourself. | உலாவி நகலெடுப்பைத் தடுத்தது. கீழே உள்ள உரையைத் தேர்ந்தெடுத்து நீங்களே நகலெடுக்கவும். | no issue found | |
| `scheduling.copyNotFound` | The offer text could not be found. | வாய்ப்பு உரையைக் கண்டுபிடிக்க முடியவில்லை. | no issue found | |
| `scheduling.copyText` | Copy offer text | வாய்ப்பு உரையை நகலெடு | no issue found | |
| `scheduling.createConflict` | That overlaps another slot of the same provider, or was already used for a different request. | இது அதே வழங்குநரின் மற்றொரு நேர இடத்துடன் மேலோங்குகிறது, அல்லது வேறொரு கோரிக்கைக்கு ஏற்கனவே பயன்படுத்தப்பட்டது. | B: "overlaps another slot" is rendered மேலோங்குகிறது, which means "prevails / rises above", not "overlaps". Meaning error in a scheduling message. | |
| `scheduling.createInvalid` | Check what you entered. The times must be a real range. | நீங்கள் உள்ளிட்டதைச் சரிபார்க்கவும். நேரங்கள் சரியான வரம்பாக இருக்க வேண்டும். | no issue found | |
| `scheduling.created` | Created. | உருவாக்கப்பட்டது. | no issue found | |
| `scheduling.draftTitle` | Offer draft | வாய்ப்பு வரைவு | no issue found | |
| `scheduling.durationMinutes` | Length in minutes | நீளம் (நிமிடங்களில்) | no issue found | |
| `scheduling.empty` | Nothing to show. | காட்டுவதற்கு எதுவும் இல்லை. | no issue found | |
| `scheduling.endsAt` | Ends | முடிவு | no issue found | |
| `scheduling.filterLocation` | Location | இடம் | no issue found | |
| `scheduling.filterProvider` | Provider | வழங்குநர் | no issue found | |
| `scheduling.hold` | Hold | நிறுத்து | no issue found | |
| `scheduling.holdBody` | The slot is kept for the time you enter. Nothing is sent to anyone. | நீங்கள் உள்ளிடும் நேரம் வரை இந்த இடம் வைக்கப்படும். யாருக்கும் எதுவும் அனுப்பப்படாது. | no issue found | |
| `scheduling.holdDone` | The slot is held. | இடம் நிறுத்தி வைக்கப்பட்டது. | C: Three different wordings for a slot "hold": ஒதுக்கீடு (the holds list), நிறுத்தி வைக்கப்பட்டது (this string), வைக்கப்படும் (scheduling.holdBody). நிறுத்து also means "stop". Use one. | |
| `scheduling.holdFilter` | Show holds that are | இந்த நிலையிலுள்ள ஒதுக்கீடுகளைக் காட்டு | no issue found | |
| `scheduling.holdMinutes` | Minutes to hold | நிறுத்தும் நிமிடங்கள் | no issue found | |
| `scheduling.holdPatient` | Hold for a patient (optional) | ஒரு நோயாளிக்காக நிறுத்து (விருப்பம்) | no issue found | |
| `scheduling.holdStatus.active` | Active | செயலில் | no issue found | |
| `scheduling.holdStatus.converted` | Booked | முன்பதிவானது | no issue found | |
| `scheduling.holdStatus.expired` | Expired | காலாவதியானது | no issue found | |
| `scheduling.holdStatus.released` | Released | விடுவிக்கப்பட்டது | no issue found | |
| `scheduling.holdTitle` | Hold this slot? | இந்த நேர இடத்தை நிறுத்தி வைக்கவா? | no issue found | |
| `scheduling.holds` | Holds | ஒதுக்கீடுகள் | no issue found; earlier AI pass flag (ambiguous): Allocation/quota does not convey a held slot. | |
| `scheduling.inactive` | Inactive | செயலற்றது | no issue found | |
| `scheduling.join` | Add to waitlist | காத்திருப்புப் பட்டியலில் சேர் | no issue found | |
| `scheduling.joinBody` | The patient is added to the queue for the visit type and priority you choose. Nobody is contacted. | நீங்கள் தேர்ந்தெடுக்கும் வருகை வகை மற்றும் முன்னுரிமைக்கான வரிசையில் நோயாளி சேர்க்கப்படுவார். யாரும் தொடர்பு கொள்ளப்படமாட்டார்கள். | no issue found | |
| `scheduling.joinConflict` | This patient is already waiting for that visit type. | இந்த நோயாளி அந்த வருகை வகைக்கு ஏற்கனவே காத்திருக்கிறார். | no issue found | |
| `scheduling.joinDone` | Added to the waitlist. | காத்திருப்புப் பட்டியலில் சேர்க்கப்பட்டது. | no issue found | |
| `scheduling.joinPriority` | Priority | முன்னுரிமை | no issue found | |
| `scheduling.joinTitle` | Add to the waitlist? | காத்திருப்புப் பட்டியலில் சேர்க்கவா? | no issue found | |
| `scheduling.joinVisitType` | Visit type | வருகை வகை | no issue found | |
| `scheduling.lists` | Scheduling lists | அட்டவணைப் பட்டியல்கள் | no issue found | |
| `scheduling.locations` | Locations | இடங்கள் | no issue found | |
| `scheduling.markBody` | Record that you delivered this offer yourself. The system sent nothing to the patient. This cannot be undone. | இந்த வாய்ப்பை நீங்களே வழங்கினீர்கள் என்று பதிவு செய்கிறது. கணினி நோயாளிக்கு எதுவும் அனுப்பவில்லை. இதைத் திரும்பப் பெற முடியாது. | no issue found | |
| `scheduling.markConflict` | This offer is no longer live (it expired, was released or was booked). | இந்த வாய்ப்பு இனி செயலில் இல்லை (காலாவதியானது, விடுவிக்கப்பட்டது அல்லது முன்பதிவானது). | no issue found | |
| `scheduling.markDelivered` | Mark delivered | வழங்கப்பட்டதாகக் குறி | no issue found | |
| `scheduling.markDone` | The offer is marked delivered. | வாய்ப்பு வழங்கப்பட்டதாகக் குறிக்கப்பட்டது. | no issue found | |
| `scheduling.markTitle` | Mark this offer delivered? | இந்த வாய்ப்பை வழங்கப்பட்டதாகக் குறிக்கவா? | no issue found | |
| `scheduling.minutes` | min | நிமி | no issue found | |
| `scheduling.name` | Name | பெயர் | no issue found | |
| `scheduling.noBookings` | There are no confirmed bookings. | உறுதிசெய்யப்பட்ட முன்பதிவுகள் இல்லை. | no issue found | |
| `scheduling.noCandidates` | Nobody on the waitlist matches this slot. | இந்த இடத்திற்குப் பொருந்தும் யாரும் காத்திருப்புப் பட்டியலில் இல்லை. | no issue found | |
| `scheduling.noHolds` | There are no holds to show. | காட்டுவதற்கு ஒதுக்கீடுகள் இல்லை. | no issue found | |
| `scheduling.noOffers` | There are no offers. | வாய்ப்புகள் இல்லை. | no issue found | |
| `scheduling.noSlots` | There are no open or held slots. | திறந்த அல்லது நிறுத்தி வைக்கப்பட்ட நேர இடங்கள் இல்லை. | no issue found | |
| `scheduling.noWaitlist` | Nobody is waiting. | யாரும் காத்திருக்கவில்லை. | no issue found | |
| `scheduling.offerCandidates` | Candidate details | பொருத்தமானவற்றின் விவரங்கள் | no issue found; earlier AI pass: corrected (meaning problem) | |
| `scheduling.offerFilter` | Filter offers by state | நிலை வாரியாக வாய்ப்புகளை வடிகட்டு | no issue found | |
| `scheduling.offerState.awaiting_delivery` | Awaiting delivery | வழங்கக் காத்திருக்கிறது | no issue found | |
| `scheduling.offerState.booked` | Booked | முன்பதிவானது | no issue found | |
| `scheduling.offerState.delivered` | Delivered | வழங்கப்பட்டது | no issue found | |
| `scheduling.offerState.expired` | Expired | காலாவதியானது | no issue found | |
| `scheduling.offerState.released` | Released | விடுவிக்கப்பட்டது | no issue found | |
| `scheduling.offerStatus.degraded` | Partly available | ஓரளவு கிடைக்கிறது | no issue found | |
| `scheduling.offerStatus.empty` | No candidates | பொருத்தமானவை இல்லை | no issue found; earlier AI pass: corrected (meaning problem) | |
| `scheduling.offerStatus.ok` | A draft is ready | வரைவு தயாராக உள்ளது | no issue found | |
| `scheduling.offerStatus.unavailable` | Not set up yet | இன்னும் அமைக்கப்படவில்லை | no issue found | |
| `scheduling.offers` | Offers | வாய்ப்புகள் | no issue found; earlier AI pass flag (ambiguous): Means opportunities rather than offers. | |
| `scheduling.offersListTitle` | Offers | வாய்ப்புகள் | no issue found | |
| `scheduling.offersNote` | A read-only view of the draft offer. It is computed when you ask and stored nowhere. | வரைவு வாய்ப்பின் படிக்க-மட்டும் பார்வை. நீங்கள் கேட்கும்போது கணக்கிடப்படுகிறது, எங்கும் சேமிக்கப்படுவதில்லை. | no issue found | |
| `scheduling.patientChosen` | Patient chosen | நோயாளி தேர்ந்தெடுக்கப்பட்டார் | no issue found | |
| `scheduling.patientDob` | Date of birth | பிறந்த தேதி | no issue found | |
| `scheduling.patientFind` | Find | தேடு | no issue found | |
| `scheduling.patientName` | Full name | முழுப் பெயர் | no issue found | |
| `scheduling.patientNone` | No patient matched. | எந்த நோயாளியும் பொருந்தவில்லை. | no issue found | |
| `scheduling.patientPick` | Choose the patient | நோயாளியைத் தேர்ந்தெடுங்கள் | no issue found | |
| `scheduling.patientSearch` | Find the patient | நோயாளியைக் கண்டறி | no issue found | |
| `scheduling.placeholderConfirm` | [PLACEHOLDER: confirmation wording to be approved by the clinic] | [இடஒதுக்கீடு: உறுதிப்படுத்தும் சொற்களை மருத்துவமனை அங்கீகரிக்க வேண்டும்] | B: A placeholder marker rendered as [இடஒதுக்கீடு: ...]. இடஒதுக்கீடு means a reservation or quota, not "placeholder". Every other placeholder string stays in English by design; this one should too. | |
| `scheduling.priorityLevels` | Priority levels | முன்னுரிமை நிலைகள் | no issue found | |
| `scheduling.providers` | Providers | வழங்குநர்கள் | no issue found; earlier AI pass flag (ambiguous): May read as general suppliers rather than clinicians. | |
| `scheduling.release` | Release | விடுவி | no issue found | |
| `scheduling.releaseBody` | The slot is given back now. | நேரம் இப்போதே திருப்பி வழங்கப்படும். | no issue found | |
| `scheduling.releaseConflict` | This hold was already booked or has expired. The list will refresh. | இந்த ஒதுக்கீடு ஏற்கனவே முன்பதிவாகிவிட்டது அல்லது காலாவதியானது. பட்டியல் புதுப்பிக்கப்படும். | no issue found | |
| `scheduling.releaseTitle` | Release this hold? | இந்த ஒதுக்கீட்டை விடுவிக்கவா? | no issue found | |
| `scheduling.sendOffer` | Send offer | வாய்ப்பை அனுப்பு | no issue found | |
| `scheduling.showDraft` | Show draft | வரைவைக் காட்டு | no issue found | |
| `scheduling.showInactive` | Show inactive | செயலற்றவற்றையும் காட்டு | no issue found | |
| `scheduling.slotStatus.booked` | Booked | முன்பதிவானது | no issue found | |
| `scheduling.slotStatus.cancelled` | Cancelled | ரத்தானது | no issue found | |
| `scheduling.slotStatus.held` | Held | நிறுத்தி வைக்கப்பட்டது | no issue found | |
| `scheduling.slotStatus.open` | Open | திறந்தது | no issue found | |
| `scheduling.slots` | Slots | நேர இடங்கள் | no issue found | |
| `scheduling.startsAt` | Starts | தொடக்கம் | no issue found | |
| `scheduling.title` | Scheduling | நேர அட்டவணை | no issue found | |
| `scheduling.unknownPatient` | This patient is not registered at the clinic that owns this slot. | இந்த நோயாளி இந்த நேர இடத்தைக் கொண்ட மருத்துவமனையில் பதிவு செய்யப்படவில்லை. | no issue found | |
| `scheduling.visitTypes` | Visit types | வருகை வகைகள் | no issue found | |
| `scheduling.waitlist` | Waitlist | காத்திருப்புப் பட்டியல் | no issue found | |
| `scheduling.zoneNote` | Times are in the clinic time zone: {{zone}} (demo placeholder). | நேரங்கள் மருத்துவமனையின் நேர மண்டலத்தில் உள்ளன: {{zone}} (டெமோ இடஒதுக்கீடு). | B: "demo placeholder" is rendered டெமோ இடஒதுக்கீடு; இடஒதுக்கீடு means a reservation or quota, not "placeholder". Same in time.zoneNotePlaceholder. | |
| `shell.activeRole` | Active role | தற்போதைய பங்கு | no issue found; earlier AI pass flag (ambiguous): Role is a key concept used on many screens; the word chosen literally means share/part. | |
| `shell.activeRoleUnavailable` | That role is not available right now. Choose a role to continue. You are still signed in. | அந்தப் பங்கு இப்போது கிடைக்கவில்லை. தொடர ஒரு பங்கைத் தேர்ந்தெடுங்கள். நீங்கள் இன்னும் உள்நுழைந்துள்ளீர்கள். | no issue found | |
| `shell.changeRole` | Change role | பங்கை மாற்று | no issue found | |
| `shell.clinic` | Clinic | கிளினிக் | B: "Clinic" is கிளினிக் here but மருத்துவமனை ("hospital") in nearly every other string; the console serves clinics, the landing pages a hospital. Check which word is meant where.; earlier AI pass flag (mixed-language): Loanword while other strings use the Tamil word for hospital/clinic; inconsistent. | |
| `shell.continue` | Continue | தொடரவும் | no issue found | |
| `shell.environment` | Environment | சூழல் | no issue found | |
| `shell.navLabel` | Session | அமர்வு | no issue found | |
| `shell.pickHelp` | Pick the one role you need for this session. You can change it later. | இந்த அமர்வுக்குத் தேவையான ஒரே ஒரு பங்கைத் தேர்ந்தெடுங்கள். பின்னர் மாற்றலாம். | no issue found | |
| `shell.pickTitle` | Choose your active role | உங்கள் தற்போதைய பங்கைத் தேர்ந்தெடுங்கள் | no issue found | |
| `shell.profile` | Operating profile | இயக்க நிலை | no issue found | |
| `shell.profileUnknown` | Operating profile unknown | இயக்க நிலை தெரியவில்லை | no issue found | |
| `staff.account.signOut` | Sign out | வெளியேறுக | no issue found | |
| `staff.account.unavailable` | This account cannot use the staff console. Contact your administrator. | இந்தக் கணக்கால் பணியாளர் தளத்தைப் பயன்படுத்த முடியாது. உங்கள் நிர்வாகியைத் தொடர்பு கொள்ளவும். | no issue found | |
| `staff.activate.body` | If you were invited to the staff console and have not activated your account yet, you can do it here. It needs the authenticator code you signed in with. | பணியாளர் கன்சோலுக்கு உங்களுக்கு அழைப்பு வந்து, இன்னும் கணக்கைச் செயல்படுத்தவில்லை என்றால், இங்கே செய்யலாம். நீங்கள் உள்நுழைந்த அங்கீகார குறியீடு இதற்குத் தேவை. | B: Long sentence; check the invited-staff wording and that "அங்கீகார குறியீடு" is the word staff use for the authenticator code. | |
| `staff.activate.button` | Activate my account | என் கணக்கைச் செயல்படுத்து | C: Button label. | |
| `staff.activate.confirmBody` | This activates your own account. Nobody else, an administrator included, can do it for you. | இது உங்கள் சொந்தக் கணக்கைச் செயல்படுத்தும். வேறு யாரும், நிர்வாகி உட்பட, உங்களுக்காக இதைச் செய்ய முடியாது. | B: "நிர்வாகி உட்பட" (an administrator included) states that nobody else can activate the account; check it reads as a fact, not a threat. | |
| `staff.activate.confirmTitle` | Activate your account? | உங்கள் கணக்கைச் செயல்படுத்தவா? | C: Dialog title. | |
| `staff.activate.done` | Your account is activated. | உங்கள் கணக்கு செயல்படுத்தப்பட்டது. | C: Plain state line. | |
| `staff.activate.notDone` | Your account was not activated. Contact your administrator. | உங்கள் கணக்கு செயல்படுத்தப்படவில்லை. உங்கள் நிர்வாகியைத் தொடர்பு கொள்ளுங்கள். | C: Plain state line. | |
| `staff.activate.refused` | This account cannot be activated here. Contact your administrator. | இந்தக் கணக்கை இங்கே செயல்படுத்த முடியாது. உங்கள் நிர்வாகியைத் தொடர்பு கொள்ளுங்கள். | C: Plain state line. | |
| `staff.activate.title` | Activate your account | உங்கள் கணக்கைச் செயல்படுத்துங்கள் | C: Heading. | |
| `staff.console.error` | Your account details could not be loaded. | உங்கள் கணக்கு விவரங்களை ஏற்ற முடியவில்லை. | no issue found | |
| `staff.console.loading` | Loading your account… | உங்கள் கணக்கை ஏற்றுகிறது… | no issue found | |
| `staff.console.retry` | Try again | மீண்டும் முயற்சிக்கவும் | no issue found | |
| `staff.console.welcome` | You are signed in. | நீங்கள் உள்நுழைந்துள்ளீர்கள். | no issue found | |
| `staff.mfa.challenge.help` | Open your authenticator app and enter the current 6-digit code. | அங்கீகார செயலியைத் திறந்து தற்போதைய 6 இலக்கக் குறியீட்டை உள்ளிடுங்கள். | no issue found | |
| `staff.mfa.challenge.submit` | Verify | சரிபார்க்கவும் | no issue found | |
| `staff.mfa.challenge.title` | Enter your authenticator code | உங்கள் அங்கீகாரக் குறியீட்டை உள்ளிடுங்கள் | no issue found | |
| `staff.mfa.code` | 6-digit code | 6 இலக்கக் குறியீடு | no issue found | |
| `staff.mfa.codeHint` | Enter the 6 digits shown in your authenticator app. | உங்கள் அங்கீகார செயலியில் காட்டப்படும் 6 இலக்கங்களை உள்ளிடுங்கள். | no issue found | |
| `staff.mfa.enrol.error` | Setup could not be started. | அமைப்பைத் தொடங்க முடியவில்லை. | no issue found | |
| `staff.mfa.enrol.intro` | Staff accounts need a second step at every sign-in. Scan this QR code with an authenticator app. | பணியாளர் கணக்குகளுக்கு ஒவ்வொரு உள்நுழைவிலும் இரண்டாவது படி தேவை. ஒரு அங்கீகார செயலி மூலம் இந்த QR குறியீட்டை ஸ்கேன் செய்யுங்கள். | no issue found | |
| `staff.mfa.enrol.manual` | Or enter this key in the app manually: | அல்லது இந்த விசையைச் செயலியில் கைமுறையாக உள்ளிடுங்கள்: | no issue found | |
| `staff.mfa.enrol.preparing` | Preparing your setup… | உங்கள் அமைப்பைத் தயாரிக்கிறது… | no issue found | |
| `staff.mfa.enrol.retry` | Try again | மீண்டும் முயற்சிக்கவும் | no issue found | |
| `staff.mfa.enrol.scanAlt` | QR code for your authenticator app | அங்கீகார செயலிக்கான QR குறியீடு | no issue found | |
| `staff.mfa.enrol.submit` | Verify and continue | சரிபார்த்து தொடரவும் | no issue found | |
| `staff.mfa.enrol.title` | Set up your authenticator app | உங்கள் அங்கீகார செயலியை அமைக்கவும் | no issue found | |
| `staff.mfa.failed` | That code was not accepted. Try again. | அந்தக் குறியீடு ஏற்கப்படவில்லை. மீண்டும் முயற்சிக்கவும். | no issue found | |
| `staff.mfa.invalid` | Enter the 6-digit code. | 6 இலக்கக் குறியீட்டை உள்ளிடுங்கள். | no issue found | |
| `staff.mfa.verifying` | Verifying… | சரிபார்க்கிறது… | no issue found | |
| `staff.session.ended` | Please sign in again. | தயவுசெய்து மீண்டும் உள்நுழையவும். | no issue found | |
| `staff.signIn.email` | Work email | பணி மின்னஞ்சல் | no issue found | |
| `staff.signIn.failed` | Sign-in failed. Check your details and try again. | உள்நுழைவு தோல்வியடைந்தது. விவரங்களைச் சரிபார்த்து மீண்டும் முயற்சிக்கவும். | no issue found | |
| `staff.signIn.intro` | Sign in with your work email and password. | உங்கள் பணி மின்னஞ்சல் மற்றும் கடவுச்சொல்லுடன் உள்நுழையுங்கள். | no issue found | |
| `staff.signIn.invitationOnly` | Accounts are created by invitation only. | கணக்குகள் அழைப்பின் மூலம் மட்டுமே உருவாக்கப்படும். | no issue found | |
| `staff.signIn.password` | Password | கடவுச்சொல் | no issue found | |
| `staff.signIn.submit` | Sign in | உள்நுழைக | no issue found | |
| `staff.signIn.submitting` | Signing in… | உள்நுழைகிறது… | no issue found | |
| `staff.signIn.title` | Staff sign in | பணியாளர் உள்நுழைவு | no issue found | |
| `staff.stepUp.cancel` | Cancel | ரத்து செய் | no issue found; earlier AI pass flag (register): Bare imperative sits next to a -க confirm button in the same dialog; mixed button forms. | |
| `staff.stepUp.help` | This action needs a fresh code from your authenticator app. | இந்தச் செயலுக்கு உங்கள் அங்கீகார செயலியிலிருந்து புதிய குறியீடு தேவை. | B: "fresh" code rendered புதிய ("new"); see admin.stepUp. | |
| `staff.stepUp.submit` | Confirm | உறுதிப்படுத்துக | no issue found; earlier AI pass flag (register): Same dialog mixes -க confirm with bare-imperative cancel; app-wide button form inconsistency. | |
| `staff.stepUp.title` | Confirm it is you | நீங்கள்தான் என உறுதிப்படுத்துங்கள் | no issue found | |
| `tasks.ackBody` | You will be recorded as the person who took this task. | இந்தப் பணியை எடுத்தவராக நீங்கள் பதிவு செய்யப்படுவீர்கள். | B: "acknowledge" (take and be recorded as holding a task) is rendered ஏற்றுக்கொள்; the same verb as "approve". A reader may think acknowledging a task approves something. See alerts.acknowledged. | |
| `tasks.ackTitle` | Acknowledge this task? | இந்தப் பணியை ஏற்றுக்கொள்ளவா? | no issue found | |
| `tasks.acknowledge` | Acknowledge | ஏற்றுக்கொள் | no issue found; earlier AI pass flag (register): Accept is not quite acknowledge and shares a word with accepting an appointment. | |
| `tasks.acknowledgedBy` | Acknowledged by | ஏற்றுக்கொண்டவர் | no issue found | |
| `tasks.actions` | Actions | செயல்கள் | no issue found | |
| `tasks.back` | Back to tasks | பணிகளுக்குத் திரும்பு | no issue found | |
| `tasks.closed` | This task is closed. | இந்தப் பணி தீர்க்கப்பட்டது. | C: "closed" and "resolved" are both rendered தீர்க்கப்பட்டது ("solved"); the console distinguishes closing a task from resolving it in other strings.; earlier AI pass: corrected (term standardised) | |
| `tasks.closedElsewhere` | This kind of task is closed through its own screen, not here. | இந்த வகைப் பணி இங்கே அல்ல, அதன் சொந்தத் திரையில் தீர்க்கப்படும். | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.col.assigned` | Assigned to | ஒதுக்கப்பட்டவர் | no issue found | |
| `tasks.col.created` | Created | உருவாக்கப்பட்டது | no issue found | |
| `tasks.col.kind` | Kind | வகை | no issue found | |
| `tasks.col.priority` | Priority | முன்னுரிமை | no issue found | |
| `tasks.col.status` | Status | நிலை | no issue found | |
| `tasks.col.task` | Task | பணி | no issue found | |
| `tasks.conflictAck` | Someone else holds this task, or it is already closed. | இந்தப் பணியை வேறொருவர் வைத்துள்ளார், அல்லது ஏற்கனவே தீர்க்கப்பட்டது. | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.conflictResolve` | This task cannot be closed here, or someone else changed it. | இந்தப் பணியை இங்கே தீர்க்க முடியாது, அல்லது வேறொருவர் மாற்றியுள்ளார். | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.detail` | Task | பணி | no issue found | |
| `tasks.empty` | There are no tasks to show. | காட்டுவதற்குப் பணிகள் இல்லை. | no issue found | |
| `tasks.filter` | Show tasks that are | இந்த நிலையிலுள்ள பணிகளைக் காட்டு | no issue found | |
| `tasks.kind.identity_confirmation_revoked` | Identity confirmation revoked | அடையாள உறுதிப்படுத்தல் திரும்பப் பெறப்பட்டது | no issue found | |
| `tasks.kind.red_flag` | Red flag | Red flag | kept in English by design | |
| `tasks.kind.referral_dead_letter` | Referral failed | பரிந்துரை தோல்வியடைந்தது | no issue found | |
| `tasks.kind.referral_hold` | Referral hold | பரிந்துரை நிறுத்தம் | no issue found; earlier AI pass flag (ambiguous): Same hold term issue. | |
| `tasks.kind.result_correction` | Result correction | முடிவுத் திருத்தம் | no issue found | |
| `tasks.kind.screening_unassessable` | Unassessable | Unassessable | kept in English by design | |
| `tasks.kind.triage_proposal` | Triage proposal | Triage proposal | kept in English by design | |
| `tasks.kind.unmerge_review` | Unmerge review | இணைப்பு நீக்க மதிப்பாய்வு | no issue found | |
| `tasks.noProvenance` | Nothing is recorded. | எதுவும் பதிவு செய்யப்படவில்லை. | no issue found | |
| `tasks.open` | Open task | பணியைத் திற | no issue found | |
| `tasks.openReferral` | Open the referral | பரிந்துரையைத் திற | no issue found | |
| `tasks.priority.immediate` | Immediate | உடனடி | no issue found | |
| `tasks.priority.standard` | Standard | வழக்கமான | C: "Standard" priority rendered வழக்கமான ("usual"); check it reads as a priority level next to உடனடி (immediate). | |
| `tasks.provenance` | Where it came from | இது எங்கிருந்து வந்தது | no issue found | |
| `tasks.reasonCodes` | Reason codes | காரணக் குறியீடுகள் | no issue found | |
| `tasks.resolve` | Resolve | தீர்க்கவும் | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.resolveBody` | This closes the task. | இது பணியைத் தீர்க்கிறது. | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.resolveTitle` | Resolve this task? | இந்தப் பணியைத் தீர்க்கவா? | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.resolvedAt` | Closed at | தீர்க்கப்பட்ட நேரம் | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.source` | Source message | மூலச் செய்தி | no issue found | |
| `tasks.status.acknowledged` | Acknowledged | ஏற்றுக்கொள்ளப்பட்டவை | no issue found | |
| `tasks.status.open` | Open | திறந்தவை | no issue found | |
| `tasks.status.resolved` | Resolved | தீர்க்கப்பட்டவை | no issue found; earlier AI pass: corrected (term standardised) | |
| `tasks.title` | Tasks | பணிகள் | no issue found | |
| `tasks.urgent` | Urgent | அவசரம் | no issue found | |
| `tasks.urgentNote` | Shown first because of its kind or priority. | வகை அல்லது முன்னுரிமை காரணமாக முதலில் காட்டப்படுகிறது. | no issue found | |
| `tasks.you` | You | நீங்கள் | no issue found | |
| `tasksHeld.byOther` | Someone else is holding this task. Only the person who acknowledged it can close it. | இந்தப் பணியை வேறொருவர் வைத்திருக்கிறார். அதை ஏற்றுக்கொண்டவர் மட்டுமே இதை மூட முடியும். | no issue found | |
| `time.zoneNote` | Times are in the clinic time zone: {{zone}}. | நேரங்கள் மருத்துவமனையின் நேர மண்டலத்தில் உள்ளன: {{zone}}. | no issue found | |
| `time.zoneNotePlaceholder` | Times are in the clinic time zone: {{zone}} (demo placeholder: the clinic has not set one). | நேரங்கள் மருத்துவமனையின் நேர மண்டலத்தில் உள்ளன: {{zone}} (டெமோ இடஒதுக்கீடு: மருத்துவமனை ஒன்றை அமைக்கவில்லை). | B: "demo placeholder" rendered டெமோ இடஒதுக்கீடு (reservation); see scheduling.zoneNote. | |
| `upload.another` | Upload another | மற்றொன்றைப் பதிவேற்று | no issue found | |
| `upload.conflict` | That upload conflicts with an earlier one. Start again. | அந்தப் பதிவேற்றம் முந்தையதுடன் முரண்படுகிறது. மீண்டும் தொடங்கவும். | no issue found | |
| `upload.done` | Uploaded | பதிவேற்றப்பட்டது | no issue found | |
| `upload.file` | File | கோப்பு | no issue found | |
| `upload.fileHint` | PDF, JPEG or PNG. | PDF, JPEG அல்லது PNG. | no issue found | |
| `upload.invalid` | The file was not accepted. | கோப்பு ஏற்கப்படவில்லை. | no issue found | |
| `upload.kind` | Kind of document | ஆவண வகை | no issue found | |
| `upload.kinds.other` | Other | மற்றவை | no issue found | |
| `upload.kinds.referral` | Referral | பரிந்துரை | no issue found | |
| `upload.kinds.report` | Report | அறிக்கை | no issue found | |
| `upload.local.empty` | That file is empty. | அந்தக் கோப்பு காலியாக உள்ளது. | no issue found | |
| `upload.local.none` | Choose a file first. | முதலில் ஒரு கோப்பைத் தேர்ந்தெடுக்கவும். | no issue found | |
| `upload.local.type` | Only PDF, JPEG or PNG files can be uploaded. | PDF, JPEG அல்லது PNG கோப்புகளை மட்டுமே பதிவேற்ற முடியும். | no issue found | |
| `upload.notCheckedNote` | This file was not checked. | இந்தக் கோப்பு சரிபார்க்கப்படவில்லை. | no issue found | |
| `upload.notScannedNote` | This file has not been scanned yet. | இந்தக் கோப்பு இன்னும் ஸ்கேன் செய்யப்படவில்லை. | no issue found | |
| `upload.openIt` | Open the referral | பரிந்துரையைத் திற | no issue found | |
| `upload.phase.checking` | Checking the file… | கோப்பைச் சரிபார்க்கிறது… | no issue found | |
| `upload.phase.preparing` | Preparing the upload… | பதிவேற்றத்தைத் தயாரிக்கிறது… | no issue found | |
| `upload.phase.sending` | Sending the file… | கோப்பை அனுப்புகிறது… | no issue found | |
| `upload.scan.clean` | Clean | சுத்தம் | C: "Clean" (a file scan found nothing) rendered சுத்தம் ("cleanliness"). | |
| `upload.scan.not_checked` | Not checked | சரிபார்க்கப்படவில்லை | no issue found | |
| `upload.scan.not_scanned` | Not scanned yet | இன்னும் ஸ்கேன் செய்யப்படவில்லை | no issue found | |
| `upload.scanStatus` | Scan status | ஸ்கேன் நிலை | no issue found | |
| `upload.submit` | Upload | பதிவேற்று | no issue found | |
| `upload.title` | Upload a document | ஆவணத்தைப் பதிவேற்று | no issue found | |
