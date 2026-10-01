#!/bin/bash
# PostToolUse hook on Write|Edit. Heuristic backstop for CLAUDE.md rule #1: never invent clinical

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# 1. Clean up Windows backslashes in the input path to forward slashes
FILE_PATH="${FILE_PATH//\\//}"

# 2. Filter target directories relatively
case "$FILE_PATH" in
  *kb/cards*|*kb/red_flag_rules*|*card*.json|*card*.py|*red_flag*.py) ;;
  *) exit 0 ;;
esac

# Give Node.js a tiny fraction of a second to flush the file to the disk pool
sleep 0.1

# 3. Check relative file presence directly
[ -f "$FILE_PATH" ] || exit 0

if grep -qi 'CLINICAL-PLACEHOLDER' "$FILE_PATH"; then
  exit 0
fi

# Loose pattern: a number next to a clinical-sounding token, anywhere in the file.
if grep -Eiq '\b(ef|sbp|dbp|bpm|dose|mg|threshold|spo2|hr)\b[^.]{0,15}[0-9]' "$FILE_PATH"; then
  jq -n --arg msg "Warning: $FILE_PATH contains what looks like a clinical number (threshold, dose, or vital) with no CLINICAL-PLACEHOLDER marker and no visible approver. This is a heuristic check, not a verdict — if the value is genuinely clinician-approved, ignore this. If it was invented, mark it '# CLINICAL-PLACEHOLDER: needs clinician sign-off' or invoke the clinical-content-reviewer subagent before this ships." \
    '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $msg}}'
else
  exit 0
fi
