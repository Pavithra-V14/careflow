# Phase 8: in-app voice channel (flagged), mechanism level, 2026-11-06

BUILD-SPEC.md 23, 11.6, 5.2, 12.1; CLAUDE.md rules 2 and 11. Code: `careflow/voice.py`, prompts `prompts/voice_*.v1.*.txt`. **No audio, no WebSocket and no Gemini call exists.** The engine is a port with a scripted fake in the tests; the real adapter is a new external service (rule 13) and `synthetic_only` at launch.

## What is built

- **The mic is shown only when all of it holds** (`mic_state`): the clinic flag `FEATURE_VOICE_CHANNEL`, the patient's own `voice_enabled`, an available engine route, and a rule set that can assess. Each is tested to hide the mic on its own, and unknown (None) hides it. This is the "patient-level opt-in gates the mic independently of the clinic flag" confirmation.
- **First line, scope**: one static system prompt per workflow (`slot_offer`, `intake_question`, `readiness_item`), each restricting the session to reading one governed text and listening. The text read and the sentence said when the patient strays are the clinic's own governed templates, passed in by the caller. Nothing in code words anything to a patient. Prompts pass the CI scan (no placeholder in the system prompt; three allowlisted user variables). A second check classifies each finished turn the rules passed clean (`decision` tier) as in scope or not; an out-of-scope turn gets the governed decline text, the session ends and front desk follows up in text.
- **Second line, the rules on the live transcript**: every transcribed chunk goes through the same `StreamEvaluator` as other channels, first, before the scope classifier. On a match: the fixed pre-approved safety template is forced into the session (never composed), the immediate nurse task is raised through `RedFlagEngine.escalate_red_flag` (source = the session), the session ends. A phrase split across chunks is still caught.
- **The gap is recorded, not assumed**: `run_session` records `gap_ms` (chunk finished to interrupt landed) for every flag; `summarise_gaps` reports NOT MEASURED with no samples. The fake's numbers are not a figure.
- **Consent before audio** (23.4): a per-session consent is recorded before the engine opens; without it nothing is opened.
- **Unknown is not clear**: a rule set that cannot assess refuses to open a session at all.
- **Decorative** (23.5): an engine that fails to open or dies mid-session ends the session in a closed word; chunks already received were screened. The nurse task is raised even if no safety template is approved or the engine cannot speak, and the result then says the response was not spoken, which counts as a miss in the gate.
- **The hard gate is wired**: `evalmetrics.voice_safety_escalation_rate` over voice gold cases (spoken response AND nurse task, unknown is a miss) feeds the `voice_safety_escalation_rate` gate through `evidence_for_gates`. No voice case means NOT MEASURED.

## Needs a person, a decision or a new service

1. **The real engine** (Gemini Live over a browser WebSocket) and its data-processing agreement for `clinical`. Until it exists the interrupt-latency figure is **not measured**; Appendix B requires measuring it with the real engine before voice ships past this phase.
2. **The voice gold set**: scripted red-flag utterances authored and reviewed by a clinician (the gate needs them).
3. **The governed texts**: the safety response (pre-approved, pre-translated), the decline sentence and the handoff-to-text wording are clinician/owner content. `max_turns` is a required value with no default.
4. **Storage and routes**: the patient `voice_enabled` preference (off by default, profile menu), the transcript sink (30-day body retention) and a route to open a session need migrations, a reviewer pass and role-matrix cases.
5. **Shadow price** for the audio-token unit: a real per-minute figure recorded at build (Appendix B).
