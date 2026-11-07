# Runbook: a model or an external provider is down

BUILD-SPEC.md 2, 5.2 and 10.4; `careflow/readiness.py` (`GET /readyz`); the ceilings in `careflow/spend.py` and `careflow/budget.py`. **Status: drafted for the owner's review; nothing here is a clinical decision.**

**What degrades, by design (never silently):**
- **Red-flag rules do not use a model.** They keep running, first, on every path. An outage does NOT weaken them.
- **Letter reading (vision/extraction)**: a letter that cannot be transcribed is never treated as clear. It is held for a person (`hold_for_human`, a nurse review task), as is any letter the grounding checks cannot confirm.
- **Triage proposal and reply interpretation (decision tier)**: the staff screen shows "no suggestion: needs a person". Nothing is auto-approved and nothing is auto-sent.
- **Translation**: the message is not sent in that language; a person decides.
- **`clinical` profile**: `GET /readyz` answers `{"ready": false}` when a required role has no enabled, approved route (so the platform stops sending traffic there). `demo` stays ready because it handles synthetic data only.

**Who:** engineering on call. **Safe to repeat:** yes; nothing below changes patient data.

1. **Confirm it is the provider**, not us: `GET /readyz`, the worker log (error CLASS only), the provider's status page. Do not paste request or response bodies anywhere.
2. **Expect and watch the holds.** Held referrals and "needs a person" screens are the correct behaviour; the nurse queue grows. Tell the clinical lead that suggestions are paused, in words, not numbers of patients.
3. **Do not switch to another provider on your own.** A provider that has not been approved for the data class (`phi_approved` in the registry) is never used for real patient text; moving a route is a registry change that needs the owner's sign-off.
4. **When it returns**, held items stay held until a person acts: they are not re-run automatically into approval. Re-submit through the product path.
5. **Spend ceiling reached** looks the same on screen ("no suggestion"); check the ceiling before assuming an outage.

**Never:** bypass the rules-first order; treat a failed or empty transcription as "no flags"; send real patient text to an unapproved provider to "get things moving".
