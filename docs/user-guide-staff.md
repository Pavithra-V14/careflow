# CareFlow staff console: user guide

This guide describes what the staff console screens do, as built on 20 November 2026. It describes only what is on the screens. It gives no clinical advice, no thresholds and no policy: where a screen shows wording that a clinician or counsel still has to write, this guide says so and does not fill the gap.

**How to read the labels in this guide**

- **Not tested live** means the screen has only been tried against the console's own fake data (unit tests and browser tests), or was refused when walked against the real backend. Where it says "needs second factor", the backend's database checks for a recently verified second factor, and a password-only development session is refused. This is recorded as not tested live, not as a failure.
- **Walked live** means a screen was tried against the real (demonstration) backend with a made-up patient, the user typing every password and authenticator code. The summary table "What has and has not been walked live" below says exactly which screens, for which role, and which writes. Anything not in that table as walked is **not tested live**.
- **Tamil:** every screen can be switched to Tamil. The Tamil wording has been checked by a machine only. **No Tamil string has been reviewed by a human reader**, and the Tamil reader must still approve all of it before real use.
- The console only hides links a role cannot use. The backend decides what each role may do. If you open something your role may not use, the screen says "This is not available to your role."

## Before you start

1. **Signing in.** Sign in with your e-mail and password, then the code from your authenticator app. If you have not set up an authenticator yet, the console walks you through it first. If you hold more than one role, you pick the role to work in.
2. **Environment badge.** A badge on every screen says whether this is a live or a test environment. A demo or test environment uses made-up people only.
3. **Language.** The language switcher is on every screen (English or Tamil).
4. **Signing out for being idle.** If the page is not used for a while, a box asks "Are you still there?" with a countdown. The only way to stay signed in is the button "Stay signed in". If the time runs out you are signed out and the screen is cleared. How long the wait is, is a setting the privacy owner chooses; a demo build uses labelled placeholders.
5. **Going offline.** A banner says "You are offline. Changes cannot be saved until the connection is back." What you typed stays on the screen; send it again when the connection returns.
6. **Every change asks first.** Nothing is saved until you confirm in a box. The box says what will be recorded and that it is kept under your name.

## What has and has not been walked live (20 November 2026)

| Role and screen | Status |
|---|---|
| Front desk: sign-in with a second factor | walked live |
| Front desk: patient search, open a record, confirm identity | walked live (the confirmation is a write on the made-up patient: 200) |
| Front desk: scheduling lists (locations, visit types, providers, slots) | walked live, read-only; no scheduling action walked |
| Front desk: message screen list, "Show names" | walked live, read-only |
| Front desk: family link requests page | walked live with the second factor: loads, empty list; verify, reject and revoke not tested live |
| Front desk: referrals, tasks, alerts, patient requests, identity lists | walked live earlier, read-only, on a password-only session; actions not walked |
| Front desk: portal claims (approve), break-glass, erasure | not tested live (needs second factor) |
| Front desk: visit readiness | the backend refused it ("your role may not do this"): it is a nurse and doctor section |
| Supervisor (also holding front desk) | walked live earlier, read-only |
| Nurse and doctor: referral review (read), tasks, patients, messages, scheduling and care reads | walked live earlier, read-only, on a password-only session |
| Nurse and doctor: visit readiness list, the staff view of a visit's preparation, cards and templates lists | walked live with the second factor (read-only): they load. The readiness run for the demo visit said "Visit readiness is not set up yet"; the preparation view and both card lists were empty because nothing had been supplied |
| Nurse and doctor: recording a referral decision, writing a draft, submitting, approving or rejecting | not tested live |
| Admin: the plain lists | walked live earlier, read-only |
| Admin: accounts, voice, recovery, grievances, break-glass review, autonomy changes | not tested live (needs second factor) |
| Auditor: audit log | not tested live |

## Messages you may see (in every role)

These sentences are fixed. The screen never shows text sent back by the server.

| Message | What it means |
|---|---|
| Please sign in again. | Your session ended or is no longer valid. Sign in again. |
| This is not available to your role. | Your role may not do this. Nothing was changed. |
| That could not be found. | The thing you asked for is not there (or is not visible to you). |
| That conflicts with the current state. Refresh and try again. | Someone else changed it, or it was already done. Refresh the page and look again. |
| Check what you entered and try again. | A field was not accepted. Correct it and send it again. |
| Too many requests. Wait a moment and try again. | You are going too fast. Wait, then try again. |
| This is not available right now. Try again in a moment. | The service, or a switch the hospital has not turned on yet, is not available. Nothing was changed. |
| The connection failed. Check it and try again. | The network failed. |
| Something went wrong. Try again. | An unexpected failure. Nothing is assumed to have been saved. |
| A fresh authenticator code is needed to do this. Nothing was changed. | This step needs a recently verified second factor. Enter a new code when asked. |
| You are offline. Nothing was saved. What you typed is still here. | See "Going offline" above. |
| Nothing was recorded / Nothing was saved / Nothing was shown | Said after a refusal, so you know the action did not happen. |

When a retry happens after a failure, the console sends the same request key, so repeating a confirmation never records a second copy. Changing what you typed is treated as a new request.

---

## Front desk

**What the role can do.** Front desk handles referrals and documents, patient identity, scheduling, patient requests and claims, family links, erasure requests, messages and break-glass access. Sections shown: Referrals, Tasks, Patients, Messages, Family links, Identity, Scheduling, Alerts, Portal claims, Patient requests, Break-glass and Erasure requests.

### Referrals and uploads

1. Open **Referrals** for the list of referrals (document, received, type, state, open holds, decision).
2. **Upload a document** (front desk and nurse): choose the kind (Referral, Report or Other), choose a PDF, JPEG or PNG file, press **Upload**. The screen reports the scan status: Clean, Not scanned yet, or Not checked. An empty file or a wrong file type is refused before anything is sent.
3. **Open referral** shows holds, the triage proposal (if the reviewer may see it) and whether a decision has been made.
4. **Run the referral** queues the Front Door run for that document after a confirmation. It does not message the patient. Any red flag it finds arrives as a task for a person. "Runs cannot be started yet (not set up). Nothing was queued." means the switch is not on.

Front desk does not decide referrals; see Nurse and Doctor.

### Tasks

**Tasks** lists tasks (kind, priority, status, assigned to, created). Red-flag and "Unassessable" tasks are shown first and marked Urgent. Open a task to read where it came from and its source message. **Acknowledge** records you as the person who took it; **Resolve** closes it (a confirmation first). Some kinds are closed through their own screen: the screen says "This kind of task is closed through its own screen, not here." If someone else holds the task, only they can close it.

### Patients and identity

1. **Patients → Search:** type a full name and date of birth (phone is optional). A match is shown only for exactly those details. Nothing typed is saved. If nothing is found, "No matching record was found."
2. **Open a record by id** if you already have it.
3. A record shows its latest identity event. **Confirm identity** records, under your name, that you confirmed this patient's identity. It is offered whenever the latest identity event is not a confirmation, **even on a record that says "Verified"** (for example one registered by the front desk, whose latest event is "None"); it is not offered once the latest event is a confirmation, or for a merged-away record. *Walked live: found exactly one demo record, confirmed it (the confirm call answered 200).* **Revoke confirmation** (reason: Wrong person, Planted or suspect, or Mistaken click) moves the record back to unverified so a person reviews any match; nothing already linked is undone. The screen then says whether an administrator alert or a review task was raised, or why not.
4. **Open the full details** shows the name, date of birth and phone, and is recorded in the audit log (who, role, which record; never the contents).
5. **Register a patient:** search first. Register only when the person is not found. The result is one of: matched an existing record, a new record created, possible matches found (nothing is linked automatically; a person decides), or the request was already saved.
6. **Identity** lists possible duplicates and merges. **Approve** and **Unmerge** buttons are **switched off** in this build (the second-factor check they need has not been confirmed).

### Scheduling

**Scheduling** holds providers, locations, visit types, slots, holds, bookings, the waitlist and offers. Times are shown in the clinic's time zone (a demo placeholder until the clinic sets one).

- **Hold** keeps a slot for the time you enter. **Book** books it for the patient you choose. **Cancel** frees the slot and ranks the waitlist for you to look at; nobody is contacted and nobody is booked. **Join the waitlist** adds a patient to the queue for a visit type and priority. **Release** gives a held slot back.
- **Offers:** the draft offer shows ranked candidates and the draft text, read-only. It is worked out when you ask and stored nowhere. **Nothing is sent to anyone from this screen.** After you deliver an offer yourself, **Mark delivered** records that you did it. This cannot be undone.
- **Offer run for a slot (backfill):** the waitlist is ranked and the run stops at each candidate for you to decide: Send (holds the slot and records the offer; you deliver it yourself), Skip, or Stop. A patient's reply is typed in or picked from a stored message, and the red-flag rules read it first.
- **Set-up:** front desk or admin can add a provider (no sign-in is linked), a location, a visit type (a length you enter; nothing guesses one) and a slot.
- Bookings, waitlist and offer lists can show patient names on request: see "Show names" below.

### Messages and "Show names"

**Messages** lists the clinic's conversations: ids, a closed topic (Care, Appointments, Safety), who sent each message and when. It never shows message text in the list. **Open** a conversation, then **Read** one message at a time: a confirmation says the reading is recorded under your name, and the text is shown only while you keep it open. **Screen for red flags** runs the red-flag rules on a stored message and records the result: "no red flag", "a red flag (a nurse task was raised)" or "could not assess (a nurse task was raised)". Nothing is sent to the patient. If the clinician-written patient wording does not exist yet, the screen says no reply is sent.

**Show names** (on lists that show patient ids) shows name, year of birth and the last four digits of the phone for up to 25 patients at a time, after a confirmation. It is recorded as one audit entry that holds only the count. Names go when you leave the screen.

*Messages and Show names were walked live (read-only, front desk, nurse and doctor) and passed.*

### Alerts

**Alerts** lists alerts aimed at a role you hold. **Acknowledge** records, once, that you have seen it. "Include acknowledged alerts" shows past ones.

### Patient requests

Requests patients made about their own record: erasure, a correction (only the name of the detail is shown, never a value) and a nominee. The status buttons offered on each row are exactly the changes the server allows from that status (Mark as seen, Resolve, Decline); a row with none shows no button. Each change tells the patient with an in-app notice and records your name; it changes nothing about the record. You can link an erasure request you started for that patient. "That change is not allowed from the current status" means the request moved on.

### Portal claims

People who registered on the portal ask to be linked to a record. The list shows only a request id and a time. **Approve** links the person to the confirmed record you choose; **Reject** links nobody. Results you may see: already resolved; that record cannot be claimed; this person is already linked; the person has not given the required consent yet. *Not tested live: needs second factor.*

### Family links

Requests by patients to be linked as the guardian of a child or the carer of an adult. The list shows ids, a type, a status (Waiting for a check, Verified, Rejected, Withdrawn) and, if you ask, names. **A verified link grants nothing in this build; it is only recorded.**

- **Verify:** choose the dependant's record (found with search) and answer "Was the linked person's consent obtained?" (nothing is pre-chosen). The server checks the clinic's rule using the age from the stored date of birth; the age is never typed here.
- **Reject** records one reason word (lowercase letters, digits, underscores).
- **Revoke** closes a request or a verified link with one reason word; the patient then sees it as withdrawn.
- Messages: "the clinic has not entered its rules" means family links are off for this clinic until counsel's values are entered (a demo uses placeholders); "Family links are not turned on yet"; "The clinic's rule does not allow this link for the dependant's age"; "needs the linked person's consent"; "no longer has an active account"; "already linked".

*Not tested live: needs second factor (the database checks for it; password-only dev sessions are refused).*

### Break-glass access

Opens a time-limited read of one patient's messages with a reason code from the clinic's list. Every read is recorded under your name and reviewed afterwards by an administrator who did not open it. *Not tested live: needs second factor.*

### Erasure requests

Erasing a patient's identity takes two front desk people: one asks, a different one approves, each with a recent authenticator code. Approving removes the patient's encrypted name, date of birth and phone number and cannot be undone from this screen; the request, its audit rows, the record number and the matching fingerprints are kept. **In this build asking, approving and cancelling are switched off** (the list is read-only) until the second-factor check is confirmed. *Not tested live: needs second factor.*

---

## Front desk supervisor

**What the role can do.** The supervisor role adds oversight of **Tasks**. In this console the supervisor account also holds the front desk role (decided on 14 November 2026), so everything in the Front desk section above also applies. There is no screen that only a supervisor sees.

*A read-only live walk of the supervisor account (also holding front desk) passed: 62 calls, no mismatches. The supervisor-only walk was cancelled by decision.*

---

## Nurse

**What the role can do.** Sections: Referrals, Tasks, Patients, Messages, Cards and templates, Identity, Scheduling, Alerts, Care, Visit readiness, Notes and Break-glass. A nurse does not see the Admin section.

### Referral review

1. Open a referral. If a triage proposal can be shown, the **Reviewer view** shows: the kind of task, the engine, the confidence (one decimal), the model and the decision rule as recorded, the rule that triggered review, the evidence (field, source) and the alternatives considered with their probabilities, and what happens if you approve or reject. **A suggestion is shown "as the system recorded it. It is not a decision: a person decides."**
2. If no proposal can be shown, the decision form is not offered: "The decision is offered only when the proposal can be shown. If this referral was only just run, wait a moment and reload."
3. **Record a decision:** choose Approve or Reject (nothing is chosen for you; Edit is shown as Coming soon and cannot be chosen). If the clinic has a list of reasons you may choose one; a Reject needs one.
4. **Optional question, "Was this escalation right?"** choose "This escalation was unnecessary" or "The system should have escalated this and did not", or leave it blank. Blank means not recorded, not accurate. A recorded view cannot be changed later.
5. Press **Record decision**, then confirm. The decision is kept under your name. One decision per referral: "The referral is not waiting for a decision, or a different decision is already recorded", or, for a repeat that differs only in the optional question, "The first decision stands and cannot be changed."

*The referral review was walked live as a doctor against a demo proposal (read-only). Recording a decision was not tested live.*

### Cards and templates

*Not tested live: needs second factor (the database checks for it; password-only dev sessions are refused).* Everything on this screen is typed by a nurse or a doctor; **nothing is supplied by the system**.

1. **Write a new draft:** choose Card or Template, a code (lowercase letters, digits, dots, dashes, underscores; one open draft per code), a title and the text. A card also needs synonyms, phrases patients use (one per line) and who may read it (at least one role). **Save the draft.** A draft is never in use.
2. **Edit** your own draft; **Submit** freezes it and sends it to a doctor (you cannot edit it afterwards).
3. A nurse cannot approve. A doctor who did not write the draft decides: see the Doctor section.

### Visit readiness and visit preparation

*Visit readiness: not tested live: needs second factor.* The screen lists upcoming visits. Open one to show the readiness run: its status, what it is waiting for (the patient's reply or nothing), the current item and any review task. **Start readiness** starts the run or shows the one already running. **Relay the patient's reply** records what the patient said, or that no reply came; the rules read the reply first and it is stored encrypted. The clinic's questions and checklist are not shown here, only where the run is.

**Visit preparation (what the patient marked)** is a read-only block under the run: the hospital's own checklist for the visit type and what the patient marked (Done, Not done, Not marked). *Not tested live: needs second factor.*

### Care, Notes, medications

- **Care:** care plans (a plan's text is never shown on the list; a doctor writes a plan, the rules screen it, and a doctor approves it before anything runs), results (appended, never edited; a correction is a new result naming the one it replaces; an empty value is recorded as unknown, not zero), care gaps (barriers, episodes, rule dates). *Not tested live: needs second factor.*
- **Notes:** notes are never edited; a correction is an addendum naming the note it adds to. *Not tested live: needs second factor.*
- **Medications** (on a patient record): statements are never edited or deleted. A correction or a stop is a new row; an empty end means "no end recorded", not "still taking".

### Tasks, Alerts, Patients, Messages, Scheduling

As described under Front desk, with the role's own limits: a nurse can read the lists, screen a message, and take or close tasks the server allows, but cannot hold, book, cancel or join the waitlist, confirm identity, or search patients (those are front desk).

---

## Doctor

**What the role can do.** Everything a nurse can do on referrals, patients, messages, care, notes, readiness and Cards and templates, plus decisions that need a doctor. Sections: Referrals, Tasks, Patients, Messages, Cards and templates, Identity, Scheduling, Alerts, Care, Visit readiness, Notes, Break-glass, Admin (Autonomy and Red-flag rules tabs only).

- **Referral review:** as under Nurse.
- **Care plans:** a doctor writes a plan and approves or rejects a proposed one.
- **Cards and templates:** a doctor who did **not** write a submitted draft can **Approve** or **Reject** it. Approve shows the text exactly as it will be used; approving makes it the next version ("Approved as version N"). Reject needs one reason word. If you wrote the draft: "You wrote this draft. A different doctor must decide." *Not tested live: needs second factor.*
- **Red-flag rules (Admin → Red-flag rules):** shows each version of a rule with the decisions made on it, exactly as the system holds it. A draft becomes approved only when **two different doctors** approve it; the author cannot be one of them. One rejection closes a version for good. A doctor can also write a draft: a code and any number of literal phrases and word sets (no other kind of criterion, no regular expression). **This guide gives no rule wording; rule content must come from clinicians.**
- **Autonomy (Admin → Autonomy):** shows the clinic's mode (shadow, assisted) and its history. Switching to autonomous is only done through a proposal that a second person holding the other role approves within 7 days; nothing changes until then.

---

## Admin

**What the role can do.** Sections: Tasks, Scheduling, Alerts, Dashboard, Care, Break-glass, and **Admin** with tabs: Accounts, Autonomy, Red-flag rules, Reviewer, Cost, Voice, Recovery, Grievances.

- **Dashboard:** metrics, or "There is no metrics data to show."
- **Accounts:** accounts that hold or held a role at this clinic (ids, a status and role words; no e-mail is stored). **Invite** creates an account with one role (they activate it themselves, using their own authenticator code; nobody else can do it for them). **Suspend** and **Reinstate**, **Grant** and **Revoke** a role. *Not tested live: needs second factor.*
- **Autonomy** and **Red-flag rules:** admins read the lists and history and decide nothing; the decisions are doctors'.
- **Reviewer:** reviewer decision statistics for a period.
- **Cost:** monthly cost from the spend ledger, actual and at the planned scale. A term that is not configured is shown as not configured.
- **Voice:** voice-session transcripts can be read and purged only by an administrator with a fresh second factor; every read and purge is audited. A read shows the text only while the window is open. Purge deletes expired transcript chunks only and cannot be undone. *Not tested live: needs second factor.*
- **Recovery:** resets a staff member's second factor. You cannot ask for your own, and a second administrator who is neither the requester nor the person must approve. Complete resets it; the person sets up a new one at the next sign-in. *Not tested live: needs second factor.*
- **Grievances:** grievances sent by patients, shown as the patient wrote them while they can be read. **Acknowledge** records you saw it; **Resolve** closes it and a resolved grievance is final. *Not tested live: needs second factor.*
- **Break-glass review:** an administrator who did not open an access reviews it afterwards (Appropriate or Inappropriate); the review cannot be changed, and you cannot review your own.

---

## Auditor

The **Audit log** shows every row as the system recorded it, newest first, for the clinics you audit. All filters are optional (action, kind of record, record id, person, from, until; dates are in your browser's time zone). **Read the log**, then **Older rows**. Nothing here can be changed.

---

## Where this guide stops

- It describes the screens, not hospital policy. Where a screen shows "PLACEHOLDER" or says a clinician has not written the wording yet, the wording is genuinely missing and this guide does not supply it.
- Switches the hospital has not turned on (family links, visit preparation, cards and templates, online booking, voice) show a calm "not turned on yet" message instead of the feature.
- Real use needs the human review of every Tamil string and the privacy owner's choices (for example the idle sign-out time).
