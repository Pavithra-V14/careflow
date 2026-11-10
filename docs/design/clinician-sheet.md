# CareFlow: decisions we need from the clinical side

For: Priya and the clinical lead. Plain language. **We have not filled in any answer and we are not suggesting any.** Until a question has a signed answer, the software refuses to run that part with real patient data (it stops and asks a person). "Options" lists only the choices the software can express, not a recommendation. Item numbers match `clinician-decisions.md`. Please write your answer, your name and the date.

## A. Who looks at what, and how fast

| # | Question | Why it matters | Options the software has | Answer / name / date |
|---|---|---|---|---|
| 1 | Who reviews a triage suggestion for a referral? | The suggestion waits until this role decides. | nurse / doctor / either | |
| 2 | How urgent is a triage-review task when it is created? | It sets the order the review queue is worked in. | immediate / standard | |
| 3 | How urgent is a "held referral" task (we could not assess the letter, extraction failed, no date of birth, key conflict, empty letter)? | A held letter is not safe to ignore; this sets how it ranks. | immediate / standard | |
| 14 | When the identity check refuses a letter's details, who handles it: front desk or a nurse? (An internal key clash already goes to a nurse.) | Wrong owner means the task is missed or mishandled. Currently front desk, marked "pending clinic confirmation". | front desk / nurse | |
| 15 | How urgent is the task raised when a referral job fails five times and gives up? | Otherwise a referral can sit unseen. | immediate / standard | |
| 6 | How long may each kind of review task wait, and when does an unanswered one escalate and to whom? | Without deadlines nothing escalates. | (we need a time and an escalation target per task kind) | |

## B. Reading letters and matching them to protocols

| # | Question | Why it matters | Options the software has | Answer / name / date |
|---|---|---|---|---|
| 13 | What counts as a confident match of a person (which identifiers must agree)? | Decides when a letter is joined to an existing patient and when a person checks. Real patient data cannot be used until this is signed. | (a list of identifiers; you choose which are "strong") | |
| 9 | The red-flag rules: which phrases, patterns and vital signs must escalate a case at once? | These run first on every letter, message and voice call. A rule goes live only with two clinicians' approval. | (rule text; two-person approval) | |
| 8 | The protocol cards: their content, risk class, priority, which clinic or specialty each applies to, and the test set used to check retrieval. | Triage suggestions are only as good as the cards. No cards exist yet. | (clinician-authored) | |
| 7 | How sure must the system be before it offers a card? Below that it says "no match". | Too loose offers wrong cards; too strict sends everything to a person. | (two thresholds, set from the test set) | |
| 11 | For each card, which facts in the letter must be confirmed (e.g. a drug, a dose, a date) before the card can be used without a person checking? | Only these facts raise a review when the letter's text does not support them. | (a list per card) | |
| 10 | Which unit spellings are the same (e.g. mg and milligram), and which conversions are allowed? | Today a unit must match exactly, otherwise a person checks. | (a table; none exists) | |

## C. Decisions, auto-approval and what patients are told

| # | Question | Why it matters | Options the software has | Answer / name / date |
|---|---|---|---|---|
| 4 | The list of reasons a reviewer can choose when they change or reject a suggestion. | Lets us learn from corrections. | (a short list) | |
| 5 | May the system approve one kind of low-risk suggestion without a person? If yes: what makes a card "low-risk", which priorities qualify, and how confident must it be? | Today nothing is approved automatically. | no auto-approval / (a risk label + priorities + a confidence level) | |
| 12 | The wording of the message a patient sees when their message needs urgent attention, and any other automatic reply. | Nothing is sent to a patient until this is approved. | (approved text and languages) | |

## D. Appointments and cancellations (Phase 3)

| # | Question | Why it matters | Options the software has | Answer / name / date |
|---|---|---|---|---|
| 16 | The clinic's priority levels: a code, a label and an order (1 is served first), approved by a named doctor. | A freed slot is offered to the highest priority waiting patient first. With none defined, nobody is offered a freed slot. | (a list you define) | |
| 17 | How sure must the system be before it SUGGESTS to the front desk that a patient's reply means "yes, I accept"? (A person always confirms; booking without a click is not built.) | Wrong readings of a reply lead to wrong bookings. | (a confidence level per clinic) | |
| 18 | How long does an appointment offer stay open, and how long is the slot held for it? | Too short loses the slot; too long blocks it. | (two durations) | |
| 19 | How many next patients are tried before a person is told "no one could be offered"? | Stops a freed slot from being passed along forever. | (a number) | |
| 20 | The wording of the offer, the confirmation and the "that time is gone" messages, and their languages. | Nothing is sent without approved wording. | (approved text per language) | |
| 21 | May any mode send an offer without the front desk pressing "send"? | Currently no: a person always sends. | no / (name the mode and conditions) | |
| 22 | How urgent, and for which role, are scheduling tasks such as "slot freed, no ranked candidates", "reply needs a person", "offer blocked by a red flag"? | Only needed if such tasks are added later. | (priority and role per task) | |

## E. Identity confirmation: revoking a mistaken confirmation (migration 0025)

| # | Question | Why it matters | Options the software has | Answer / name / date |
|---|---|---|---|---|
| 23 | The list of reasons front desk can give when they take back a confirmation of who a patient is. Today: wrong person, planted or suspect record, mistaken click. | A short fixed list (no free text, which could hold patient details) shows why records get demoted. Operational words, not clinical content. | (the three words above; change, add or remove) | |
| 24 | When a confirmation is taken back, a nurse or doctor is asked to re-check what was linked to that record in the meantime. Who gets that task, and how urgent is it? | Until this is answered NO task is raised: the take-back is recorded, the screen says no task was raised, and nobody re-checks. | role: nurse or doctor; urgency: immediate or standard | |
| 25 | What does it take to CLOSE a red-flag review task: who may close it, what must be recorded (what was done, who was told), and does a second person confirm? | Until this is answered no route closes a red-flag task: a nurse can acknowledge it, nothing more. | (who, what is recorded, one or two people) | |
| 26 | What may a reviewer EDIT in a triage proposal before approving it? | Until this is answered the console shows "edit" as "Coming soon" and the server refuses it with a fixed response: a reviewer can only approve or reject. | (see the options below) | |

### AI-drafted options, not a decision (items 23 to 26)

These were drafted by the assistant to make the questions easier to answer. They are **not** recommendations from the clinical side, **nothing here is answered**, and every answer cell above stays blank until a named person fills it in. Where a safe default is stated, that is what the software does *until* the question is answered.

**23. Revoke reasons.** *AI-drafted options, not a decision.* Proposed closed list: `wrong_person`, `planted_or_suspect`, `mistaken_click`, `other`. No free text. (The software today knows the first three; `other` would be new.)

**24. Who re-checks, and how urgently.** *AI-drafted options, not a decision.* Proposed: the re-check goes to a front-desk supervisor or an admin; a revoke with reason `planted_or_suspect` gets the highest priority and also an alert to an admin; the other reasons get the lowest priority. (Today only a nurse or doctor can be named, and there is no admin alert; both would be new.) Safe default until answered: no task is raised, and the screen says so. **Note: There is no front-desk supervisor role and no admin alert today; adding either is a new role or feature and needs a design note and the user's OK.** Wiring these answers into the revoke route (and any migration, e.g. for `other`) waits for the answers to items 23 and 24 and is then built once with the final list.

**25. Closing a red-flag review task.** *AI-drafted options, not a decision.* (a) a nurse or a doctor closes it, with a closed reason and no free text; (b) a doctor only; (c) a second person must confirm. Trade-off: the stricter the option, the safer and the slower. Safe default until answered: no route closes one (a nurse can acknowledge it, nothing more).

**26. What a reviewer may edit in a triage proposal.** *AI-drafted options, not a decision.* One engineering rule holds whatever is chosen (it is not a clinical question): the original stays, and an edit is a new append-only event recording before and after, who, when and a reason code; nothing is overwritten. Options for which fields are editable: (a) priority only, (b) department only, (c) both. Safe default until answered: "edit" is refused with the fixed response.

## F. Recurrence rules, plans and readiness content (added 2026-11-07; clinician-OWNED items, nothing here has a software default)

| # | Question | What the software does until it is answered | Answer (clinician, name, date) |
|---|---|---|---|
| 27 | The care-gap recurrence rules: for each kind of care episode, which recurring checks apply, how often (every N days, or once a year in month M), and how many days of grace before it counts as overdue. | The rules come only from the required setting `CAREFLOW_CARE_GAP_RULES`. Unset means no episode can be created. The demo pack carries ONE labelled placeholder rule (`demo_rule`, 30 days, 5 days of grace) that is not a clinical decision. | |
| 28 | The care-plan loop: what a doctor may ask the system to wait for (which result codes), the conditions on a result that count as met or not met, and what a met condition leads to. | The plan MODEL has no approved route, so every plan is held for a doctor; the closed plan schema is validated by code. The demo task settings (doctor, normal priority) are placeholders. | |
| 29 | Visit Readiness content: the intake facts to ask for, the wording of each question (sent exactly as written), the readiness checklist per visit type, and the answers that count as "not done". | Required setting `CAREFLOW_VISIT_READINESS_CONTENT`. Unset means no readiness run starts. The demo pack carries clearly labelled placeholder questions. | |
| 30 | Whether a sample drawn for after-the-fact review may ever be re-checked by the person who made the original decision. | Today the database refuses it (decided 2026-11-07). | |
| 31 | The result codes the clinic records and the measure names each code carries (for example which tests exist and what each reports). A result code is a short label such as the prefix a care-plan wait matches; a measure name is a lower-case label for one number. | Only labelled demo placeholders exist (`DEMO-T1.a`, `demo_value`); the system invents no code and no measure name, and a plan can wait only for a code a doctor writes. | |
| 32 | When a result replaces an earlier one of the same test: does "the same effective date" mean the same instant, or the same calendar day in the clinic's time zone? (A new value on the same day for the same test is either a correction of a wrong value or a fresh measurement.) | Today: the exact same instant counts as a correction, and a writer who declares an amendment for that case is reviewed as a correction anyway. `CLINICAL-PLACEHOLDER: needs clinician sign-off`. | |
| 33 | When a result is corrected, which decisions need a person to look again, who looks, and how urgently? | Today: every care-plan step that closed on the old value, and every step still waiting on it, raises a review task for a doctor at standard priority, with no 30-day window (steps carry no decision time). `CLINICAL-PLACEHOLDER: needs clinician sign-off`. | |

| 34 | For the retention report: what counts as an encounter for "8 years after the last encounter" (today: a confirmed booking whose slot has started; a cancelled booking is not one), and how should minor and pregnancy records be told apart (the database holds no fact it could use, so the report does not assess them)? | `CLINICAL-PLACEHOLDER: needs clinician sign-off`. Patients with no encounter on record are counted apart and never as past the period. | |
## What happens with your answers
Each answer goes in as a setting or as clinician-authored data, with your name and the date, and is reviewed before it goes live. Where two people must approve (red-flag rules), both names are recorded. If any answer is "not yet", the software keeps sending that case to a person.
