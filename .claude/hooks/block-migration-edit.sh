#!/bin/bash
# PreToolUse hook on Edit. Enforces CLAUDE.md rule #10: never edit a merged migration.
# A migration already committed to git is treated as "applied" for this project-layer check —
# the real, authoritative check is the checksummed ledger the migration runner itself enforces
# against the live database. This hook is a fast, local backstop that catches the mistake before
# it's even committed.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Only act on files under db/migrations
case "$FILE_PATH" in
  */db/migrations/*.sql) ;;
  *) exit 0 ;;
esac

# If the file has at least one commit already, it's been merged — block the edit.
if git -C "$(dirname "$FILE_PATH")" log --oneline -- "$FILE_PATH" 2>/dev/null | grep -q .; then
  jq -n --arg reason "This migration is already committed. Migrations are append-only — fix forward with a new migration file instead of editing this one. See BUILD-SPEC.md §17.1 and CLAUDE.md rule #10. Use the /new-migration skill." \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
else
  exit 0
fi
