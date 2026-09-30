# CareFlow — root rules

Read `BUILD-SPEC.md` in full before writing code. This file is the short, load-bearing summary you should re-check yourself against on every change — if something you're about to do conflicts with a rule below, stop and re-read the relevant section of `BUILD-SPEC.md` rather than guessing.

## Non-negotiable rules

1. **Never invent clinical content.** No threshold, dose, red-flag phrase, or protocol rule may be written from your own judgment. Insert `# CLINICAL-PLACEHOLDER: needs clinician sign-off` and stop. (`BUILD-SPEC.md` §20.2)
2. **Red-flag deterministic rules run before any model call, on every path, with no exception.** A model's safety opinion can only escalate a case that passed the rules clean — it can never de-escalate one the rules already flagged. (§5.2)
3. **Unknown is not a value.** A missing or unverified fact satisfies no rule. It is never treated as true, never treated as false, and never silently closes a loop. (§9.5, §11.2, §14.3)
4. **A node that pauses must never also cause a side effect.** Side effects go in the step immediately before an interrupt/wait. This is enforced by a structural test — do not merge a graph change that fails it. (§7.2)
5. **Every write carries an idempotency key.** A missing one on any write path is a release blocker, not a nitpick. (§7.2, §16.1)
6. **No agent-owned memory of patients.** Everything durable is clinical record, operational state, or versioned knowledge — never a free-text or vector store an agent maintains about a person on its own. (§9.1)
7. **Patient data never leaves the local trust boundary except through a listed, approved external call.** Check `BUILD-SPEC.md` §2 (operating profiles) and §17.6 (data-flow register) before adding any new external API call that could see patient text.
8. **Row-level security, not application code, enforces clinic isolation.** Every table holding clinic-scoped data has RLS from the migration that creates it, and the CI role-matrix test (§18.4, §21) must pass against the real restricted database role — not a superuser. **Every new route, in every phase, adds its own case to the role-matrix test in the same change that adds the route.** Do not build the route first and defer the test case to later — a route with no role-matrix coverage is not done, regardless of what phase it's in.
9. **Auto-approved outputs never become training labels.** Only human decisions do. (§12.6)
10. **Never edit a merged migration.** Fix forward with a new one. The migration ledger is checksummed specifically to catch this.
11. **A voice session is not a lower-scrutiny channel than a text message — it's a higher-urgency one.** The deterministic red-flag rules run against a live voice transcript in parallel, in real time, exactly as they run on a referral letter or a portal message — see `BUILD-SPEC.md` §11.6 for the specific mechanism a native speech-to-speech API needs (it isn't identical to the text-channel check, and that difference is written out explicitly there, not glossed over). Never let scope creep turn a narrowly-instructed voice session into an open-ended one to be more helpful — the narrow scope is itself part of the safety design, not a limitation to work around.
12. **No Docker anywhere in this project.** Development, CI, and production all use hosted infrastructure — a hosted Supabase project for the database and auth (dev and CI use separate projects), and the model-serving stack in §17.7 runs on real machines, not containers. If a task seems to need Docker, that's a signal the task was scoped wrong, not a signal to add Docker.
13. **Before adding a dependency on any cloud API or external service not already named in `BUILD-SPEC.md`, ask first.** Do not silently introduce a new external dependency to solve a problem, even a well-reasoned one — the person building this wants to approve every service that touches the project, the same way every clinical threshold needs a named approver.

## Operating profiles

Every environment is `demo` or `clinical` (§2). Build every feature to degrade explicitly and visibly when running `clinical` without an approved external route — never silently, never by crashing. When in doubt about which profile a piece of code should assume, default to writing it so it works correctly in both.

## Where things live

- `BUILD-SPEC.md` — the full specification, organized by layer. This is the source of truth for every design decision, including *why*.
- `metrics-catalog.yaml` — every metric's id, definition, source query, cadence, target, alert, and owner, defined exactly once. The Live/Test dashboard and Grafana alerts are both generated from this file, not hand-duplicated.
- Appendix A of `BUILD-SPEC.md` — options that were deliberately rejected, with reasons. Do not silently reintroduce one of these; if you think a rejected option was actually right, say so and ask, don't just build it.
- Appendix B of `BUILD-SPEC.md` — facts that were researched but not hands-on verified. Treat each as a required spike before you depend on it.
- Appendix D of `BUILD-SPEC.md` — the phase order. Build in this order; later phases assume earlier ones' gates already pass.

## When the spec doesn't cover something

If you hit a decision point `BUILD-SPEC.md` doesn't address:
- If it could affect patient safety, privacy, or a clinical judgment: stop and ask. Do not pick a reasonable-sounding default.
- If it's a pure implementation detail with no safety/clinical/privacy surface (a variable name, a file layout choice consistent with the stack in §3): use your judgment, and note the choice briefly in a commit message or comment so it's easy to find later.

## Verification discipline

- Always use the Context7 MCP when you need library or framework API documentation, current setup steps, or code generation guidance for anything in the stack (§3) — FastAPI, SQLAlchemy, Pydantic v2, LangGraph, Qdrant, Supabase Auth, React — without waiting to be asked. Training data goes stale; a version-specific docs lookup doesn't. This does not cover live pricing, rate limits, or news — for those, an item belongs in Appendix B, not a Context7 query.
- Every claim in this codebase about an external service's behavior that came from Appendix B must be confirmed by an actual call against that service (or an explicit, documented mock) before code depends on it in a non-test path.
- Every gate in `BUILD-SPEC.md` §14 (hard gates, threshold gates, pre-deployment checklist) must be a real, running check — not a comment saying it should exist. "We'll add the test later" is not an acceptable state to leave a gate in.
- When you fix a bug, add the regression test that would have caught it before considering the fix done. `BUILD-SPEC.md` §21 describes the test pyramid this belongs in.
