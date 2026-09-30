---
name: phase-status
description: Report which build phase this project is in and what that phase's acceptance criteria are, from BUILD-SPEC.md Appendix D. Use when asked "what's next" or "are we done with this phase".
disable-model-invocation: true
---

Read Appendix D of BUILD-SPEC.md. Based on what exists in the repo so far (check the actual
directory structure and test suite, don't assume), report:

1. Which phase (0–10) the project is currently in.
2. That phase's acceptance criteria, quoted from Appendix D, with each one marked done / not done
   based on real evidence (a passing test, a working migration, an existing file) — not intention.
3. The single next concrete task to finish the current phase, before starting the next one.

Do not recommend starting a later phase's work while the current phase has unmet criteria, per
CLAUDE.md's instruction to build in the given phase order.
