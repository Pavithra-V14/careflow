# Runbook: a red-flag task is overdue

BUILD-SPEC.md 12.1, 13.3, 13.4; `careflow/watchdog.py`; `careflow/review_console.py` (escalation chain). **Status: drafted for the owner and the clinical lead to review. Nothing here is a clinical decision, and no number below is a deadline.** The deadline and the escalation chain are required settings (`CAREFLOW_REVIEW_DEADLINE_MINUTES`, `CAREFLOW_WATCHDOG_LIMITS`) chosen by the clinical lead.

**What fires:** the in-app watchdog raises `red_flag_overdue` (audience: nurse) when a red-flag task is past its deadline, and again at each escalation level up the chain. It depends on no third party. A red-flag task is raised automatically the moment any channel (text, voice, a letter) matches a rule; it never waits for a human first. This runbook is about the human step that has not happened.

**Who:** the clinical lead (or the next role up the configured chain). Engineering supports; engineering never closes a red-flag task.

1. **Open the task, not the patient record.** The alert carries the class, a count and an opaque id only. Sign in and open the task from the review queue. Do not forward the alert text anywhere: it is deliberately contentless.
2. **Find out why it is unclaimed.** No nurse on shift? A role mismatch (the task is for `nurse`; an account with another role cannot see it)? The banner not seen? Fix the cause, then have someone with the role claim it.
3. **A person acts.** The fixed safety response has already gone to the patient automatically. The nurse contacts the patient according to the clinic's own protocol (clinician-owned; it is not in this repository).
4. **Never** close the task from the database, mark it handled to silence the alert, or change the deadline setting to make the alert stop.
5. **Voice sessions:** a red flag spoken in a voice session raises the same task. If `voice_safety_missed` fires (a flag that did not produce the spoken response and the task), treat it as the highest priority: it leaves no text trail.
6. **After:** record in the task what was done (a closed reason code). If the cause was staffing or routing, tell the owner; the chain and the deadline are theirs to change.

**Check that alerting itself works:** `delivery_failed` means a channel (e-mail, chat) is dead; the in-app banner still shows the alert. A drill is to raise a synthetic red-flag task in the demo environment and confirm the nurse banner appears.
