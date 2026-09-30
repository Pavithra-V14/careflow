# CareFlow `.claude/` scaffold

Drop this folder in the repo root, next to `BUILD-SPEC.md`, `CLAUDE.md`, and
`metrics-catalog.yaml`, and commit it.

## Requirement: `jq`

Both hooks parse JSON with `jq`. Confirmed present on Debian/Ubuntu-based systems via
`apt-get install jq`; if your build machine doesn't have it, install it before starting a session,
or the hooks will error out on every `Edit`/`Write` call (harmlessly — Claude Code treats a hook
that can't run as non-blocking — but silently, which defeats the point).

## What's in here — and why nothing is called "commands"

As of the current docs (`code.claude.com/docs/en/commands`): *"To add your own commands, see skills."* There is no separate commands primitive to author anymore — a skill with `disable-model-invocation: true` **is** a custom command, invoked as `/name`, costing zero context until you type it. The three below are exactly that.

| Path | Kind | Does |
|---|---|---|
| `skills/clinical-guard/` | reference skill, loads by relevance | Keeps the no-fabrication and rule-ordering rules in context when touching clinical files |
| `skills/new-migration/` | command, `/new-migration`, zero idle cost | Walks through creating a migration correctly |
| `skills/run-gates/` | command, `/run-gates`, zero idle cost | Runs the real evaluation gates from BUILD-SPEC.md §14.3, plus the built-in `/security-review` |
| `skills/phase-status/` | command, `/phase-status`, zero idle cost | Reports which Appendix D phase you're in |
| `agents/clinical-content-reviewer.md` | subagent | Judgment call on card/rule changes — invoke before merging any |
| `agents/rls-security-reviewer.md` | subagent | Judgment call on migrations and auth changes |
| `agents/spec-compliance-checker.md` | subagent | Cross-checks a finished feature against the spec and Appendix B |
| `hooks/block-migration-edit.sh` | `PreToolUse` hook | Mechanically blocks editing a committed migration |
| `hooks/check-clinical-placeholder.sh` | `PostToolUse` hook | Heuristic warning on an unmarked clinical number |
| `settings.json` | wiring | Registers both hooks and a session-start reminder |

## Two built-in facilities this scaffold deliberately does not duplicate

- **`/run-skill-generator`, then `/run` and `/verify`.** BUILD-SPEC.md §19.1 requires every screen to be checked in a real, running browser, not just passed through a type-checker. Run `/run-skill-generator` early (Phase 0 or 1) to teach Claude Code how to launch this specific app from a clean environment; after that, `/verify` actually drives it. This satisfies the requirement with a built-in Claude Code facility — nothing needed to build here.
- **`/security-review` and `/code-review`.** Both are bundled, free, and already installed — don't build a general-purpose review agent to duplicate them. `run-gates` now calls `/security-review` directly rather than leaving it as a separate step someone has to remember.

## Context-window discipline

`/skill-doctor` shows what each skill actually costs in context and how often it's used; `/doctor` finds unused skills, MCP servers, and plugins against their context cost. Run both occasionally as this scaffold grows — a skill nobody invokes is pure token tax on every turn, and the fix (turning it off, or adding `disable-model-invocation: true`) is one setting away.

## Tested, not just written

Both hooks were run against real fixtures (a git repo with a committed and an uncommitted
migration; a card file with and without the placeholder marker) before being included here — see
the commit history of this scaffold's source conversation if you want the exact test transcript.
The migration hook correctly denies editing a committed file and stays silent on a new one; the
placeholder hook correctly warns on an unmarked number and stays silent once the marker is present.
The `SessionStart` hook's exact shell command was executed directly, not just read, to confirm the
quoting doesn't break it.

## What this scaffold deliberately does *not* try to enforce mechanically

The idempotency-key rule, the row-level-security-forced rule, and the no-side-effect-across-a-pause
rule all need to understand a change's *intent*, not just pattern-match its text. Those are the
`rls-security-reviewer` and `spec-compliance-checker` subagents' job — invoke them explicitly
("use the rls-security-reviewer subagent on this migration") rather than expecting a hook to catch
them, because a hook that pretends to enforce a judgment call teaches false confidence.

## Packaging as a plugin (later, if you need it)

This is project-scoped by design — one clinic, one repo. If you later want the same skills, agents
and hooks reusable across other repos, add a `.claude-plugin/plugin.json` manifest to this same
folder structure; nothing else needs to move.
