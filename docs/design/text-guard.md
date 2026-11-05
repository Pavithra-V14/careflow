# Text guard: injection scan and PII mask (heuristics)

`careflow/text_guard.py`, plain code, no model, no service, no new dependency. Decided 2026-11-04 (review finding: no instruction-injection classifier and no PII masking on the Front Door path; BUILD-SPEC 11.1, 11.3, hard gate 14.3).

**What it does.** In the Front Door graph, after the deterministic red-flag rules and before any model reads the letter, `scan_text` looks for instruction-injection shapes (an instruction to ignore rules, text addressed to the system, role markers, steering of the priority, an instruction not to escalate, hidden or bidirectional characters, long encoded blobs, markup, tool-call shapes). A hit, an empty or non-text input, or an oversized letter is `held_suspect_text`: a nurse review task with closed reason words, no model called. The retriever (which embeds the letter with a model) receives `mask_pii(text)`: phone and identifier-like number runs, e-mail addresses, full dates, a birth year after a birth label, and labelled or titled names replaced by fixed tokens. The extractor still reads the original, because it must read the identity.

**It can only add a hold.** It never clears a red flag, lowers a priority, or turns a hold into a pass; the rules run first and independently. When unsure it holds.

**Honest limits.** These are word-and-shape heuristics, English only (Tamil and transliterated text are not covered), written without clinical judgement and tested on INVENTED text. They miss attacks in other words and sometimes hold an innocent letter. The masking misses an unlabelled name in running prose. They do NOT close the 14.3 hard gate "no injected text changes a priority or extracted value undetected": that needs a clinician-authored adversarial gold set and its measured result (pre-launch checklist item 41 stays OPEN). They also do not make it safe to send patient text to a hosted embedding, reranker or hedge route; that still needs an approved route (CLAUDE.md rule 7).

**Not here:** the extraction step is not masked (it must read the identity), and nothing masks the letter shown to staff.
