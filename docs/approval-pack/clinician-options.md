# Clinician sheet: options for items 23 to 34

> **AI-prepared, not approved by Priya.** Prepared 2026-11-13 from `docs/design/clinician-sheet.md` and `docs/design/clinician-decisions.md`. Not clinical advice. Never loaded by the application; never marked approved. Every "suggested answer" below is a starting point for Priya to accept, change or reject, not a clinical decision. The answer cells stay blank until a named person fills them in and signs.

**Rules this sheet follows.** Where an item is red-flag, triage or other safety content, this sheet gives only a structure and the questions to ask. It contains no rule, no threshold, no interval, no question wording and no test code. Those come from the clinician (`# CLINICAL-PLACEHOLDER: needs clinician sign-off`). Where an item is about who does what and how fast (process, not content), it gives options with trade-offs and one suggested answer.

**How to read each item.** Options are A, B, C. "Safe default" is what the software does until the item is answered. "Suggested" is the one option the assistant would pick if forced, with the reason. Items 23 to 26 already have a short options note in the original sheet; this file extends it and does not replace it.

---

## 23. Reasons for taking back an identity confirmation (process, closed word list)

| Option | What it is | Trade-off |
|---|---|---|
| A | Keep today's three words: `wrong_person`, `planted_or_suspect`, `mistaken_click`. | Nothing new to build. A revoke that fits none of the three has to be forced into one, which blurs the reporting. |
| B | A, plus `other`. | Honest catch-all; one small change. Risk: `other` becomes the habit and hides patterns. |
| C | A, plus `other` and a fourth specific word the clinic names (for example for a duplicate record). | Most informative; needs the clinic to name the extra word and keeps the list short. |

**Safe default:** the three words today. **Suggested: B.** A closed list with a catch-all keeps free text (which could hold patient details) out of the system, and a periodic look at how often `other` is used will show whether a real fourth word is needed.
**Question for Priya:** are the words understandable to front desk in plain language, and do they want them shown in another language?

## 24. Who re-checks after a confirmation is taken back, and how urgently (process)

| Option | What it is | Trade-off |
|---|---|---|
| A | A nurse gets a task; urgency depends on the reason (suspect record faster, mistaken click slower). | Uses a role that exists today. Nurses carry more of the queue. |
| B | A doctor gets the task for every reason. | Most senior look; slowest and most expensive. |
| C | A nurse for most reasons, a doctor when the reason is `planted_or_suspect`. | Matches effort to risk; two routing rules to maintain. |
| D | The front-desk supervisor role (built 2026-11-13, migration 0073) takes the task, optionally with an alert to an admin for the reasons the clinician names. | Keeps clinical staff out of a data-quality re-check; the supervisor sees only this kind of task. The role must be given to a named person, and the clinic must decide which reasons also alert an admin (`CAREFLOW_REVOKE_ALERT_REASONS`). |

**Safe default:** no task is raised, the screen says so, and nobody re-checks. (The front-desk supervisor role and the admin alert now exist but are switched on only by the settings `CAREFLOW_REVOKE_TASK_ROLE` and `CAREFLOW_REVOKE_ALERT_REASONS`, so option D is available.)
**Suggested: C**, because a suspect or planted record is the case where a wrong link does the most harm.
**Questions:** what counts as "linked in the meantime" for the clinic (messages, referrals, bookings)? How soon must the re-check happen, in hours? (That number is the clinic's.)

## 25. Closing a red-flag review task (safety content: structure and questions only)

This item concerns what it takes to close a task raised by an urgent-pattern match. The sheet does not propose what an urgent pattern is or what wording to use.

| Option | Structure |
|---|---|
| A | A nurse or a doctor closes it, recording a closed reason and who was told, no free text. |
| B | A doctor only closes it, same record. |
| C | One person closes it and a second person confirms before it counts as closed. |

Trade-off: the stricter the option, the safer and the slower; option C doubles the people needed for each task.
**Safe default:** no route closes a red-flag task; a nurse can acknowledge it and nothing more.
**Suggested: B or C, as Priya prefers; the assistant does not pick between them.** A red-flag closure is the highest-stakes closure in the system, so A is the least cautious.
**Questions to ask:** What must be recorded when it closes (what was done, who was told, when)? Is the list of "reasons for closing" a closed list, and who writes it? What is the maximum time a red-flag task may stay open before it escalates, and to whom? Does closing by a night-shift nurse need a doctor's countersignature the next day?

## 26. What a reviewer may edit in a triage proposal (triage content: structure and questions only)

The engineering rule holds whatever is chosen: the original stays, and an edit is a new append-only event recording before and after, who, when and a reason code.

| Option | Structure |
|---|---|
| A | Priority only. |
| B | Department only. |
| C | Both. |

Trade-off: fewer editable fields means fewer ways to change a proposal wrongly, but more rejections and re-runs; more fields means fewer re-runs and more to review afterward.
**Safe default:** "edit" is refused with a fixed response; a reviewer can only approve or reject.
**Suggested: A.** The narrowest useful edit; it can be widened later by a new decision, but a field once editable is hard to take back.
**Questions:** Which proposal fields exist today that a reviewer might want to change? Must an edit always carry a reason code, and from which list? Does an edited proposal need a second review?

## 27. Care-gap recurrence rules (clinical content: structure and questions only)

**Structure the clinic fills in (no values given here):** a list of episode kinds; for each, a list of recurring checks; for each check, either an interval in days or a fixed month each year, and a number of grace days before it counts as overdue. The software accepts this only through the setting `CAREFLOW_CARE_GAP_RULES`; unset means no episode can be created.
**Questions:** Which kinds of care episode does the clinic track? For each, which checks recur? Is each check an interval or an annual month? How many grace days? Who owns changing a rule later, and does a change need two people? Should a rule apply from the date it is saved or from the start of the episode?
**Safe default:** no rules; the demo pack carries one labelled placeholder rule that is not a clinical decision.
**Suggested:** start with one episode kind and one check, so the loop can be watched end to end before more are added.

## 28. The care-plan loop (clinical content: structure and questions only)

**Structure:** which result codes a plan may wait for; the condition on a result that counts as met or not met; what a met condition leads to (a task for a person, never an automatic action).
**Questions:** Which result codes may a doctor wait on? What conditions are expressible (above, below, present, absent)? Who is told when a condition is met, and how urgently? What happens when a condition is never met after some time, and after how long? May the system ever act without a person? (Today it may not.)
**Safe default:** the plan model has no approved route; every plan is held for a doctor.
**Suggested:** keep "a met condition raises a task for a doctor" as the only outcome for the first release.

## 29. Visit-readiness content (clinical content: structure and questions only)

**Structure:** a list of intake facts; for each, the exact words sent to the patient; for each visit type, which facts to ask and which checklist items to show; which answers count as "not done".
**Questions:** Which visit types are in scope? Which facts does each need? Who writes and approves the wording of each question, and in which languages? Which answers mean "not done", and what happens then? How often may a patient be re-asked?
**Safe default:** unset means no readiness run starts; the demo pack carries labelled placeholder questions.
**Suggested:** one visit type, a few facts, wording written by the clinician (never by the assistant).

## 30. Re-checking a sample by the person who made the decision (process)

| Option | What it is | Trade-off |
|---|---|---|
| A | Never: the original decider may not re-check their own sample (today). | Independent measurement of the error rate; needs a second reviewer to be available. |
| B | Allowed when no one else is available. | Keeps work moving; weakens the measurement. |
| C | Allowed, but flagged in the report as "self-rechecked". | Transparent; still weak evidence. |

**Safe default:** the database refuses it (decided 2026-11-07). **Suggested: A**, unchanged, because the point of the sample is an independent check. **Question:** how many reviewers can the clinic actually field, so that A does not stall?

## 31. Result codes and measure names (clinical content: structure and questions only)

**Structure:** a result code is a short label (the prefix a plan wait matches); a measure name is a lower-case label for one number. The clinic supplies both lists.
**Questions:** Which tests does the clinic record? What does each report (one number or several)? Does the clinic already use a standard coding system, and which? Who maintains the list, and how is a new code added?
**Safe default:** only labelled demo placeholders exist; the system invents no code.
**Suggested:** use the clinic's existing codes if any, rather than creating new ones.

## 32. "Same effective date" for a replaced result (process with a clinical meaning)

| Option | What it is | Trade-off |
|---|---|---|
| A | The same instant (today). | Precise; two readings the same day are treated as separate, even if one fixes the other. |
| B | The same calendar day in the clinic's time zone. | Matches how people think of "today's result"; a genuine second measurement the same day looks like a correction. |
| C | A, plus the writer must say whether a new value is a correction or a fresh measurement. | Most accurate; one more thing for the person entering the result. |

**Safe default:** the same instant counts as a correction, and a declared amendment is reviewed as a correction. `CLINICAL-PLACEHOLDER: needs clinician sign-off`.
**Suggested: C**, because the writer knows the intent and the software cannot guess it. **Questions:** Which tests are likely to be repeated the same day? Who decides when the writer is unsure?

## 33. When a result is corrected: who looks again, how urgently (process)

| Option | What it is | Trade-off |
|---|---|---|
| A | A doctor, standard priority, for every affected care-plan step (today). | Simple; may flood the doctor when a result feeds many steps. |
| B | A doctor, with priority depending on whether a step already closed on the old value or is still waiting. | Puts closed steps first; two priorities to maintain. |
| C | A nurse first, escalating to a doctor if the correction changes a met/not-met outcome. | Spreads the load; adds a hand-off. |

**Safe default:** a doctor task at standard priority for every affected step, with no time window. `CLINICAL-PLACEHOLDER: needs clinician sign-off`.
**Suggested: B**, since a step already closed on a wrong value is the case most likely to have caused harm.
**Questions:** How soon must someone look, in hours? Does the patient need to be told of a correction, and by whom?

## 34. What counts as an encounter, and how to tell minor and pregnancy records apart (retention, clinical content)

| Option for "encounter" | Trade-off |
|---|---|
| A | A confirmed booking whose slot has started (today). Easy to compute; a no-show counts as an encounter. |
| B | A booking marked attended by staff. Closer to the truth; needs staff to mark attendance reliably. |
| C | Any dated clinical entry (note, result, decision). Broadest; includes things that are not visits. |

**For minor and pregnancy records:** the database holds no fact the report could use, so the report does not assess them. Options: A) add a recorded date of birth and a recorded pregnancy flag to the record (a design decision that needs privacy review, since it adds clinical data); B) keep them out of automated reports and handle them by a person's review; C) treat every record as the longest period until a person says otherwise (safest for retention, longest to keep personal data).
**Safe default:** patients with no encounter are counted apart and never as past the period; minor and pregnancy records are not assessed. `CLINICAL-PLACEHOLDER: needs clinician sign-off`.
**Suggested:** encounter option A for now and minor/pregnancy option B, with the periods themselves left to counsel and the clinical lead (see `docs/approval-pack/clinic-proposed-values.md`, retention).
**Questions:** Does the clinic mark attendance today? Who knows a patient is a minor or pregnant, and where is that written?

---

## Signature (left blank)

| Items answered | Name | Role | Date | Signature |
|---|---|---|---|---|
| | | | | |
