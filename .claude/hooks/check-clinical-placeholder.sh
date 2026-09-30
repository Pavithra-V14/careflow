#!/bin/bash
# PostToolUse hook on Write|Edit. Heuristic backstop for CLAUDE.md rule #1: never invent clinical
# content. This is deliberately loose — it catches an obvious miss (a bare number next to a
# clinical-sounding word, with no placeholder marker anywhere in the file) and hands the real
# judgment call to the clinical-content-reviewer subagent. It never blocks; it only warns, because
# a false positive here would train Claude (and you) to ignore it.

INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

case "$FILE_PATH" in
  *kb/cards*|*kb/red_flag_rules*|*card*.json|*card*.py|*red_flag*.py) ;;
  *) exit 0 ;;
esac

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
