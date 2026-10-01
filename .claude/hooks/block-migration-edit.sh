#!/bin/bash
# PreToolUse hook on Edit. Enforces CLAUDE.md rule #10: never edit a merged migration.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# 1. Clean up Windows backslashes in the input path to forward slashes
FILE_PATH="${FILE_PATH//\\//}"

# 2. Dynamically find the repository root directory, no matter where Node spawned the script
REPO_ROOT=$(git rev-parse --show-toplevel)

# Only act on files under db/migrations
case "$FILE_PATH" in
  *db/migrations/*.sql) ;;
  *) exit 0 ;;
esac

# 3. Run git log relative to the repository root directory dynamically
if git -C "$REPO_ROOT" log --oneline -- "$FILE_PATH" 2>/dev/null | grep -q .; then
  jq -n --arg reason "This migration is already committed. Migrations are append-only — fix forward with a new migration file instead of editing this one. See BUILD-SPEC.md §17.1 and CLAUDE.md rule #10. Use the /new-migration skill." \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: $reason}}'
else
  exit 0
fi
