---
description: Reminds Claude of the non-negotiable clinical-content and safety-ordering rules before touching anything in kb/, migrations, agent graph code, or a voice_live session. Use whenever writing or editing a protocol card, a red-flag rule, a threshold, an intake question, a readiness checklist item, an agent decision path, or a voice session's system instructions.
---

Three rules from CLAUDE.md apply to this change, restated so they can't be missed:

1. Never invent a clinical threshold, dose, red-flag phrase, or intake/readiness question. If one
   is needed and none was supplied, write `# CLINICAL-PLACEHOLDER: needs clinician sign-off` and
   stop there — do not guess a plausible-sounding value. This applies to a card's `intake_question`
   and a visit type's `readiness_checklist` exactly as it applies to a threshold (BUILD-SPEC.md
   §22.2, §22.3): the fact-gap computation that decides *whether* to ask is code; the question's
   *wording* is always clinician-authored.
2. If this code is part of a referral, message, or care-plan path: the deterministic red-flag
   rules must run before any model call, on every path, with no exception. A model's opinion can
   only escalate a case the rules already passed clean — never de-escalate one they flagged.
3. **If this touches a `voice_live` session (BUILD-SPEC.md §11.6, §23): the ordering guarantee in
   rule 2 does not hold the same way.** A native speech-to-speech model has the whole exchange
   itself rather than pausing before every sentence. The guardrail here is two-layered instead:
   tight scope in the session's own system instructions as the first line, the deterministic rules
   watching the live transcript in parallel — able to interrupt, not able to pre-empt — as the
   second. Never write a `voice_live` system prompt broader than the one task it's scoped to
   (a booking reply, one specific fact-gap question, one readiness item); the narrow scope is the
   safety control, not a limitation to relax for a better user experience.

If this change touches `kb/cards` or `kb/red_flag_rules` (or their migrations), it is not usable
in the `clinical` profile until a named clinical lead approves it under the two-person rule
(BUILD-SPEC.md §12.5, §20.2). Mark it `draft_unreviewed` and say so.
