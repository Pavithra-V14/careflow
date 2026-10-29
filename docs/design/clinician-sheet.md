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

## What happens with your answers
Each answer goes in as a setting or as clinician-authored data, with your name and the date, and is reviewed before it goes live. Where two people must approve (red-flag rules), both names are recorded. If any answer is "not yet", the software keeps sending that case to a person.
