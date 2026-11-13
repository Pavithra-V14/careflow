# For the clinic owner

> **AI-prepared, not approved by the clinic owner.** Not clinical or legal advice. Never loaded by the application; never marked approved.

**Read (about 20 minutes):** `clinic-proposed-values.md`. It proposes, with a reason each: the break-glass reasons and limits, the grievance categories, the voice transcript retention, the other retention periods and the clinic time zone.

**Decide (about 20 to 30 minutes):**
- For each row tick **Approve** or write **Change to**. The questions in each reason are for you to answer, not for the assistant.
- Retention periods and break-glass review rules also need counsel and the clinical lead; approving a row here does not replace that.
- Which clinic is the public one (the page patients see before they sign in) is a separate decision: run `uv run python scripts/dev_clinic.py publish --clinic-id <id>` yourself; nobody else will.

**What happens after you approve:** you (not the software, not the assistant) set the values in the clinic's own settings; the break-glass limits are set with the owner-run script `scripts/seed_break_glass_reasons.py`. The demo pack holds only labelled placeholders and is never a source of these values.

**Total: about 45 minutes to 1 hour**, a rough estimate.
