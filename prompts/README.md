# Prompt templates (BUILD-SPEC.md 6.2, 17.4)

Prompts live here as files, with a version and a content hash, and a prompt change is a pull request that must pass the evaluation gates.
**There are no prompt templates yet**: no model service is wired. This directory exists so the scan is wired before the first prompt.

`tests/test_prompt_scan.py` runs `careflow/prompt_scan.py` over this directory on every build. The rules:

- `NAME.system.txt` is a system prompt. It must be byte-identical on every call: **no placeholder of any syntax, no timestamp or date, no
  value shaped like a phone number, e-mail address, national id or date of birth.**
- `NAME.user.txt` is a user-turn template. It may use only variables listed in `variables.allowlist`, never a variable whose name looks like
  identifying data (name, date of birth, phone, e-mail, address, ids), and no timestamp or PHI-shaped literal.
- Any other file name here (besides this README, `variables.allowlist` and `.gitkeep`) is itself a failure.

A failure names the file, the line and a short code, and never prints the offending text.
